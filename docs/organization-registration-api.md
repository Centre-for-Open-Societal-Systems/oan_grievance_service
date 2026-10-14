# Organization Registration and Verification API: frontend integration guide

For the NGO and FPO registration screen (`/ngo`, `/fpo`) and the admin verification queue. An NGO, FPO or cooperative registers once, an administrator verifies it, and only then may its representative file grievances.

The product flow is in [organization_onboarding_flow.md](organization_onboarding_flow.md). This page is the contract that is built.

## 1. Concepts

- An **organization** is a registered NGO, FPO or Cooperative. Its **id** is `name`, for example `ORG-00012`. Use it in admin URLs.
- The **authorized representative** is the login that registered it (`representative_user`). There is one representative per organization and one organization per login. Only that login may file for the organization.
- **org_type** is `NGO`, `FPO` or `Cooperative`, the same names as the submitter types. The FPO screen sends `FPO`. `Cooperative` is accepted for callers that want to register one separately.
- **entity_type** is the legal form, chosen from a list that depends on `org_type`:

| `org_type`              | `entity_type` values                                                                         |
| :---------------------- | :------------------------------------------------------------------------------------------- |
| `NGO`                   | Local NGO, International NGO, Community-Based Organisation, Civil Society Organisation       |
| `FPO` and `Cooperative` | Farmer Producer Organisation, Primary Cooperative, Cooperative Union, Cooperative Federation |

- **registration_number** is the registration or licence number. It is unique across organizations, stored in upper case, and 3 to 60 letters and digits with single `-` or `/` between groups. The registration screen sends it as `tax_registration_number`, which is accepted as an alias. Responses use `registration_number`.
- **status** is the verification status. See section 3.
- The representative's **Submitter Profile** is converted to the organization's type, name and contact details when the organization is registered, so `GET /auth/me` then reports the profile type as `NGO`, `FPO` or `Cooperative`. It stays blocked until the organization is Active.

## 2. Conventions

| Item         | Value                                                                      |
| :----------- | :------------------------------------------------------------------------- |
| Base URL     | `{base_url}/api/v1`                                                        |
| Auth         | `Authorization: Bearer <access_token>` from `POST /api/v1/auth/login`      |
| Content type | `Content-Type: application/json` for `POST` and `PATCH`                    |
| Success body | `data` is the organization record itself, so `data.status` is the status   |
| List body    | `data.organizations[]` plus `data.pagination` and a top-level `pagination` |

Error envelope and codes are the same as the other APIs.

| HTTP | `code`              | When                                                                                       |
| :--- | :------------------ | :----------------------------------------------------------------------------------------- |
| 400  | `VALIDATION_ERROR`  | A bad or unknown field, or a rule broken. `details` maps field name to message when it can |
| 403  | `PERMISSION_DENIED` | The role may not do this, or the organization is Suspended                                 |
| 404  | `NOT_FOUND`         | No organization for the caller (`/orgs/me`), or no organization with that id               |
| 409  | `DUPLICATE_ENTRY`   | The caller already has an organization, or the registration number is taken                |

Field errors for shape (missing, too long, bad email, bad postal code, bad website, bad registration number, unknown field) arrive in `details` keyed by the request field name. Rules that need the database or the organization type (phone number, entity type for the organization type) arrive as a `message` with empty `details`. Show those on the form.

## 3. Verification status

| Status      | Meaning                                                                        | Representative can file |
| :---------- | :----------------------------------------------------------------------------- | :---------------------- |
| `In Review` | Registered, waiting for an administrator                                       | No                      |
| `Active`    | Verified                                                                       | Yes                     |
| `Suspended` | Rejected or withdrawn. `status_reason` says why. The organization is read-only | No                      |

Allowed moves:

| From        | To          | Who                       | Notes                                                                        |
| :---------- | :---------- | :------------------------ | :--------------------------------------------------------------------------- |
| (new)       | `In Review` | Representative            | Registration. The status cannot be chosen                                    |
| `In Review` | `Active`    | Administrator             | Verifies it                                                                  |
| `In Review` | `Suspended` | Administrator             | Rejects it. Reason required                                                  |
| `Active`    | `Suspended` | Administrator             | Withdraws it. Reason required                                                |
| `Suspended` | `Active`    | Administrator             | Reinstates it                                                                |
| `Suspended` | `In Review` | Administrator             | Reopens the review                                                           |
| `Active`    | `In Review` | System, on a profile edit | The representative changed `legal_name`, `org_type` or `registration_number` |

Any other move is a 400. Setting the status it already has is a no-op. Every decision records `verified_by`, `verified_on` and `status_reason`, and the document keeps a version history.

The mirrored block on the representative's profile lifts only when the profile's block reason starts with `Organization`. A block an officer placed for another reason, such as abuse, survives verification.

## 4. Representative endpoints

Role: Grievance Submitter. Officers, reviewers and admins get 403.

### `POST /orgs`: register

```json
{
  "legal_name": "Selam Farmers Cooperative Union",
  "brand_name": "Selam",
  "org_type": "FPO",
  "entity_type": "Cooperative Union",
  "registration_number": "REG-2024-0042",
  "representative_name": "Almaz Bekele",
  "contact_email": "office@selam-coop.example.com",
  "contact_phone": "+251911234567",
  "website": "https://selam-coop.example.com",
  "street_address": "Bole Road 12",
  "kebele": "03",
  "woreda": "Bishoftu",
  "zone": "East Shewa",
  "region": "Oromia",
  "country": "Ethiopia",
  "postal_code": "1000"
}
```

Required: `legal_name`, `org_type`, `entity_type`, `registration_number`, `contact_email`, `contact_phone`, `street_address`, `zone`, `region`, `country`, `postal_code`. Optional: `brand_name`, `representative_name` (defaults to the account's name), `website`, `kebele`, `woreda`. `contact_phone` is any valid number in the service's jurisdiction and is stored as E.164. `website` needs `http://` or `https://`. Any other field, including `status`, is a 400.

Returns the organization with `status: "In Review"`.

### `GET /orgs/me`

The caller's organization, or 404 until one is registered. The screen shows the registration form on 404 and the status card otherwise.

### `PATCH /orgs/me`

Send only the fields to change. `brand_name`, `representative_name`, `website`, `kebele` and `woreda` accept `null` to clear. `status`, `representative_user` and the verification fields are not accepted.

- A `Suspended` organization is read-only: 403.
- Changing `legal_name`, `org_type` or `registration_number` on an `Active` organization sets it back to `In Review` with a `status_reason` naming what changed, and the profile is blocked again. Tell the user before they save. Contact and address edits do not change the status.
- An empty body is a 400.

## 5. Administrator endpoints

Role: Grievance Admin, System Manager or Administrator. Grievance Review Officer may call the two `GET` endpoints only.

### `GET /orgs`: the verification queue

| Query       | Value                                                                       |
| :---------- | :-------------------------------------------------------------------------- |
| `status`    | `In Review`, `Active` or `Suspended`. Use `In Review` for the pending queue |
| `org_type`  | `NGO`, `FPO` or `Cooperative`                                               |
| `search`    | Matches legal name, brand name and registration number                      |
| `sort`      | `newest` (default) or `oldest`. Use `oldest` to work the queue in order     |
| `page`      | 1-indexed. Default 1                                                        |
| `page_size` | 1 to 100. Default 20                                                        |

Blank filters mean "no filter". An unknown filter or value is a 400.

### `GET /orgs/{org_id}`

One organization with the representative and contact details, for the review screen.

### `PATCH /orgs/{org_id}/status`

```json
{ "status": "Active" }
```

```json
{ "status": "Suspended", "reason": "Registration certificate could not be confirmed" }
```

`reason` is required for `Suspended` and optional otherwise. The response is the updated organization. Activating unblocks the representative's profile at once, and suspending blocks it at once.

## 6. Effect on filing grievances

The representative files through the same endpoints as any submitter. The server decides:

- A profile linked to an organization may file only while the organization is `Active`, and only for its representative. Otherwise the request is a 403 `PERMISSION_DENIED`, "This organization is not verified" or "You are not the authorized representative".
- Staff filing on a caller's behalf are held to the organization's status too.
- A suspended organization's representative can still open and answer the cases already filed.
- `POST /submitters/{id}/unblock` refuses a profile whose organization is not `Active`. Activate the organization instead.

## 7. Record

| Field                                                  | Notes                                                    |
| :----------------------------------------------------- | :------------------------------------------------------- |
| `name`                                                 | Organization id                                          |
| `legal_name`, `brand_name`                             |                                                          |
| `org_type`, `entity_type`                              | See section 1                                            |
| `registration_number`                                  | Upper case                                               |
| `representative_user`, `representative_name`           | The authorized representative                            |
| `contact_email`, `contact_phone`, `website`            | Phone in E.164                                           |
| `street_address`, `kebele`, `woreda`, `zone`, `region` |                                                          |
| `country`, `postal_code`                               |                                                          |
| `status`                                               | `In Review`, `Active` or `Suspended`                     |
| `status_reason`                                        | Why it is not Active, or the last decision's note        |
| `verified_by`, `verified_on`                           | The last administrator decision. Null until there is one |
| `creation`, `modified`                                 | ISO timestamps                                           |

## 8. What `GET /auth/me` returns for a representative

`data.profiles.grievance` is the representative's submitter profile. The profile only holds a filing-time copy of the organization's name and contact details, so for a profile linked to an organization it also carries the organization itself. The Submit Grievance identity step reads it to pre-fill the NGO, FPO and cooperative forms.

| Key                                              | Value                                                   |
| :----------------------------------------------- | :------------------------------------------------------ |
| `type`                                           | `NGO`, `FPO` or `Cooperative`                           |
| `submitter_name`                                 | The organization's legal name                           |
| `registration_number`                            | The organization's registration number                  |
| `representative_name`                            | The authorized representative's name                    |
| `contact_mobile`, `country_code`, `phone_number` | The organization's contact phone                        |
| `contact_email`                                  | The organization's contact email                        |
| `identities`                                     | `[{"scheme": "org", "value": "<registration number>"}]` |
| `organization.id`, `legal_name`, `brand_name`    | The organization                                        |
| `organization.org_type`, `entity_type`           | As in section 1                                         |
| `organization.registration_number`               | Same as above                                           |
| `organization.representative_name`               | Same as above                                           |
| `organization.status`, `status_reason`           | Verification status and the last decision's note        |
| `organization.can_file`                          | `true` only while the status is `Active`                |

The keys `registration_number`, `representative_name` and `organization` are absent for a profile with no organization, so farmers and development agents see the same response as before. NGOs, FPOs and cooperatives have no Fayda ID, so none is returned: `identities` holds only the `org` entry, even for a representative whose account had a Fayda-keyed personal profile before the organization was registered.

## 9. Sign-up, first sign-in and completing the registration

An NGO, FPO or cooperative representative signs up like anyone else, then completes the organization form at first sign-in.

| Step | Call                      | What happens                                                                                                                                            |
| :--- | :------------------------ | :------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1    | `POST /auth/register`     | Creates the account and a **blocked, typed profile**. No organization exists yet                                                                        |
| 2    | `POST /auth/login`        | Signs in. Nothing organization-specific                                                                                                                 |
| 3    | `GET /auth/me`            | `profiles.grievance.type` is `NGO`, `FPO` or `Cooperative` and there is no `profiles.grievance.organization`. Send the user to the organization form    |
| 4    | `GET /orgs/me`            | 404 until the form is submitted. Use it to choose between the form and the status card                                                                  |
| 5    | `POST /orgs`              | Registers the organization `In Review` and converts the profile (section 1). From now on `GET /orgs/me` and `profiles.grievance.organization` return it |
| 6    | `PATCH /orgs/{id}/status` | An administrator activates it. Only then can the representative file                                                                                    |

Sign-up payload for an organization. The same fields as any sign-up, with `submitter_type` set and no `fayda_id`:

```json
{
  "full_name": "Almaz Bekele",
  "email": "almaz@example.com",
  "password": "<password>",
  "country_code": "+251",
  "phone_number": "911641538",
  "role": "Grievance Submitter",
  "submitter_type": "NGO"
}
```

- `submitter_type` is `NGO`, `FPO` or `Cooperative`. An Individual Farmer still needs `fayda_id`.
- An organization has no Fayda ID. One sent with an organization sign-up is not stored, and `fayda_id` in `GET /auth/me` is `null` for these types.
- The profile is keyed by the phone number until `POST /orgs`, which re-keys it by registration number. The account's name stays the representative's: `representative_name` defaults to it.
- A sign-up that already carries a `registration_number` takes the earlier path (a profile keyed by that number, blocked until an administrator verifies it), but it creates no organization record. The user still completes `POST /orgs`, with the same registration number.
- `GET /orgs/me` is the endpoint that returns the signed-in representative's organization. It identifies them from the bearer token, so it takes no id.

## 10. Not available yet

- Logo upload, KYC document upload and the separate contacts endpoint from the onboarding flow. The registration screen collects contact details inline and does not use them.
- Notifying the representative when an administrator decides. They see the status when they next open the screen.
- More than one representative per organization.
