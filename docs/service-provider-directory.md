# Service Provider Directory

Audience: backend and frontend developers working on the Submit Grievance form (STG-454).

The directory is a local list of the banks, cooperatives, input stores and markets a grievance can be about. The form's free-text "Service Provider / Branch / Office Name" field becomes a searchable dropdown over it. Search reads this table only and never calls an external source.

## 1. Status

| Part                                                                  | State                                        |
| :-------------------------------------------------------------------- | :------------------------------------------- |
| `Grievance Service Provider` DocType                                  | Shipped (STG-454)                            |
| `GET /api/v1/service-providers` (search), `GET .../{provider_id}`     | Shipped (STG-454)                            |
| `Grievance Service Provider Source` DocType and the one-off sync (§6) | Built, waiting on the A2C connection details |
| `GET /api/v1/service-provider-sources`, `POST .../{source_name}/sync` | Built                                        |
| Daily scheduled sync of every enabled source (§6)                     | Built                                        |
| `Grievance.associated_service_provider` as a Link, and the migration  | Not built                                    |
| Admin endpoints to create or deactivate a provider by hand            | Not built                                    |
| Dropdown in `oan_grievance_ui`                                        | Not built                                    |

The directory fills from a source by running the sync (§6), by hand or by the daily job. Until the A2C connection is configured and enabled it is empty, and nothing reads it yet apart from the two search endpoints.

## 2. Rules

- The field on the grievance form stays optional. If the provider is not listed, the submitter leaves it empty and describes the provider in the description.
- A grievance will store the **provider ID** (`SP-00042`), never the name, so renaming a provider changes nothing on old grievances.
- No role can delete a provider by hand: deactivate it instead. Only the sync removes providers, and only ones its source dropped that nothing links to (§6), so a grievance that names a provider still resolves.

## 3. Data model

DocType `Grievance Service Provider`, module Grievance Masters. The ID is `SP-{#####}` and never changes.

| Field                 | Type                                                | Notes                                                                                             |
| :-------------------- | :-------------------------------------------------- | :------------------------------------------------------------------------------------------------ |
| `provider_name`       | Data, required, indexed                             | Display name. Trimmed on save                                                                     |
| `provider_type`       | Link to `Grievance Service Provider Type`, required | Which kind of provider it is. The list of types is data (§3.1), not fixed in the code             |
| `parent_provider`     | Link to this DocType                                | The head office of a branch. A provider cannot be its own parent and the chain cannot loop        |
| `administrative_area` | Link to `Grievance Administrative Area`             | Where it operates. **Empty means nationwide**                                                     |
| `registered_region`   | Data                                                | The region text exactly as the source gave it. Kept even when it matches no `administrative_area` |
| `registration_number` | Data, indexed                                       | Searchable                                                                                        |
| `logo`                | Small Text                                          | As the source gave it, often a path relative to the source's own host                             |
| `is_active`           | Check, default 1                                    | Inactive providers are hidden from search but still resolve by ID                                 |
| `source`              | Link to the Source DocType, read-only               | Empty means the provider was added by hand                                                        |
| `external_ref`        | Data, read-only                                     | The source's own ID, such as `PB-0452`. Unique together with `source`                             |
| `last_synced_at`      | Datetime, read-only                                 | When the sync last confirmed the provider                                                         |

A provider that has a `source` belongs to that source: its name, type, parent, area, region text, logo and registration number are changed at the source, and only `is_active` can be edited by hand. The sync is the only writer of the other fields.

### 3.1 Provider types

The kinds of provider are the records of a small master, `Grievance Service Provider Type` (named by `type_name`, with an `is_active` flag and a description), not a list typed into a field. `provider_type` on a provider and `default_provider_type` on a source are links to it, so Frappe checks that the type exists.

- The first set is seeded by `bench migrate` from `SERVICE_PROVIDER_TYPES` in `setup/install.py`, the one place the names are listed: Bank, Microfinance, Cooperative, Cooperative Union, Input Store, Market, Government Office and Other. Seeding never overwrites an administrator's changes.
- An administrator adds a type by creating a record; nothing in the code or the DocType JSON has to change, and the new name works at once as a `provider_type` value on the search endpoint.
- A type is retired with `is_active = 0`, never deleted (no role holds `delete`). A retired type cannot be given to a new or changed provider or source, but providers that already have it keep it.

Things worth knowing:

- There is a composite index on `(is_active, provider_name)` for the dropdown.
- Area filtering joins the area table and uses its Nested Set bounds (`lft`, `rgt`). The attached design copied the bounds onto each provider, but Frappe renumbers `lft` and `rgt` whenever the tree changes, so a copy would go stale.
- Read access is given to the Grievance Officer, Submitter, Assisted Submitter and Review Officer roles. Create and write are for Grievance Admin and System Manager. The search endpoint has its own guard (§4).

## 4. API

The endpoints follow the platform conventions: `@route`, `@handle_api_errors`, the guard, `@validate_request`, then `@api_doc`; the standard success envelope; unknown query parameters return 400. They are not exposed under `/api/method/...`.

| #   | Method | Endpoint                                  | Purpose                     |
| :-- | :----- | :---------------------------------------- | :-------------------------- |
| 1   | `GET`  | `/api/v1/service-providers`               | Search active providers     |
| 2   | `GET`  | `/api/v1/service-providers/{provider_id}` | One provider, active or not |

**Access.** Any signed-in user who holds `create` on `Grievance` (Grievance Admin, System Manager, Submitter, Assisted Submitter). A guest gets 401 from the JWT middleware. A signed-in user without that permission gets 403. A Grievance Officer cannot file a grievance, so cannot use these endpoints today.

**Gateway tier.** `officer-core` (300 requests a minute per consumer), chosen so a debounced type-ahead cannot hit the 60 a minute `citizen-intake` limit. Change `tier=` on the `@route` if you disagree.

### 4.1 Search

`GET /api/v1/service-providers`

| Param           | Type   | Default | Notes                                                                                                                        |
| :-------------- | :----- | :------ | :--------------------------------------------------------------------------------------------------------------------------- |
| `search`        | string |         | Case-insensitive match on the name, the registration number or the parent's name. 2 to 100 characters. Blank means no search |
| `provider_type` | string |         | The name of a provider type (§3.1), for example `Bank`. An unknown name is a 400                                             |
| `area`          | string |         | Area ID or path code. See below                                                                                              |
| `page`          | int    | 1       | 1 or more                                                                                                                    |
| `page_size`     | int    | 20      | 1 to 50                                                                                                                      |

Only active providers are returned.

**Area filter.** With `area` set, a provider matches if its area is inside that area, or is an ancestor of it, or if it has no area at all (nationwide). A kebele-level user therefore sees kebele, woreda, zone and region providers, the nationwide ones, and none from a sibling kebele or another region. The spec said "inside or an ancestor"; keeping nationwide providers is an addition, because a provider whose area could not be mapped would otherwise vanish from every filtered search.

**Order.** Names that start with the search text first, then the most local area (deepest in the tree), then nationwide providers, then name, then ID. `%` and `_` in the search text are matched literally.

Example, `GET /api/v1/service-providers?search=cbe&provider_type=Bank&area=zone-ET0314&page_size=5` (real output; IDs differ per site):

```json
{
  "status": "success",
  "message": "Service providers retrieved",
  "data": {
    "items": [
      {
        "id": "SP-00002",
        "name": "CBE - Bahir Dar Branch",
        "type": "Bank",
        "parent": { "id": "SP-00001", "name": "Commercial Bank of Ethiopia" },
        "area": { "id": "zone-ET0314", "name": "Bahir Dar", "path_label": "Amhara › Bahir Dar" },
        "registration_number": null,
        "external_ref": null,
        "registered_region": null,
        "logo": null
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
  }
}
```

`path_label` runs from the region down to the area (country is left out), joined with `›`.

`external_ref`, `registered_region` and `logo` carry what the source sent, under A2C's own names, so a response can be checked against the source. They are `null` for a hand-made provider (the example above was added by hand). For a synced A2C bank:

| A2C `data.banks[]`  | GET `items[]`                                              |
| :------------------ | :--------------------------------------------------------- |
| `name` (`PB-0308`)  | `external_ref`                                             |
| `bank_name`         | `name`                                                     |
| `logo`              | `logo`                                                     |
| `registered_region` | `registered_region`, and `area` when the name matches      |
| none                | `id` (`SP-#####`), `type`, `parent`, `registration_number` |

Checked on 2026-10-21 against the A2C dev backend: all 14 banks matched field for field.

### 4.2 One provider

`GET /api/v1/service-providers/{provider_id}`

Returns the same item as one search result, as `data`, plus `"is_active": true|false`. It returns inactive providers too, so a saved grievance can still show the name.

### 4.3 Errors

| HTTP | `code`                 | When                                                                                                                                                            |
| :--- | :--------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 400  | `VALIDATION_ERROR`     | `search` under 2 or over 100 characters, `page_size` over 50, a `provider_type` that is not a known type ("Provider type X does not exist."), unknown parameter |
| 401  | `AUTHENTICATION_ERROR` | Not signed in                                                                                                                                                   |
| 403  | `PERMISSION_DENIED`    | No `create` permission on `Grievance`                                                                                                                           |
| 404  | `NOT_FOUND`            | Unknown `provider_id` or unknown `area`                                                                                                                         |

The attached design named the 401 and 403 codes `UNAUTHORIZED` and `FORBIDDEN`. The platform's own vocabulary is used here, because clients already branch on it.

### 4.4 Sources and the one-off sync

For administrators (Grievance Admin, System Manager, Administrator). A signed-in user without one of those roles gets 403.

> **TODO (STG-454): the sync endpoint (#4) is temporary.** Its path and contract will change. The daily job (§6) is what normally feeds the directory, so build nothing in the UI that depends on #4 staying as it is. When it changes, update the route in `api/v1/service_provider_source.py`, regenerate `openapi/`, `apisix/` and `postman/`, and revise this section.

| #   | Method | Endpoint                                              | Purpose                                         |
| :-- | :----- | :---------------------------------------------------- | :---------------------------------------------- |
| 3   | `GET`  | `/api/v1/service-provider-sources`                    | Each source, its last sync result and counts    |
| 4   | `POST` | `/api/v1/service-provider-sources/{source_name}/sync` | Queue a sync of the whole source. Answers `202` |

`GET` returns `data.items`, each with `name`, `source_type`, `is_enabled`, `api_base_url`, `credentials_configured` (true or false, never the secret), `last_synced_at`, `last_sync_status` (`Success`, `Partial` or `Failed`), `last_sync_message`, `provider_count` and `active_provider_count`.

`POST` takes no body and returns `{"data": {"source": "A2C Banks", "job_id": "...", "queued": true}}`. The work runs in the background on the `long` queue, so the call returns at once; read the result from the source with `GET` (§6). Errors: 404 `NOT_FOUND` for an unknown source, 400 `VALIDATION_ERROR` for a disabled one, and 409 `DUPLICATE_ENTRY` ("A sync for this source is already running") when a run of that source is already queued or running. The design named the 409 `SYNC_IN_PROGRESS`; the platform has one code per HTTP status and clients branch on it.

## 5. Tests

- `grievance_masters/doctype/grievance_service_provider/test_grievance_service_provider.py`: ID series, defaults, trimming, required and valid type, parent chain (self and loop), no delete permission, and that the type constant matches the DocType options.
- `tests/test_service_provider_api.py`: name, registration number and parent-name search; case; wildcards; blank search; type filter; prefix and locality ordering; area filter (ancestors, subtree, siblings, other regions, nationwide); response shape and path label; paging; invalid input; inactive lookup; 404s; access (submitter allowed, no-role user and guest refused, middleware 401).

- `grievance_masters/doctype/grievance_service_provider_source/test_grievance_service_provider_source.py`: https-only URL, no credentials in the URL, paths, an incomplete source cannot be enabled, login and token modes, the secret stays encrypted, the seeded source, synced providers locked to `is_active`, and the `(source, external_ref)` constraint.
- `tests/test_service_provider_sync.py`: create, no-op re-run, rename and move keeping the ID, removal of dropped providers (archived, survivors keep their IDs), deactivation instead when still in use, a returning provider, other sources and hand-made providers untouched, empty list refused, unknown regions, bad items, partial runs, every failure of the source (refused login, unreachable, unusable answers) with no secret in the message, rollback on a crash, queueing and the 409, and the two endpoints with their access rules. Only the A2C HTTP calls are mocked.

## 6. The sync

One sync reads the whole source and brings the directory in line with it. It runs in two ways: on demand through `POST /api/v1/service-provider-sources/{source_name}/sync`, and once a day from the scheduler.

**The daily job.** `tasks.enqueue_service_provider_syncs` (registered under `daily` in `hooks.py`) queues a sync of every enabled source on the `long` queue and returns. Frappe's `daily` event is the cron expression `0 0 * * *`: **midnight in the site's timezone** (System Settings), picked up on the next scheduler tick, which is at most 4 minutes later by default. On the dev site the timezone is Asia/Kolkata, so the job runs at about 00:00 IST (18:30 UTC). Frappe registers the job when `bench migrate` runs, and the scheduler and a background worker must be running. A source whose last run is still going is skipped until the next day, and a source that cannot be queued is written to the Error Log without stopping the others. The scheduler and the endpoint queue the same job under the same ID per source, so they can never run the same source at once. To keep a source out of the schedule, disable it.

**No duplicate data.** A provider is identified by the source's own ID (for A2C, `PB-####`), never by its name:

- The table has a unique constraint on `(source, external_ref)`, so even two overlapping runs cannot store a bank twice.
- Every run looks up the existing row first and updates it, so running the job again and again leaves one row per bank, with the same `SP-` ID.
- IDs are compared without regard to case or padding, as the database does: `pb-1` and `PB-1` are the bank `PB-1`. A list that repeats an ID is stored once, with a warning.
- Two different IDs with the same name are two providers, because A2C treats them as two banks (dev has two called `legel entity`). They are not merged; merging would lose the link from a `PB-####` ID to its directory row.
- The same ID in two different sources is kept once per source.

| In the source                           | In the directory                                                                     |
| :-------------------------------------- | :----------------------------------------------------------------------------------- |
| A provider not seen before              | Created, with the source's type, the area if the region name matches, and `SP-#####` |
| A provider whose name or region changed | Updated; the `SP-` ID stays the same                                                 |
| A provider that is no longer listed     | Removed, unless something still links to it (then deactivated)                       |
| A provider that was dropped and is back | Added again as a new row, with a new `SP-` ID                                        |
| Nothing at all (an empty answer)        | The run fails and nothing is changed                                                 |

**After every run the directory holds what the source holds, and nothing older.** A provider still in the source is updated in place and keeps its ID: a grievance stores the `SP-` ID, so IDs cannot be re-issued on every run. Deleting everything and inserting it again would do exactly that, and a grievance saved on Monday would point at a record that no longer exists on Tuesday.

A removed provider is archived by Frappe in the Deleted Document log, where an administrator can restore it. Frappe refuses to delete a record that another record links to, so a provider with a branch under it (and, once the field becomes a Link, one a grievance names) is deactivated instead and stays resolvable. The run message counts both: `removed` and `deactivated (still in use)`.

A source with a hand-made provider, or another source's providers, is never touched. If the source still lists a provider an admin deactivated by hand, the next sync reactivates it: the source is the truth for the providers it owns.

How a run ends:

- **Success.** Everything was saved. Warnings can still be present, such as a region that matches no administrative area (the provider is kept, with no area) or an item with no ID.
- **Partial.** Some providers failed validation (for example a name over 140 characters). The others were saved, and a failed provider that is still listed is not removed.
- **Failed.** The source could not be read (login refused, unreachable, unusable answer, empty list) or something unexpected broke. Nothing was changed. The message names what failed without quoting the source's answer, so no token or password can end up in it.

The result is written on the source (`last_synced_at`, `last_sync_status`, `last_sync_message`) and shown by `GET /api/v1/service-provider-sources`. Unexpected errors also go to the Error Log.

### 6.1 Setting up the A2C source

A source called `A2C Banks` is created disabled on every `bench migrate`, with the A2C catalog's paths and field names already filled in (login `/v1/auth/login`, list `/v1/catalog/facets`, list key `banks`, ID `name`, name `bank_name`, area `registered_region`, logo `logo`, type `Bank`). It has no URL and no credentials, so nothing runs until a site operator completes it.

1. As System Manager, open the `Grievance Service Provider Source` record `A2C Banks` in Desk. No app role has Desk access, so this is a site-operator task.
2. Set **API Base URL** (https only, no trailing slash; the dev backend is `https://a2c-backend-development.oanstaging.com/api`).
3. Set the credentials. **Login** mode needs a username and password and signs in on every run, reading the token from `data.token`. **Token** mode needs one long-lived bearer token.
4. Tick **Enabled**. The record will not save as enabled while the URL, paths or credentials are missing.
5. `POST /api/v1/service-provider-sources/A2C Banks/sync` as an administrator, then `GET /api/v1/service-provider-sources` to read the result.

Credentials are stored in encrypted Password fields and are never returned by the API.

**Waiting on the A2C connection details.** The sync uses a login or a token that A2C accepts. A Grievance-issued token is rejected by A2C today (§6.2), so use a dedicated A2C service account, not a person's login: a login token lasts 15 minutes, which the Login mode handles by signing in again on each run. If A2C is given a long-lived service token, switch the source to Token mode. Only the source's fields change; no code does.

Run against the A2C development backend on 2026-10-21, the sync created 14 banks and a second run changed nothing (rolled back afterwards). The six banks with a real region got an area; the dev placeholders `region` and `Test4` matched none and got a warning.

### 6.2 What was learned about the A2C source

The first source is the A2C bank catalog, not the "All Providers" table the design assumed. The A2C service already exposes bank data over HTTP:

| Endpoint                         | What it gives                                                                                                                                            |
| :------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /v1/catalog/facets`         | A `banks` array of **active banks only**. Each item has `name` (the `PB-####` ID), `bank_name`, `logo` and `registered_region`. **Needs a bearer token** |
| `GET /v1/catalog/banks/{bankId}` | Details of one bank. It cannot list banks                                                                                                                |

So the first sync is an **API** source, and only `facets` can enumerate. What that means for the design:

- **Reference.** `external_ref` = `name` (`PB-####`). `provider_name` = `bank_name`. `provider_type` is always `Bank`: this source holds no cooperatives, input stores or markets, which need another source or manual entry.
- **No branches.** There is no parent field, so no head-office and branch pairs from this source.
- **Removal by absence.** The list shows only active banks and has no `updated_at`. A bank that drops off the list looks the same as a deleted one, so every run is a full sync and anything missing is removed. The list is small, so this is cheap.
- **Area.** `registered_region` is a region name, not a code. `services/resolvers.resolve_administrative_area` already matches a Region by its display name, which is unique across the country, so open point 3 of the design is mostly answered. Names that do not match leave the area empty and count a warning; the text itself is still kept in `registered_region`, so nothing A2C sent is lost.
- **Logo.** Stored as A2C sent it (`logo`, usually a path such as `/files/product-imaged05e46.jpg` that is relative to A2C's own host) and returned by the GET endpoints. The frontend must resolve it against the A2C host; the directory does not build a full URL.
- **`/banks/{bankId}`** is not needed by the sync.

The design's `DatabaseAdapter` and its `map_*` column mapping still fit a database source. The API adapter needs a response path (`banks`) and key names in their place.

**Checked against the A2C development backend (2026-10-21).**

- **Host and login.** The API is `https://a2c-backend-development.oanstaging.com/api`. `POST /v1/auth/login` with `{"usr", "pwd"}` returns the token as `data.token` (and `data.refresh_token`), not `access_token`. The other hosts tried, the A2C web app and the Kong gateway, are not the API for this.
- **API key not accepted.** `Authorization: token <key>:<secret>` (the Frappe API-key scheme) is answered with 401 `Missing Authorization Header` on the catalog routes, on the backend host and on the gateway. A2C's own OpenAPI lists that scheme for its two inbound webhooks only. The sync therefore uses a login or a Bearer token, not an API key.
- **Auth.** Both catalog endpoints answer 401 `Missing Authorization Header` without a token, so the sync needs an A2C credential. A Grievance-issued token was rejected with 401 `Invalid token` on the gateway, so the two services do not share signing keys today. The sync needs its own A2C service account (a user token lasts 15 minutes).
- **`GET /v1/catalog/facets`** returns 200 with `data.banks` (14 on dev), next to `categories`, `regions`, `tags`, `amount_range`, `tenure_range`, `tenures` and `max_interest_rate`. Each bank has exactly `name`, `bank_name`, `logo` (a relative path such as `/files/...` or null) and `registered_region`. The attached OpenAPI does not list `banks`; it is out of date.
- **`GET /v1/catalog/banks/{bankId}`** returns 200 with `bank`, `bank_code`, `bank_name`, `brand_name`, `entity_type`, `logo_url`, `registered_country`, `registered_region` and `website`; an unknown ID returns 404 `NOT_FOUND`. `bank_code` may be useful as `registration_number`, which the list does not carry. That would cost one call per bank, so decide whether it is worth it.
- **Names are not unique.** Dev has two banks both called `legel entity` (`PB-0271`, `PB-0170`), so the sync must key on the `PB-####` ID, never the name.
- **Regions.** Real names such as `Addis Ababa` resolve to a Grievance area (`region-ET14`). The dev placeholders `region` and `Test4` do not, so those banks would be created with no area and a warning.

## 7. Not built yet

1. Turn `Grievance.associated_service_provider` and `Grievance RBAC Assignment.service_provider_scope` into Links, with a patch that turns existing free text into providers first. Routing and permissions compare the two values for equality, so they need no change once both hold IDs.
2. Add `associated_service_provider_name` to grievance detail responses, and validate the ID on create (unknown ID and inactive ID both 400).
3. Endpoints to create or deactivate a provider by hand, and to create or edit a source, for people who cannot use Desk. No app role has Desk access.
4. A lookup of a provider by its `external_ref` (`PB-####`), for the pre-fill below.
5. The dropdown in `oan_grievance_ui`.
6. A per-source frequency (hourly or manual-only). Today every enabled source syncs daily.

### 7.1 Where the requirement comes from

- **Functional Specification D3, §3.2.2:** "Associated service provider" is optional and "Pre-filled from OAN registry if available". Section 2 of the FSD's channel table also lists "service provider linked from OAN registry" among the farmer's additional fields.
- **FR-03:** auto-routing considers service category, grievance type, geographic area and the associated service provider. `Grievance RBAC Assignment.service_provider_scope` already holds the provider today; once it holds a directory ID, routing needs no change.

The pre-fill has a consequence for the directory. If the OAN registry links a farmer to a bank by its `PB-####` ID, the form must turn that into a directory ID. The provider now stores that ID as `external_ref`, but no endpoint looks a provider up by it yet (item 4 above).

Open questions from the design that still stand: the real source columns for non-bank providers, and whether the first set of provider types is complete (more can be added as records).
