# Department API: frontend integration guide

For the Administration Departments screen, the Add and Edit department modals, and the places that today hardcode a department list: the Nodal Officer department dropdown and the Category Assignments department picker.

The department list in `GET /api/v1/grievances/options` stays as it is. It is the read-only dropdown source (active departments only). This API is its write side.

## 1. Concepts

- A **department** is the unit a grievance is routed to and the mailbox it is notified at.
- The department **id** (`department_id`) is the department name. It is fixed once the department exists, because grievances, category assignments and response templates link to it. Use it in URLs and wherever another API asks for a `department`.
- **email_account** is where the assigned department is notified. It is stored lowercase.
- **head_of_dept** is the user id (an email) of the department head. It must be an enabled Grievance Officer or Grievance Admin. Send `null` to clear it.
  - The head is the `Dept Head` recipient of SLA-breach escalation notifications. Notifications first look for an officer desk covering the case that holds the department head level, and use `head_of_dept` when there is none.
  - Assigning the head here does not give the user a desk or any extra permission.
- **active** is false once a department has been retired. There is no hard delete.
- **l1_role_level**, **l2_role_level** and **routing_strategy** are the routing preferences category assignments read from the department. A department needs `l1_role_level` (and `l2_role_level` for a senior officer) before a category assignment can name it, so set them when the department is created.

## 2. Conventions

| Item         | Value                                                                    |
| :----------- | :----------------------------------------------------------------------- |
| Base URL     | `{base_url}/api/v1`                                                      |
| Auth         | `Authorization: Bearer <access_token>` from `POST /api/v1/auth/login`    |
| Role         | Grievance Admin, System Manager or Administrator. Others get 403         |
| Content type | `Content-Type: application/json` for `POST` and `PATCH`                  |
| Department id | The department name. Put it through `encodeURIComponent` in a path      |

Responses use the same success and error envelopes as the officer API (see `docs/officer-management-api.md`, section 2). List responses add a top-level `pagination` block next to `data`.

| HTTP | `code`              | When                                                                                         |
| :--- | :------------------ | :------------------------------------------------------------------------------------------- |
| 400  | `VALIDATION_ERROR`  | A bad field, an unknown field, or a rule broken. `details` maps field name to message        |
| 401  | none                | Missing or expired token                                                                     |
| 403  | `PERMISSION_DENIED` | The signed-in user is not an admin                                                           |
| 404  | `NOT_FOUND`         | No department with that id                                                                   |
| 409  | `DUPLICATE_ENTRY`   | The department name, or its short name, is already taken                                     |

For field errors, show `details[field]` next to that input. For rule errors (`details` is empty or the field is not on screen), show `message`.

## 3. The department record

```json
{
  "department_id": "Ministry of Agriculture",
  "department_name": "Ministry of Agriculture",
  "short_name": "MoA",
  "email_account": "moa@example.com",
  "phone": "+251111234567",
  "head_of_dept": "head@example.com",
  "head_of_dept_name": "Hana Bekele",
  "active": true,
  "l1_role_level": "nodal_officer",
  "l2_role_level": "senior_nodal_officer",
  "routing_strategy": "Primary First"
}
```

`department_id`, `department_name` and `email_account` carry the same values as the `departments[]` entries in `GET /api/v1/grievances/options`, so a value picked from either source can be sent to the other APIs. `short_name`, `phone`, `head_of_dept`, `head_of_dept_name`, `l1_role_level`, `l2_role_level` and `routing_strategy` are `null` when not set.

## 4. List departments

`GET /api/v1/departments`

| Query param   | Type               | Notes                                                                      |
| :------------ | :----------------- | :------------------------------------------------------------------------- |
| `active`      | boolean            | `true` for live departments, `false` for retired ones. Omit for both       |
| `head_of_dept`| string             | User id of the head                                                        |
| `q`           | string             | Searches name, short name and email                                        |
| `page`        | integer, 1 or more | Default 1                                                                  |
| `page_size`   | integer, 1 to 100  | Default 20                                                                 |

Unknown params and bad values return 400. A blank value is treated as not set. Results are sorted by name. Retired departments are included unless `active=true` is sent.

```json
{
  "status": "success",
  "message": "Departments retrieved",
  "data": { "departments": [ { "...": "see section 3" } ] },
  "meta": { "api_version": "v1", "status": "current" },
  "request_id": "…",
  "pagination": { "page": 1, "page_size": 20, "total_count": 1, "total_pages": 1, "has_next": false, "has_prev": false }
}
```

## 5. Get one department

`GET /api/v1/departments/{department}`

No query or body. Returns `data.department`. 404 if the id does not exist.

## 6. Create a department

`POST /api/v1/departments`

```json
{
  "department_name": "Ministry of Agriculture",
  "short_name": "MoA",
  "email_account": "moa@example.com",
  "phone": "+251111234567",
  "head_of_dept": "head@example.com",
  "l1_role_level": "nodal_officer",
  "l2_role_level": "senior_nodal_officer",
  "routing_strategy": "Primary First"
}
```

| Field              | Required | Notes                                                                                  |
| :----------------- | :------- | :------------------------------------------------------------------------------------- |
| `department_name`  | yes      | Non-blank, up to 140 characters. Must be new. It becomes the department id             |
| `email_account`    | yes      | Valid email. Stored lowercase                                                          |
| `short_name`       | no       | Acronym. Must be unique, because it is also accepted as an id by other APIs            |
| `phone`            | no       | Blank means not set                                                                    |
| `head_of_dept`     | no       | User id. Must be an enabled Grievance Officer or Grievance Admin                       |
| `active`           | no       | Default `true`                                                                         |
| `l1_role_level`    | no       | A Grievance Role Level id, for example `nodal_officer`                                 |
| `l2_role_level`    | no       | A Grievance Role Level id, for example `senior_nodal_officer`                          |
| `routing_strategy` | no       | `Primary First`, `Round Robin` or `Least Loaded`. Blank leaves each desk's own setting |

Returns 200 with `message: "Department created"` and `data.department`. There is no 201. A name that already exists (ignoring case) returns 409.

## 7. Update a department, including the head

`PATCH /api/v1/departments/{department}`

Send only the fields that change. An omitted field stays as it is.

```json
{ "head_of_dept": "new.head@example.com" }
```

| Field              | Notes                                                                                                |
| :----------------- | :--------------------------------------------------------------------------------------------------- |
| `email_account`    | Valid email. Cannot be null                                                                          |
| `short_name`       | Null or blank clears it. Must stay unique                                                            |
| `phone`            | Null or blank clears it                                                                              |
| `head_of_dept`     | Assigns or reassigns the head. Null or blank clears it                                               |
| `active`           | `false` retires the department (section 8). `true` brings it back                                    |
| `l1_role_level`, `l2_role_level`, `routing_strategy` | Null or blank clears them                                          |

`department_name` is not editable: sending it returns 400, as does any field not listed here. An empty body returns 400 "No fields to update."

The head is checked only when it changes, so a head who is disabled later never blocks an unrelated edit. Reassigning the head only changes who `Dept Head` notifications reach. It does not touch officer desks.

Returns 200 with `message: "Department updated"` and the full `data.department`.

## 8. Retire a department

`DELETE /api/v1/departments/{department}`

No body. The department stays on record for the cases, assignments and templates that link to it, and is marked `active: false`. It then drops out of `GET /api/v1/grievances/options`. This is the same as `PATCH` with `{ "active": false }`.

Retiring is refused with 400 while the department has open cases (status Submitted, Assigned, In Progress or More Info Needed). The message says how many. Reassign or close them first. Repeating the call on a retired department is a no-op. Bring a department back with `PATCH { "active": true }`.

Retiring does not touch the department's category assignments or officer desks. Deactivate those separately if routing should stop.

## 9. Rules that produce a 400 or 409

| Rule                                                              | Status |
| :---------------------------------------------------------------- | :----- |
| Name, email or another required field missing or blank            | 400    |
| `email_account` is not a valid email address                      | 400    |
| `head_of_dept` is unknown, disabled, or not an officer or admin   | 400    |
| `routing_strategy` or a role level that does not exist            | 400    |
| An unknown field, or `department_name` on update                  | 400    |
| Retiring a department that has open cases                         | 400    |
| A department with that name already exists                        | 409    |
| The short name belongs to another department                      | 409    |

## 10. Suggested screen wiring

| Screen                                  | Call                                                                              |
| :-------------------------------------- | :-------------------------------------------------------------------------------- |
| Departments table                       | `GET /api/v1/departments?page=&page_size=&q=`, with an Active/Retired toggle on `active` |
| Add department modal                    | `POST /api/v1/departments`. Show 409 as "A department with this name already exists" |
| Edit department modal                   | `PATCH /api/v1/departments/{department_id}` with only the changed fields           |
| Head of department picker               | `GET /api/v1/officers?status=Active`, then send the officer's `name` as `head_of_dept` |
| Retire button                           | `DELETE /api/v1/departments/{department_id}`. Show `message` on 400                |
| Nodal Officer department dropdown       | `GET /api/v1/departments?active=true&page_size=100`, in place of the hardcoded list. Use `department_id` as the value |
| Category Assignments department picker  | The same call. Send `department_id` as `department`                               |

## 11. Not available yet

- Renaming a department. The name is the id, and renaming it would have to rewrite every grievance, assignment and template that links to it.
- Hard delete. A retired department is kept.
- Senior and nodal officer fields on the department. Officers are placed through category assignments and `POST /api/v1/officers`.
