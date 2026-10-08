# Service Category and Grievance Type API: frontend integration guide

Audience: frontend developers building the Categories and Grievance Types admin tab (STG-409).

These endpoints add create, edit and deactivate to the service categories and grievance types that `GET /api/v1/submitters/options` and `GET /api/v1/grievances/options` return as read-only lists. All example responses below are real output from the running API.

## 1. Quick reference

| #   | Method   | Endpoint                                   | Purpose                                    | Roles                 |
| :-- | :------- | :----------------------------------------- | :----------------------------------------- | :-------------------- |
| 1   | `GET`    | `/api/v1/service-categories`               | List categories (active and inactive)      | Admin, Review Officer |
| 2   | `POST`   | `/api/v1/service-categories`               | Create a category                          | Admin                 |
| 3   | `GET`    | `/api/v1/service-categories/{category}`    | Get one category                           | Admin, Review Officer |
| 4   | `PATCH`  | `/api/v1/service-categories/{category}`    | Edit a category                            | Admin                 |
| 5   | `DELETE` | `/api/v1/service-categories/{category}`    | Deactivate a category (and its types)      | Admin                 |
| 6   | `GET`    | `/api/v1/grievance-types`                  | List grievance types (active and inactive) | Admin, Review Officer |
| 7   | `POST`   | `/api/v1/grievance-types`                  | Create a type under a category             | Admin                 |
| 8   | `GET`    | `/api/v1/grievance-types/{grievance_type}` | Get one type                               | Admin, Review Officer |
| 9   | `PATCH`  | `/api/v1/grievance-types/{grievance_type}` | Edit a type                                | Admin                 |
| 10  | `DELETE` | `/api/v1/grievance-types/{grievance_type}` | Deactivate a type                          | Admin                 |

**Admin** means Grievance Admin, System Manager or Administrator. **Review Officer** is the read-only Grievance Review Officer role. Every other role, and guests, get 403.

## 2. Conventions

| Item         | Value                                                                                     |
| :----------- | :---------------------------------------------------------------------------------------- |
| Base URL     | `{base_url}`, for example `https://grievance.example.org`                                 |
| Auth header  | `Authorization: Bearer <access_token>` (from `POST /api/v1/auth/login`)                   |
| Content type | `Content-Type: application/json` for `POST` and `PATCH`                                   |
| Category id  | In a path, the category **name** or its **ticket code**. Wrap it in `encodeURIComponent`. |
| Type id      | `grievance_type_id`, for example `GTYPE-02169`                                            |
| Strictness   | Unknown body fields and unknown query parameters return 400                               |

### Success envelope

```json
{
  "status": "success",
  "message": "Service category created",
  "data": {},
  "meta": { "api_version": "v1", "status": "current" },
  "request_id": "07994a2f-1c09-4a71-aa78-c27a421a1ef0"
}
```

List responses put the page info **inside `data`**, next to the array: `data.pagination`.

```json
"pagination": { "page": 1, "page_size": 20, "total_count": 1, "total_pages": 1, "has_next": false, "has_prev": false }
```

### Error envelope

```json
{
  "status": "error",
  "message": "Validation failed",
  "code": "VALIDATION_ERROR",
  "details": { "category_name": "String should have at least 1 character" },
  "meta": { "api_version": "v1", "status": "current" },
  "request_id": "07994a2f-1c09-4a71-aa78-c27a421a1ef0"
}
```

`details` maps field names to messages for schema errors and is `{}` for business-rule errors. Show `message` to the user in that case.

| HTTP | `code`              | Meaning                                                                                    |
| :--- | :------------------ | :----------------------------------------------------------------------------------------- |
| 400  | `VALIDATION_ERROR`  | Bad or unknown field, invalid ticket code, locked code, inactive parent, protected default |
| 403  | `PERMISSION_DENIED` | The caller's role cannot do this                                                           |
| 404  | `NOT_FOUND`         | The category or type in the path does not exist                                            |
| 409  | `DUPLICATE_ENTRY`   | The name or code is already taken                                                          |

## 3. Business rules the UI should reflect

- **Nothing is deleted.** `DELETE` sets `is_active` to `false`. Grievances already filed keep their category and type.
- **Ticket code** (category `code`): exactly 3 characters from `0-9 A-Z` without `I`, `L`, `O`, `U`. Stored in capitals, unique. It **cannot change once tickets exist** under the category. Records show `code_locked: true`: disable the field.
- **Type code**: up to 30 letters, digits, hyphens or underscores, starting with a letter or digit. Stored in capitals. Unique **within its category** (two categories may both have `OTHER`).
- **Type name**: unique within its category.
- **Deactivating a category** also deactivates all its types. Reactivating the category does **not** bring them back: reactivate each type.
- **A type** cannot be created or reactivated while its category is inactive, and **cannot move to another category**.
- **Renaming a category** is safe: every grievance, type, routing rule, SLA row and template follows. The ticket code does not change. Path ids that used the old name stop working, so use the name in the response afterwards.
- **Default category.** Exactly one category is the default: the one cases with no category are filed under (an IVR call or email that named none, or a draft saved without a choice). It starts as `Other`. `is_default` is `true` on that record only.
  - Make a category the default with `is_default: true` on create or `PATCH`. The previous default loses the flag automatically. The new default also gets an active catch-all type named `Other` if it has none.
  - The default is always active. It cannot be deactivated, and `is_default: false` is refused. To change it, promote another category.
  - It can be renamed. The flag stays with it.
  - Its catch-all `Other` type cannot be deactivated while the category is the default.
- **Other screens.** The submission wizard dropdowns list active records only (types now include `code`). A new grievance cannot be filed under an inactive category or type. Category assignments and response templates already set up on a category you deactivate stay active so open cases keep working; new ones cannot be set up on it.
- **Warn before retiring.** Each category record counts what refers to it: `grievance_type_count` (active types), `assignment_count` (active category assignments), `response_template_count` (active templates) and `grievance_count`. Show these in a confirm dialog.

## 4. Service categories

### 4.1 List service categories

`GET /api/v1/service-categories`

Ordered by `sort_order`, then name. Returns active and inactive unless filtered.

| Query       | Type    | Required | Default | Notes                                                      |
| :---------- | :------ | :------- | :------ | :--------------------------------------------------------- |
| `is_active` | boolean | no       | both    | `true`/`false` (also `1`/`0`). Omit or leave blank for all |
| `search`    | string  | no       |         | Text contained in the name or the code, case-insensitive   |
| `page`      | integer | no       | 1       | 1 or more                                                  |
| `page_size` | integer | no       | 20      | 1 to 100                                                   |

```
GET /api/v1/service-categories?search=Infra&is_active=true&page=1&page_size=5
```

Response `200`:

```json
{
  "status": "success",
  "message": "Service categories retrieved",
  "data": {
    "service_categories": [
      {
        "category_name": "Infrastructure",
        "code": "007",
        "sort_order": 7,
        "is_active": true,
        "is_default": false,
        "grievance_type_count": 1,
        "assignment_count": 0,
        "response_template_count": 0,
        "grievance_count": 0,
        "code_locked": false
      }
    ],
    "pagination": {
      "page": 1,
      "page_size": 5,
      "total_count": 1,
      "total_pages": 1,
      "has_next": false,
      "has_prev": false
    }
  },
  "meta": { "api_version": "v1", "status": "current" },
  "request_id": "07994a2f-1c09-4a71-aa78-c27a421a1ef0"
}
```

### 4.2 Create a service category

`POST /api/v1/service-categories`

| Body field      | Type    | Required | Default       | Notes                                                     |
| :-------------- | :------ | :------- | :------------ | :-------------------------------------------------------- |
| `category_name` | string  | yes      |               | Up to 140 characters, unique, whitespace trimmed          |
| `code`          | string  | yes      |               | 3-character ticket code (see section 3). Unique           |
| `sort_order`    | integer | no       | last position | 0 or more. Omit to place the category at the end          |
| `is_active`     | boolean | no       | `true`        | Must be `true` when `is_default` is `true`                |
| `is_default`    | boolean | no       | `false`       | `true` makes this the default and clears the previous one |

```json
{ "category_name": "Infrastructure", "code": "007", "sort_order": 7, "is_active": true, "is_default": false }
```

Response `200`: `data.service_category` is the record.

```json
{
  "status": "success",
  "message": "Service category created",
  "data": {
    "service_category": {
      "category_name": "Infrastructure",
      "code": "007",
      "sort_order": 7,
      "is_active": true,
      "is_default": false,
      "grievance_type_count": 0,
      "assignment_count": 0,
      "response_template_count": 0,
      "grievance_count": 0,
      "code_locked": false
    }
  },
  "meta": { "api_version": "v1", "status": "current" },
  "request_id": "07994a2f-1c09-4a71-aa78-c27a421a1ef0"
}
```

Errors: `409` when the name or code is already used; `400` for a bad code (for example `OIL`: "Ticket codes exclude I, L, O and U") or a missing or blank field.

### 4.3 Get a service category

`GET /api/v1/service-categories/{category}`

`{category}` is the name or the ticket code, for example `/service-categories/007` or `/service-categories/Infrastructure`.

Response `200`: same record as 4.2 under `data.service_category`, message `Service category retrieved`. `404 NOT_FOUND` when unknown.

### 4.4 Edit a service category

`PATCH /api/v1/service-categories/{category}`

Send **only the fields that change**. An empty body returns `400` "No fields to update."

| Body field      | Type    | Notes                                                                                                                     |
| :-------------- | :------ | :------------------------------------------------------------------------------------------------------------------------ |
| `category_name` | string  | Renames the category everywhere (the default included)                                                                    |
| `code`          | string  | Rejected with 400 when `code_locked` is `true`                                                                            |
| `sort_order`    | integer | 0 or more                                                                                                                 |
| `is_active`     | boolean | `false` also deactivates the types. Refused for the default category                                                      |
| `is_default`    | boolean | `true` makes this the default (the previous default loses the flag). `false` is refused: promote another category instead |

```json
{ "sort_order": 3 }
```

To make a category the default, send `{ "is_default": true }`. The response is the updated record with `"is_default": true`; the previous default now reads `false`.

Response `200`: the updated record under `data.service_category`, message `Service category updated`.

```json
{
  "status": "success",
  "message": "Service category updated",
  "data": {
    "service_category": {
      "category_name": "Infrastructure",
      "code": "007",
      "sort_order": 3,
      "is_active": true,
      "is_default": false,
      "grievance_type_count": 1,
      "assignment_count": 0,
      "response_template_count": 0,
      "grievance_count": 0,
      "code_locked": false
    }
  },
  "meta": { "api_version": "v1", "status": "current" },
  "request_id": "07994a2f-1c09-4a71-aa78-c27a421a1ef0"
}
```

Errors: `400` (unknown field, locked code, deactivating or un-defaulting the default), `404`, `409` (name or code taken).

### 4.5 Deactivate a service category

`DELETE /api/v1/service-categories/{category}`

No body. Same as `PATCH` with `{ "is_active": false }`. Repeating the call changes nothing. The category's types are deactivated too (`grievance_type_count` becomes 0).

Response `200`:

```json
{
  "status": "success",
  "message": "Service category deactivated",
  "data": {
    "service_category": {
      "category_name": "Infrastructure",
      "code": "007",
      "sort_order": 3,
      "is_active": false,
      "is_default": false,
      "grievance_type_count": 0,
      "assignment_count": 0,
      "response_template_count": 0,
      "grievance_count": 0,
      "code_locked": false
    }
  },
  "meta": { "api_version": "v1", "status": "current" },
  "request_id": "07994a2f-1c09-4a71-aa78-c27a421a1ef0"
}
```

To bring a category back, use `PATCH` with `{ "is_active": true }`.

## 5. Grievance types

### 5.1 List grievance types

`GET /api/v1/grievance-types`

Ordered by name. Returns active and inactive unless filtered.

| Query              | Type    | Required | Default | Notes                                                      |
| :----------------- | :------ | :------- | :------ | :--------------------------------------------------------- |
| `service_category` | string  | no       | all     | Category name or ticket code. Unknown category returns 400 |
| `is_active`        | boolean | no       | both    | `true`/`false`                                             |
| `search`           | string  | no       |         | Text contained in the type name or code, case-insensitive  |
| `page`             | integer | no       | 1       | 1 or more                                                  |
| `page_size`        | integer | no       | 20      | 1 to 100                                                   |

```
GET /api/v1/grievance-types?service_category=Infrastructure&is_active=true
```

Response `200`:

```json
{
  "status": "success",
  "message": "Grievance types retrieved",
  "data": {
    "grievance_types": [
      {
        "grievance_type_id": "GTYPE-02169",
        "type_name": "Road access blocked",
        "code": "ROAD_ACCESS",
        "service_category": "Infrastructure",
        "is_active": true,
        "grievance_count": 0
      }
    ],
    "pagination": {
      "page": 1,
      "page_size": 20,
      "total_count": 1,
      "total_pages": 1,
      "has_next": false,
      "has_prev": false
    }
  },
  "meta": { "api_version": "v1", "status": "current" },
  "request_id": "07994a2f-1c09-4a71-aa78-c27a421a1ef0"
}
```

### 5.2 Create a grievance type

`POST /api/v1/grievance-types`

| Body field         | Type    | Required | Default | Notes                                                                   |
| :----------------- | :------ | :------- | :------ | :---------------------------------------------------------------------- |
| `service_category` | string  | yes      |         | Parent category, by name or ticket code. Must be active                 |
| `type_name`        | string  | yes      |         | Up to 140 characters. Unique within the category                        |
| `code`             | string  | yes      |         | Up to 30 characters (section 3). Unique within the category. Uppercased |
| `is_active`        | boolean | no       | `true`  |                                                                         |

```json
{ "service_category": "Infrastructure", "type_name": "Road access blocked", "code": "ROAD_ACCESS" }
```

Response `200`: `data.grievance_type` is the record.

```json
{
  "status": "success",
  "message": "Grievance type created",
  "data": {
    "grievance_type": {
      "grievance_type_id": "GTYPE-02169",
      "type_name": "Road access blocked",
      "code": "ROAD_ACCESS",
      "service_category": "Infrastructure",
      "is_active": true,
      "grievance_count": 0
    }
  },
  "meta": { "api_version": "v1", "status": "current" },
  "request_id": "07994a2f-1c09-4a71-aa78-c27a421a1ef0"
}
```

Errors: `409` when the name or code already exists in that category; `400` when the category does not exist or is inactive, or the code format is wrong.

### 5.3 Get a grievance type

`GET /api/v1/grievance-types/{grievance_type}`

Response `200`: the record under `data.grievance_type`, message `Grievance type retrieved`. `404 NOT_FOUND` when unknown.

### 5.4 Edit a grievance type

`PATCH /api/v1/grievance-types/{grievance_type}`

Send only the fields that change. `service_category` is **not accepted**: a type stays in the category it was created in.

| Body field  | Type    | Notes                                                                                                                   |
| :---------- | :------ | :---------------------------------------------------------------------------------------------------------------------- |
| `type_name` | string  | Unique within the category                                                                                              |
| `code`      | string  | Unique within the category. Uppercased                                                                                  |
| `is_active` | boolean | `true` is refused (400) while the category is inactive. The default category's catch-all `Other` type cannot be `false` |

```json
{ "code": "road_access_2" }
```

Response `200` (note the code comes back uppercased):

```json
{
  "status": "success",
  "message": "Grievance type updated",
  "data": {
    "grievance_type": {
      "grievance_type_id": "GTYPE-02169",
      "type_name": "Road access blocked",
      "code": "ROAD_ACCESS_2",
      "service_category": "Infrastructure",
      "is_active": true,
      "grievance_count": 0
    }
  },
  "meta": { "api_version": "v1", "status": "current" },
  "request_id": "07994a2f-1c09-4a71-aa78-c27a421a1ef0"
}
```

### 5.5 Deactivate a grievance type

`DELETE /api/v1/grievance-types/{grievance_type}`

No body. Same as `PATCH` with `{ "is_active": false }`. Repeating the call changes nothing. Response `200` has message `Grievance type deactivated` and the record with `"is_active": false`.

## 6. Example error responses

Duplicate name (`409`):

```json
{
  "status": "error",
  "message": "Grievance Service Category Infrastructure already exists",
  "code": "DUPLICATE_ENTRY",
  "details": {},
  "meta": { "api_version": "v1", "status": "current" },
  "request_id": "07994a2f-1c09-4a71-aa78-c27a421a1ef0"
}
```

Invalid ticket code (`400`):

```json
{
  "status": "error",
  "message": "Service category Bad has code OIL, which uses O, I, L. Ticket codes exclude I, L, O and U.",
  "code": "VALIDATION_ERROR",
  "details": {},
  "meta": { "api_version": "v1", "status": "current" },
  "request_id": "07994a2f-1c09-4a71-aa78-c27a421a1ef0"
}
```

Bad request body (`400`): `details` lists each field.

```json
{
  "status": "error",
  "message": "Validation failed",
  "code": "VALIDATION_ERROR",
  "details": {
    "category_name": "String should have at least 1 character",
    "extra": "Extra inputs are not permitted"
  },
  "meta": {},
  "request_id": "07994a2f-1c09-4a71-aa78-c27a421a1ef0"
}
```

Type under an inactive category (`400`):

```json
{
  "status": "error",
  "message": "Service category 'Infrastructure' is inactive. Reactivate it before adding or enabling its types.",
  "code": "VALIDATION_ERROR",
  "details": {},
  "meta": { "api_version": "v1", "status": "current" },
  "request_id": "07994a2f-1c09-4a71-aa78-c27a421a1ef0"
}
```

Not found (`404`):

```json
{
  "status": "error",
  "message": "Service category 'Nope' was not found.",
  "code": "NOT_FOUND",
  "details": {},
  "meta": { "api_version": "v1", "status": "current" },
  "request_id": "07994a2f-1c09-4a71-aa78-c27a421a1ef0"
}
```

Wrong role (`403`):

```json
{
  "status": "error",
  "message": "Only Grievance Admin, System Manager, Administrator, Grievance Review Officer can perform this action.",
  "code": "PERMISSION_DENIED",
  "details": {},
  "meta": { "api_version": "v1", "status": "current" },
  "request_id": "07994a2f-1c09-4a71-aa78-c27a421a1ef0"
}
```

Other messages you may see in `message` with `400`: "'Other' is the default category, so it cannot be inactive. Make another category the default first.", "Exactly one category must be the default. Make another category the default instead." and "Ticket code of '...' cannot change: tickets have already been issued under it."

## 7. TypeScript types

These extend `ServiceCategoryOption` and `GrievanceTypeOption` in `src/features/metadata/types.ts`.

```ts
export interface ServiceCategoryRecord extends ServiceCategoryOption {
  is_active: boolean;
  is_default: boolean; // the category unclassified cases are filed under; exactly one
  grievance_type_count: number; // active types
  assignment_count: number; // active category assignments
  response_template_count: number; // active response templates
  grievance_count: number;
  code_locked: boolean; // true once tickets exist: disable editing `code`
}

export interface GrievanceTypeRecord extends GrievanceTypeOption {
  code: string;
  is_active: boolean;
  grievance_count: number;
}

export interface Pagination {
  page: number;
  page_size: number;
  total_count: number;
  total_pages: number;
  has_next: boolean;
  has_prev: boolean;
}

export interface CreateServiceCategoryBody {
  category_name: string;
  code: string;
  sort_order?: number;
  is_active?: boolean;
  is_default?: boolean;
}

export type UpdateServiceCategoryBody = Partial<{
  category_name: string;
  code: string;
  sort_order: number;
  is_active: boolean;
  is_default: true; // false is refused: promote another category
}>;

export interface CreateGrievanceTypeBody {
  service_category: string;
  type_name: string;
  code: string;
  is_active?: boolean;
}

export type UpdateGrievanceTypeBody = Partial<{
  type_name: string;
  code: string;
  is_active: boolean;
}>;

export interface ServiceCategoryListQuery {
  is_active?: boolean;
  search?: string;
  page?: number;
  page_size?: number;
}

export interface GrievanceTypeListQuery extends ServiceCategoryListQuery {
  service_category?: string;
}

export interface ApiError {
  status: "error";
  message: string;
  code: "VALIDATION_ERROR" | "PERMISSION_DENIED" | "NOT_FOUND" | "DUPLICATE_ENTRY";
  details: Record<string, string>;
  request_id: string;
}
```

## 8. Integration notes

- The wizard dropdown calls (`/submitters/options`, `/grievances/options`) now return `code` on each grievance type and still return **active records only**. Refetch them after an admin change.
- After a rename, take the id from the response (`category_name`) for later calls.
- `PATCH` sends only changed fields. Do not send the whole form back: an unchanged locked `code` is fine to omit, and sending a different one is an error.
- A Postman collection (folder "Service Categories & Grievance Types") and the OpenAPI spec (`openapi/openapi_v1.yaml`) carry the same endpoints.
- Existing grievance types were given a code made from their name, for example `Fertilizer Shortage` became `FERTILIZER_SHORTAGE`. Admins can edit these.
