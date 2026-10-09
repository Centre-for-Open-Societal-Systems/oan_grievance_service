# Officer Management API: frontend integration guide

For the Nodal Officers (L1), Senior Officers (L2), Admin and Reviewer tabs in Administration, and the Add and Edit modals. These endpoints replace the hardcoded `initialOfficers` arrays and the dummy Admin data, and `GET /officers/status-counts` replaces the hardcoded Active, On Leave and Inactive counts.

Performance numbers (Assigned, Resolved, Avg Time, Resolution Rate) are **not** in this API. They come from the statistics API.

One resource serves three kinds of account, chosen by `role`: `Officer` (the default), `Admin` and `Reviewer`. Anything that does not send `role` behaves exactly as it did before roles existed. Sections 1 to 12 describe officers; section 13 covers what differs for Admin and Reviewer accounts, and section 14 the status counts.

## 1. Concepts

- An **officer** is a login (User) placed on one or more **desks**. A desk is one department and one service category.
- A **role** is `Officer`, `Admin` (Grievance Admin) or `Reviewer` (Grievance Review Officer). Every record carries it. An Admin or Reviewer is a login too, but it is not an officer: it is on no category desk and takes no part in routing, escalation or officer statistics (section 13).
- The officer **id** is the officer's email, lowercase. Use it in URLs and for `reports_to`.
- **level** is `L1` (Nodal Officer) or `L2` (Senior Nodal Officer). It can be changed later.
- **service_categories** are the categories the officer handles. Every category must already have a category assignment for the chosen department, otherwise the request fails.
- **reports_to** is an L2 officer id and is only valid for an L1. It is where that L1's cases escalate to.
- **status** is `Active`, `On Leave` or `Inactive`.
  - On Leave: the officer keeps their desks, but new cases are not auto-assigned to them.
  - Inactive: the officer's desks are retired. There is no delete.
- **region** is the area the officer covers, including everything under it. When several officers match a case, the one with the nearest area wins.
- **temporary password**: a password the admin types for a new officer. The officer cannot sign in with it. They must replace it first (section 8). `must_change_password` on the officer is `true` until they do.

## 2. Conventions

| Item         | Value                                                                       |
| :----------- | :-------------------------------------------------------------------------- |
| Base URL     | `{base_url}/api/v1`                                                         |
| Auth         | `Authorization: Bearer <access_token>` from `POST /api/v1/auth/login`       |
| Role         | Grievance Admin, System Manager or Administrator. Others get 403. See below |
| Content type | `Content-Type: application/json` for `POST` and `PATCH`                     |
| Officer id   | The email. Put it through `encodeURIComponent` before using it in a path    |

The Grievance Review Officer role is read-only. It may call the `GET` endpoints for **officers** and gets 403 on every `POST`, `PATCH` and `DELETE`. Reading `Admin` and `Reviewer` accounts needs an admin role too, so a Review Officer gets 403 on `role=Admin`, `role=Reviewer` and on a single Admin or Reviewer id. Whether a Review Officer should see those is still a product decision; today the answer is no.

| Call                                               | Allowed roles                                                            |
| :------------------------------------------------- | :----------------------------------------------------------------------- |
| `GET` list, one, counts, for `Officer`             | Grievance Admin, System Manager, Administrator, Grievance Review Officer |
| `GET` list, one, counts, for `Admin` or `Reviewer` | Grievance Admin, System Manager, Administrator                           |
| `POST`, `PATCH`, password reset                    | Grievance Admin, System Manager, Administrator                           |
| Password reset of an `Admin` account               | System Manager or Administrator only                                     |

System Manager and Administrator accounts are never created, changed or reset through this API.

### Success envelope

```json
{
  "status": "success",
  "message": "Officer created",
  "data": { "officer": { "...": "..." } },
  "meta": { "api_version": "v1", "status": "current" },
  "request_id": "07994a2f-1c09-4a71-aa78-c27a421a1ef0"
}
```

List responses add a top-level `pagination` block next to `data`.

### Error envelope

```json
{
  "status": "error",
  "message": "Validation failed",
  "code": "VALIDATION_ERROR",
  "details": {
    "level": "Input should be 'L1' or 'L2'",
    "email": "Value error, Invalid email address format"
  },
  "meta": {},
  "request_id": "07994a2f-1c09-4a71-aa78-c27a421a1ef0"
}
```

| HTTP | `code`              | When                                                                                    |
| :--- | :------------------ | :-------------------------------------------------------------------------------------- |
| 400  | `VALIDATION_ERROR`  | A bad field, an unknown field, or a rule broken. `details` maps field name to message   |
| 401  | none                | Missing or expired token. Refresh the token or sign in again                            |
| 403  | `PERMISSION_DENIED` | The signed-in user may not do this (section 2), or a rule in section 7, 9 or 13 applies |
| 404  | `NOT_FOUND`         | No account with that id                                                                 |

Sign-in (`POST /api/v1/auth/login`) also returns **403 `PASSWORD_CHANGE_REQUIRED`** when an officer uses a correct temporary password. It is not an error to show: it means "send the user to Set your password".

For field errors, show `details[field]` next to that input. For rule errors (`details` is empty or the field is not on screen), show `message`.

## 3. Where the form values come from

| Form field                | Source                                                                                              |
| :------------------------ | :-------------------------------------------------------------------------------------------------- |
| Department                | `GET /api/v1/grievances/options` returns `departments[]` with `department_id` and `department_name` |
| Service categories        | `GET /api/v1/grievances/options?department=<id>` returns `service_categories[]`                     |
| Region                    | `GET /api/v1/administrative-areas?parent=` for the cascading area picker                            |
| Reports to (L1 form only) | `GET /api/v1/officers?level=L2&status=Active`, then use each officer's `name` as the value          |

With `department` set, `service_categories[]` holds only the categories that department has an active category assignment for, each with `category_name` and `code`. Refetch it when the department changes, and clear any selected categories.

Department accepts the id, name or short name. A category accepts its name or code. `region` accepts an area id, code, path code or, for regions, the name. Send what the pickers give you, which is the id.

## 4. List officers

`GET /api/v1/officers`

| Query param        | Type                             | Notes                                                    |
| :----------------- | :------------------------------- | :------------------------------------------------------- |
| `role`             | `Officer`, `Admin`, `Reviewer`   | Default `Officer`. See section 13 for the other two      |
| `level`            | `L1` or `L2`                     | The tab filter: L1 tab sends `L1`, L2 tab sends `L2`     |
| `department`       | string                           | Department id, name or short name                        |
| `status`           | `Active`, `On Leave`, `Inactive` |                                                          |
| `service_category` | string                           | Category name or code                                    |
| `region`           | string                           | Matches the officer's own area exactly, not its children |
| `q`                | string                           | Searches name and email                                  |
| `page`             | integer, 1 or more               | Default 1                                                |
| `page_size`        | integer, 1 to 100                | Default 20                                               |

Unknown params and bad values return 400. A blank value is treated as not set. Results are sorted by name, then email. `status`, `q`, `page` and `page_size` apply to every role. `level`, `department`, `service_category` and `region` are officer filters: with `role=Admin` or `role=Reviewer` they return 400 rather than an empty page, and so does `status=On Leave`.

Admin and Reviewer accounts never appear in the officer list, whatever the filters.

`must_change_password` is `true` while the officer still holds a temporary password and has not set their own. Use it to show an "Awaiting first sign-in" badge and the "Issue new temporary password" action.

Ids such as `region-ET14` and `GR-RBAC-00012` in the examples are illustrative. Use the values the API returns.

```http
GET /api/v1/officers?level=L1&department=MoA&status=Active&page=1&page_size=20
```

```json
{
  "status": "success",
  "message": "Officers retrieved",
  "data": {
    "officers": [
      {
        "name": "tigist.alemu@example.com",
        "full_name": "Tigist Alemu",
        "role": "Officer",
        "designation": "Nodal Officer",
        "level": "L1",
        "department": "MoA",
        "email": "tigist.alemu@example.com",
        "phone": "+251911123456",
        "must_change_password": false,
        "region": "region-ET14",
        "region_name": "Addis Ababa",
        "status": "Active",
        "service_categories": ["Inputs", "Schemes"],
        "reports_to": "yonas.mekonnen@example.com",
        "reports_to_name": "Yonas Mekonnen",
        "assignments": [
          {
            "assignment": "GR-RBAC-00012",
            "service_category": "Inputs",
            "department": "MoA",
            "level": "L1",
            "region": "region-ET14",
            "active": true,
            "on_leave": false
          }
        ]
      }
    ]
  },
  "meta": { "api_version": "v1", "status": "current" },
  "request_id": "…",
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total_count": 1,
    "total_pages": 1,
    "has_next": false,
    "has_prev": false
  }
}
```

## 5. Get one officer

`GET /api/v1/officers/{officer}`

No query or body. Returns `data.officer` with the same shape as above, for an account of any role. 404 if the id is not an officer, admin or reviewer. 403 if the account is an Admin or Reviewer and the caller is a Review Officer.

## 6. Create an officer

`POST /api/v1/officers`

```json
{
  "full_name": "Tigist Alemu",
  "designation": "Nodal Officer",
  "level": "L1",
  "department": "MoA",
  "email": "tigist.alemu@example.com",
  "phone": "+251911123456",
  "region": "region-ET14",
  "status": "Active",
  "service_categories": ["Inputs", "Schemes"],
  "reports_to": "yonas.mekonnen@example.com",
  "temporary_password": "Welcome2026"
}
```

| Field                | Required | Notes                                                                                              |
| :------------------- | :------- | :------------------------------------------------------------------------------------------------- |
| `role`               | no       | `Officer` (default), `Admin` or `Reviewer`. This table is for `Officer`; section 13 for the others |
| `full_name`          | yes      | Non-blank                                                                                          |
| `designation`        | yes      | Title shown on the card, for example "Nodal Officer"                                               |
| `level`              | yes      | `L1` or `L2`                                                                                       |
| `department`         | yes      | Must be an active department                                                                       |
| `email`              | yes      | Valid email. Stored lowercase and becomes the officer id                                           |
| `service_categories` | yes      | At least one                                                                                       |
| `phone`              | no       | Validated. Blank means not set                                                                     |
| `region`             | no       | Blank means every area                                                                             |
| `status`             | no       | Default `Active`                                                                                   |
| `reports_to`         | no       | L2 officer id. Only for `level: "L1"`. Blank means none                                            |
| `temporary_password` | yes      | At least 8 characters with a letter and a number. See below                                        |

Returns 200 with `message: "Officer created"` and `data.officer`. There is no 201.

If the email already belongs to a login that is not an officer yet, that login is reused and becomes an officer, and its own password is **not** changed (see below). If it is already an officer, you get 400 with the message "… is already an officer."

An account holds **one** grievance role. A login that already holds Grievance Admin, Grievance Review Officer or Grievance Submitter is refused with 400 "… already holds the … role. An account holds one grievance role, so it cannot also be given Grievance Officer." The roles add up in Frappe, so a second one would quietly grant access nobody chose. A System Manager or Administrator login is refused with 403.

### Temporary password

Add a required "Temporary password" field to the Add modal. The admin types it and tells the officer, for example in person or by phone. It is not emailed. A missing or weak value returns 400 with the message under `details.temporary_password`.

- The officer can **not** sign in with it. `POST /api/v1/auth/login` answers 403 with `code: "PASSWORD_CHANGE_REQUIRED"` even when the password is right. Send them to a "Set your password" screen (section 8).
- It is applied to a **new** login only. If the email already belongs to a login, that person keeps the password they already have. The request still succeeds, `must_change_password` is `false`, and `message` says "… already had a login, so their existing password is unchanged." Show that message, so the admin does not give the officer a password that will not work.
- The password is never returned by any endpoint. Do not keep it in state after the request.

## 7. Issue a new temporary password

`POST /api/v1/officers/{officer}/password-resets`

For an officer who forgot their password, or who never used the temporary one they were given.

```json
{ "temporary_password": "Welcome2026" }
```

Returns 200 with `data.officer`, where `must_change_password` is now `true`. Rules:

- The account's current sessions end at once, so use it when an account may be compromised too.
- The password rule is the same as on create. A weak one returns 400 with the message under `details.temporary_password`.
- It works for an officer, admin or reviewer. Any other email returns 404.
- A Grievance Admin can reset Officers and Reviewers. Resetting an **Admin** account needs a System Manager or Administrator; another Grievance Admin gets 403. An account that holds the Grievance Admin role counts as an Admin account even if it is also an officer.
- Nobody can reset their own password here (403).
- A System Manager or Administrator account is never reset here (403). Recovering one stays on Frappe Desk or `bench`, because the auth service has no System Manager reset endpoint yet.
- Meant to be limited to 10 requests per admin every 5 minutes (see the note in section 12), and logged with the caller, the account and its role.

## 8. The officer sets their own password

This is the officer's screen, not the admin's. It calls the auth service and needs no token.

`POST /api/v1/auth/password/initial`

```json
{
  "usr": "tigist.alemu@example.com",
  "current_password": "Welcome2026",
  "new_password": "MyOwn#Password1"
}
```

- `new_password` needs at least 8 characters with a letter, a number and a symbol, and must differ from the temporary one. A weak one returns 400.
- On success (200) nothing is signed in. Send the officer back to the sign-in screen to use the new password.
- A wrong or unknown email or password, or an account that holds no temporary password, all return the same 401 "Invalid login credentials". Show one message.
- Limited to 10 requests per address every 5 minutes. Repeated wrong passwords lock the account, as at sign-in.

When sign-in returns 403 `PASSWORD_CHANGE_REQUIRED`, open this screen with the email the officer just typed, and ask for the temporary password again as `current_password`.

## 9. Update an officer, including deactivate

`PATCH /api/v1/officers/{officer}`

Send **only the fields that changed**. An empty body returns 400 "No fields to update."

```json
{
  "designation": "Chief Nodal Officer",
  "service_categories": ["Inputs", "Credit"],
  "phone": null
}
```

| Field                | Notes                                                                                       |
| :------------------- | :------------------------------------------------------------------------------------------ |
| `full_name`          | Non-blank                                                                                   |
| `designation`        | Non-blank                                                                                   |
| `level`              | `L1` or `L2`. Clears the officer's `reports_to`. See the rules below                        |
| `department`         | Moves the officer to the same categories in the new department, which must have assignments |
| `phone`              | `null` or blank clears it                                                                   |
| `region`             | `null` or blank clears it                                                                   |
| `status`             | `Active`, `On Leave` or `Inactive`                                                          |
| `service_categories` | **Replaces the whole list.** Send every category the officer should keep                    |
| `reports_to`         | An L2 officer id for an L1. `null` or blank clears it                                       |

`email` cannot be changed. Sending it returns 400, and so does sending `role`: the role is fixed once the account is created.

An Admin or Reviewer takes only `full_name`, `phone`, `designation` and `status` (section 13).

| Action in the UI | Request body               |
| :--------------- | :------------------------- |
| Deactivate       | `{ "status": "Inactive" }` |
| Mark on leave    | `{ "status": "On Leave" }` |
| Reactivate       | `{ "status": "Active" }`   |
| Promote to L2    | `{ "level": "L2" }`        |

Returns 200 with `message: "Officer updated"` and the updated `data.officer`. Use that object to refresh the card instead of reloading the list.

Nobody can deactivate their own account (403), and the last active Grievance Admin cannot be deactivated (400).

## 10. Rules that produce a 400

| Message (shortened)                                               | What to tell the user                                                                                                                                                  |
| :---------------------------------------------------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| "has no category assignment for: …. Create it first."             | The department has no category-wide routing rule for it. A rule limited to one grievance type or service provider does not count. Set up the category assignment first |
| "An officer needs at least one service category."                 | Keep at least one category selected                                                                                                                                    |
| "… is already an officer."                                        | That email is already registered as an officer                                                                                                                         |
| "Only an L1 officer reports to an L2 officer."                    | Hide "Reports to" for L2                                                                                                                                               |
| "… is not an L2 officer."                                         | Pick the supervisor from the L2 list                                                                                                                                   |
| "… cannot change level while other officers report to them."      | Move or re-point those L1 officers first                                                                                                                               |
| "… would be left without officers. Assign another officer first." | The officer is the only one on a desk they are being removed from                                                                                                      |
| "Department '…' does not exist." / "Region '…' does not exist."   | The picker value is stale. Reload the options                                                                                                                          |
| "… already holds the … role. An account holds one grievance role" | The login is already a submitter, officer, admin or reviewer. Use a different email                                                                                    |
| "… is already an Admin or Reviewer."                              | That email already holds the Admin or Reviewer role                                                                                                                    |
| "… is the last active Grievance Admin and cannot be deactivated." | Make another Grievance Admin first                                                                                                                                     |
| "… cannot be changed on a … account."                             | An Admin or Reviewer has no level, department, region, supervisor or categories                                                                                        |

## 11. Suggested screen wiring

| Screen part                        | Call                                                                                      |
| :--------------------------------- | :---------------------------------------------------------------------------------------- |
| Nodal Officers tab (L1)            | `GET /officers?level=L1&page=…` and the status and department filters                     |
| Senior Officers tab (L2)           | `GET /officers?level=L2&page=…`                                                           |
| Search box                         | Add `q`. Debounce, and reset `page` to 1                                                  |
| Officer card                       | Fields on `officer`. `status` drives the Active, On Leave or Inactive badge               |
| Add modal                          | `POST /officers`. On success, add the returned `officer` or refetch                       |
| Edit modal                         | `PATCH /officers/{id}` with only the changed fields                                       |
| Deactivate and On Leave actions    | `PATCH /officers/{id}` with `status`                                                      |
| "Issue new temporary password"     | `POST /officers/{id}/password-resets`. Show it when the officer cannot sign in            |
| Awaiting first sign-in badge       | `officer.must_change_password` is `true`                                                  |
| Officer's "Set your password" page | `POST /auth/password/initial`, opened when sign-in returns `PASSWORD_CHANGE_REQUIRED`     |
| L2 card "N officers report to me"  | Count L1 rows from `GET /officers?level=L1` where `reports_to` equals the L2 id           |
| Active, On Leave, Inactive counts  | `GET /officers/status-counts?role=…` with the tab's filters. Remove the hardcoded numbers |
| Admin tab                          | `GET /officers?role=Admin`, `POST /officers` with `role: "Admin"`                         |
| Reviewer tab                       | `GET /officers?role=Reviewer`, `POST /officers` with `role: "Reviewer"`                   |

## 12. Not available yet

- Sending the temporary password to the officer by email or SMS. The admin passes it on.
- Department Head as a level. Only `L1` and `L2` are accepted.
- Performance metrics per officer. They come from the statistics API.
- Recovering a System Manager or Administrator account. It stays on Frappe Desk or `bench` until the auth service gets its own endpoint.
- The password-reset rate limit. The handler calls the auth service's `check_rate_limit` with 10 requests per 5 minutes, but that helper reads its counter through a different cache key than it writes, so it does not limit anything today. It is the same for every role and is tracked on the auth service, not here.

## 13. Admin and Reviewer accounts

`role` is `Admin` (the Grievance Admin role) or `Reviewer` (the Grievance Review Officer role, read-only). They are listed, read, created, changed and reset through the same endpoints as officers.

**Create.** `POST /api/v1/officers` with `role`:

```json
{
  "role": "Admin",
  "full_name": "Selam Bekele",
  "email": "selam.bekele@example.com",
  "phone": "+251911000001",
  "designation": "Programme Administrator",
  "temporary_password": "Welcome2026"
}
```

| Field                                                               | Admin or Reviewer                                                    |
| :------------------------------------------------------------------ | :------------------------------------------------------------------- |
| `full_name`                                                         | required                                                             |
| `email`                                                             | required                                                             |
| `phone`                                                             | required                                                             |
| `temporary_password`                                                | required. Same rule and same behaviour as for an officer (section 6) |
| `designation`                                                       | optional                                                             |
| `status`                                                            | optional. `Active` (default) or `Inactive`. `On Leave` returns 400   |
| `level`, `department`, `service_categories`, `reports_to`, `region` | **refused** with 400. `details` names each field sent                |

The login is created only when the email is new, and holds the requested role and no other. An existing login with no grievance role is kept as it is, password included, and the message says so. A login that already holds a grievance role is refused (section 6), including one that already holds this role.

**Record.** The same fields as an officer, plus `role`. For an Admin or Reviewer, `level`, `department`, `region`, `region_name`, `reports_to` and `reports_to_name` are `null`, `service_categories` is `[]` and `assignments` is `[]`. `designation`, `phone`, `status` and `must_change_password` are filled as for an officer.

```json
{
  "name": "selam.bekele@example.com",
  "full_name": "Selam Bekele",
  "role": "Admin",
  "designation": "Programme Administrator",
  "level": null,
  "department": null,
  "email": "selam.bekele@example.com",
  "phone": "+251911000001",
  "must_change_password": true,
  "region": null,
  "region_name": null,
  "status": "Active",
  "service_categories": [],
  "reports_to": null,
  "reports_to_name": null,
  "assignments": []
}
```

**Update.** `PATCH /api/v1/officers/{id}` takes only `full_name`, `phone` (not blank), `designation` and `status`. Anything else returns 400. `role` and `email` are fixed.

**Status.** Only `Active` and `Inactive`; `On Leave` returns 400.

- `Inactive` disables the login (`User.enabled = 0`) and ends its sessions and refresh tokens. It cannot sign in, and an access token it already holds is refused.
- `Active` enables the login again. The password is unchanged, so an account that never replaced its temporary password still has to.
- A login disabled on Frappe Desk reads as `Inactive` here too, so the list, the record and the counts agree.
- Nobody can deactivate their own account (403). The last active Grievance Admin cannot be deactivated (400).

**Reading.** Admin and Reviewer accounts need an admin role to read (section 2). They never appear in the default `Officer` list, in officer statistics, or anywhere in routing and escalation.

**Passwords.** A Grievance Admin can reissue a Reviewer's temporary password. An Admin's can be reissued only by a System Manager or Administrator (section 7).

### How they are recorded

An Admin or Reviewer is a plain Frappe User that holds the role. Nothing else records them, so there is no second list to keep in step:

| Item        | Where it lives                                                                                          |
| :---------- | :------------------------------------------------------------------------------------------------------ |
| Role        | The `Has Role` row on the User (`Grievance Admin` or `Grievance Review Officer`)                        |
| Status      | `User.enabled`. Active is enabled, Inactive is disabled. Disabling in Desk is reflected here at once    |
| Designation | The `oan_designation` Custom Field on User, seeded by `setup/install.py` on install and `bench migrate` |
| Name, phone | `User.full_name` and `User.phone`                                                                       |

They have no desk, so routing, escalation, scope checks and officer statistics never see them. An account with officer rows is an officer here whatever other roles it holds, and a System Manager or Administrator is never listed or managed, even if it also holds one of these roles. Logins created on Frappe Desk with the role are listed like any other.

## 14. Status counts

`GET /api/v1/officers/status-counts`

Replaces the hardcoded counts on the tabs. It takes the list's filters **without** `status`, so a tab's counts follow the same filters as its list.

| Query param        | Type                           | Notes                   |
| :----------------- | :----------------------------- | :---------------------- |
| `role`             | `Officer`, `Admin`, `Reviewer` | Default `Officer`       |
| `level`            | `L1` or `L2`                   | Officers only           |
| `department`       | string                         | Officers only           |
| `service_category` | string                         | Officers only           |
| `region`           | string                         | Officers only           |
| `q`                | string                         | Searches name and email |

`status`, `page` and `page_size` return 400, as do the officer-only filters with `role=Admin` or `role=Reviewer`.

```json
{
  "status": "success",
  "message": "Officer status counts retrieved",
  "data": { "active": 12, "on_leave": 2, "inactive": 3, "total": 17 },
  "meta": { "api_version": "v1", "status": "current" },
  "request_id": "…"
}
```

- Each person is counted **once**, in the status their record shows: the most available of their desk rows wins. `total` is the number of people, and `active + on_leave + inactive = total`.
- The list's `status` filter matches an officer who has **a row** in that status. An officer whose rows differ (an admin edited one desk directly) can therefore be on two status pages but is one count here. In the normal case every row of an officer is equal, and the counts equal the list totals for the same filters.
- Admin and Reviewer accounts have no On Leave, so `on_leave` is `0`.
- Permissions follow the role counted: `Officer` for every admin-read role, `Admin` and `Reviewer` for admin roles only.
- The route is registered before `/officers/{officer}`, so `status-counts` is never read as an officer id.

Use it for the numbers beside the tab titles, and delete the hardcoded ones.
