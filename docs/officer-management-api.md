# Officer Management API: frontend integration guide

For the Nodal Officers (L1), Senior Officers (L2), Department Head (L3, the Admin tab) and Reviewer tabs in Administration, and the Add and Edit modals. These endpoints replace the hardcoded `initialOfficers` arrays, and `GET /officers/status-counts` replaces the hardcoded Active, On Leave and Inactive counts.

Performance numbers (Assigned, Resolved, Avg Time, Resolution Rate) are **not** in this API. They come from the statistics API.

One resource serves two kinds of account, chosen by `role`: `Officer` (the default) and `Reviewer`. A department head is an officer at level `L3`. Anything that does not send `role` or `L3` behaves as it did before.

## 1. Concepts

- An **officer** is a login (User) placed on one or more **desks**. A desk is one department and one service category.
- The officer **id** is the officer's email, lowercase. Use it in URLs and for `reports_to`.
- **level** is `L1` (Nodal Officer), `L2` (Senior Nodal Officer) or `L3` (Department Head). It can be changed later.
  - A department head sits on the department's desks like the others and is the department's final escalation rung. They approve the department's reassignments and are reached by escalation, so routing does not assign them first-line cases.
- **role** is `Officer` or `Reviewer`. A **Reviewer** is placed on a department's desks with a department and service categories, like an officer, but has no level and no supervisor. They read that department's grievances and nothing else, are never assigned a case, and cannot change one. Their record has `role: "Reviewer"` and `level: null`.
- **service_categories** are the categories the officer handles. Every category must already have a category assignment for the chosen department, otherwise the request fails.
- **reports_to** is an L2 officer id and is only valid for an L1. It is where that L1's cases escalate to.
- **status** is `Active`, `On Leave` or `Inactive`.
  - On Leave: the officer keeps their desks, but new cases are not auto-assigned to them.
  - Inactive: the officer's desks are retired. There is no delete.
  - A Reviewer is only Active or Inactive. Inactive also disables their login and ends their sessions, so they cannot sign in.
- **region** is the area the officer covers, including everything under it. When several officers match a case, the one with the nearest area wins.
- **temporary password**: a password the admin types for a new officer. The officer cannot sign in with it. They must replace it first (section 8). `must_change_password` on the officer is `true` until they do.

## 2. Conventions

| Item         | Value                                                                    |
| :----------- | :----------------------------------------------------------------------- |
| Base URL     | `{base_url}/api/v1`                                                      |
| Auth         | `Authorization: Bearer <access_token>` from `POST /api/v1/auth/login`    |
| Role         | Grievance Admin, System Manager or Administrator. Others get 403         |
| Content type | `Content-Type: application/json` for `POST` and `PATCH`                  |
| Officer id   | The email. Put it through `encodeURIComponent` before using it in a path |

The Grievance Review Officer role is read-only. It may call the `GET` endpoints for officers and gets 403 on every `POST`, `PATCH` and `DELETE`. Reading Reviewer accounts (`role=Reviewer`, or one Reviewer's id) needs an admin role too, so a Review Officer gets 403 there. Whether a Review Officer should see them is still a product decision; today the answer is no.

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
    "level": "Input should be 'L1', 'L2' or 'L3'",
    "email": "Value error, Invalid email address format"
  },
  "meta": {},
  "request_id": "07994a2f-1c09-4a71-aa78-c27a421a1ef0"
}
```

| HTTP | `code`              | When                                                                                  |
| :--- | :------------------ | :------------------------------------------------------------------------------------ |
| 400  | `VALIDATION_ERROR`  | A bad field, an unknown field, or a rule broken. `details` maps field name to message |
| 401  | none                | Missing or expired token. Refresh the token or sign in again                          |
| 403  | `PERMISSION_DENIED` | The signed-in user may not do this (section 2), or a rule in section 7 or 9 applies   |
| 404  | `NOT_FOUND`         | No officer with that id                                                               |

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

| Query param        | Type                             | Notes                                                                                |
| :----------------- | :------------------------------- | :----------------------------------------------------------------------------------- |
| `role`             | `Officer` or `Reviewer`          | Default `Officer`                                                                    |
| `level`            | `L1`, `L2` or `L3`               | The tab filter. The Admin tab (department heads) sends `L3`. Not for `role=Reviewer` |
| `department`       | string                           | Department id, name or short name                                                    |
| `status`           | `Active`, `On Leave`, `Inactive` |                                                                                      |
| `service_category` | string                           | Category name or code                                                                |
| `region`           | string                           | Matches the officer's own area exactly, not its children                             |
| `q`                | string                           | Searches name and email                                                              |
| `page`             | integer, 1 or more               | Default 1                                                                            |
| `page_size`        | integer, 1 to 100                | Default 20                                                                           |

Unknown params and bad values return 400. A blank value is treated as not set. Results are sorted by name, then email. A Reviewer has no level, so `level` and `status=On Leave` return 400 with `role=Reviewer`. Listing Reviewers needs an admin role.

Reviewers never appear in the Officer list, and the default list now includes department heads (`L3`).

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

No query or body. Returns `data.officer` with the same shape as above, for an officer, a department head or a Reviewer. 404 if the id is none of them. 403 for a Reviewer's id if the caller is a Review Officer.

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

| Field                | Required | Notes                                                       |
| :------------------- | :------- | :---------------------------------------------------------- |
| `role`               | no       | `Officer` (default) or `Reviewer`. See "Reviewers" below    |
| `full_name`          | yes      | Non-blank                                                   |
| `designation`        | yes      | Title shown on the card, for example "Nodal Officer"        |
| `level`              | yes      | `L1`, `L2` or `L3`. `L3` makes a department head            |
| `department`         | yes      | Must be an active department                                |
| `email`              | yes      | Valid email. Stored lowercase and becomes the officer id    |
| `service_categories` | yes      | At least one                                                |
| `phone`              | no       | Validated. Blank means not set                              |
| `region`             | no       | Blank means every area                                      |
| `status`             | no       | Default `Active`                                            |
| `reports_to`         | no       | L2 officer id. Only for `level: "L1"`. Blank means none     |
| `temporary_password` | yes      | At least 8 characters with a letter and a number. See below |

Returns 200 with `message: "Officer created"` and `data.officer`. There is no 201.

If the email already belongs to a login that is not an officer yet, that login is reused and becomes an officer, and its own password is **not** changed (see below). If it is already an officer, you get 400 with the message "… is already an officer." A login keeps any other roles it already has. A System Manager or Administrator login is refused with 403.

### Department head

Send `level: "L3"` with the department and the categories the head covers, as for any officer. The head is placed on those desks, is the department's top escalation rung and approves its reassignments. They are not assigned first-line cases, `reports_to` is not accepted for them, and each category still needs a category assignment for the department.

### Reviewers

`POST /api/v1/officers` with `role: "Reviewer"`:

```json
{
  "role": "Reviewer",
  "full_name": "Rahel Review",
  "department": "MoA",
  "service_categories": ["Inputs", "Schemes"],
  "email": "rahel.review@example.com",
  "temporary_password": "Welcome2026"
}
```

A Reviewer needs `full_name`, `email`, `department`, `service_categories` and `temporary_password`. `designation`, `phone`, `region` and `status` (`Active` or `Inactive`) are optional. `level` and `reports_to` are refused with 400. The login gets the Grievance Review Officer role, is placed on the department's desks for those categories, and reads only the grievances in those desks' scopes. It is never assigned a case, never appears in the officer list, statistics or the officer picker, and cannot change anything. A reviewer who has no desk at all keeps the earlier oversight of every filed case, so place them on desks to scope them.

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

- The officer's current sessions end at once, so use it when an account may be compromised too.
- The password rule is the same as on create. A weak one returns 400 with the message under `details.temporary_password`.
- It works for an officer, a department head or a Reviewer. Any other email returns 404.
- It is refused with 403 for an account that is itself an admin (Grievance Admin or System Manager), even if that account is also an officer, and for your own account.
- Limited to 10 requests per admin every 5 minutes.

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
| `level`              | `L1`, `L2` or `L3`. Clears the officer's `reports_to`. See the rules below                  |
| `department`         | Moves the officer to the same categories in the new department, which must have assignments |
| `phone`              | `null` or blank clears it                                                                   |
| `region`             | `null` or blank clears it                                                                   |
| `status`             | `Active`, `On Leave` or `Inactive`                                                          |
| `service_categories` | **Replaces the whole list.** Send every category the officer should keep                    |
| `reports_to`         | An L2 officer id for an L1. `null` or blank clears it                                       |

`email` cannot be changed. Sending it returns 400. A Reviewer has no level or supervisor, so `level` and `reports_to` return 400 for them, and so does `status: "On Leave"`. Inactive on a Reviewer also disables their login and ends their sessions; Active enables it again. Nobody can deactivate their own account (403).

| Action in the UI | Request body               |
| :--------------- | :------------------------- |
| Deactivate       | `{ "status": "Inactive" }` |
| Mark on leave    | `{ "status": "On Leave" }` |
| Reactivate       | `{ "status": "Active" }`   |
| Promote to L2    | `{ "level": "L2" }`        |

Returns 200 with `message: "Officer updated"` and the updated `data.officer`. Use that object to refresh the card instead of reloading the list.

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

## 11. Suggested screen wiring

| Screen part                        | Call                                                                                  |
| :--------------------------------- | :------------------------------------------------------------------------------------ |
| Nodal Officers tab (L1)            | `GET /officers?level=L1&page=…` and the status and department filters                 |
| Senior Officers tab (L2)           | `GET /officers?level=L2&page=…`                                                       |
| Search box                         | Add `q`. Debounce, and reset `page` to 1                                              |
| Officer card                       | Fields on `officer`. `status` drives the Active, On Leave or Inactive badge           |
| Add modal                          | `POST /officers`. On success, add the returned `officer` or refetch                   |
| Edit modal                         | `PATCH /officers/{id}` with only the changed fields                                   |
| Deactivate and On Leave actions    | `PATCH /officers/{id}` with `status`                                                  |
| "Issue new temporary password"     | `POST /officers/{id}/password-resets`. Show it when the officer cannot sign in        |
| Awaiting first sign-in badge       | `officer.must_change_password` is `true`                                              |
| Officer's "Set your password" page | `POST /auth/password/initial`, opened when sign-in returns `PASSWORD_CHANGE_REQUIRED` |
| L2 card "N officers report to me"  | Count L1 rows from `GET /officers?level=L1` where `reports_to` equals the L2 id       |
| Admin tab (department heads)       | `GET /officers?level=L3`, `POST /officers` with `level: "L3"`                         |
| Reviewer tab                       | `GET /officers?role=Reviewer`, `POST /officers` with `role: "Reviewer"`               |
| Active, On Leave, Inactive counts  | `GET /officers/status-counts` with the tab's filters. Remove the hardcoded numbers    |

## 12. Not available yet

- Sending the temporary password to the officer by email or SMS. The admin passes it on.
- Performance metrics for department heads. The statistics API still covers L1 and L2.
- Recovering a System Manager or Administrator account. It stays on Frappe Desk or `bench`.
- The password-reset rate limit. The handler calls the auth service's `check_rate_limit` with 10 requests per 5 minutes, but that helper reads its counter through a different cache key than it writes, so it does not limit anything today. It is tracked on the auth service, not here.
- Performance metrics per officer. They come from the statistics API.

## 13. Status counts

`GET /api/v1/officers/status-counts`

Replaces the hardcoded counts on the tabs. It takes the list's filters **without** `status`, so a tab's counts follow the same filters as its list.

| Query param        | Type                  | Notes                                  |
| :----------------- | :-------------------- | :------------------------------------- |
| `role`             | `Officer`, `Reviewer` | Default `Officer`                      |
| `level`            | `L1`, `L2` or `L3`    | Not for `role=Reviewer`                |
| `department`       | string                | Department id, name or short name      |
| `service_category` | string                | Category name or code                  |
| `region`           | string                | Matches the account's own area exactly |
| `q`                | string                | Searches name and email                |

`status`, `page` and `page_size` return 400.

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
- The list's `status` filter matches an account that has **a row** in that status. One whose rows differ (an admin edited one desk directly) can therefore be on two status pages but is one count here. In the normal case every row of an account is equal, and the counts equal the list totals for the same filters.
- A Reviewer has no On Leave, so `on_leave` is `0`.
- Counting Reviewers needs an admin role, as listing them does. The route is registered before `/officers/{officer}`, so `status-counts` is never read as an officer id.

Use it for the numbers beside the tab titles, and delete the hardcoded ones.
