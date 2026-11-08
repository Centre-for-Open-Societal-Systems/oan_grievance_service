# IVR intake: caller profile and grievance

`services/intake.file_from_ivr` files a grievance from a phone number alone. Two inserts can lose
a race, and the code orders and guards them so neither loses the farmer's call.

## Order of work

1. Replay check on `client_submission_uuid` (`ivr:{call_id}`).
2. Read-only profile lookup. A blocked or inactive profile refuses the call here.
3. Grievance insert. A first-time caller's grievance carries `submitter` empty.
4. Profile create for a first-time caller, then `submitter` is set with `db_set`.

## Why the profile is created after the grievance

Two first calls from one number both find no profile, and both insert. The unique `dedupe_key`
refuses the second. Creating the profile last keeps that window short and keeps the failure
away from the grievance, which is the thing the caller phoned to file.

## Why a savepoint, and not `frappe.db.rollback()`

The grievance is already written in the same transaction when the profile insert fails. A full
rollback would delete it. Rolling back to the `ivr_caller_profile` savepoint undoes only the
failed insert.

Postgres aborts the whole transaction after a failed statement, so without any rollback the
re-read of the winner's profile fails with `InFailedSqlTransaction`. The savepoint clears that
state.

## Why a missing re-read raises

Frappe runs Postgres at repeatable read. A savepoint rollback keeps the transaction's snapshot,
so the re-read may not see a profile the other call committed in the meantime. In that case the
original error is re-raised, Frappe rolls back the whole request, and nothing is half filed. The
IVR platform retries, the retry finds the profile, and the call files cleanly.

## What a grievance without a submitter means

`Grievance.submitter` is not mandatory, and the consumers (`intake.record_submission`,
notifications, duplicate flagging) already handle it empty. The grievance and the profile link
commit together, so a committed IVR grievance always has its profile.

## Not the same race

A second delivery of the same call (same `call_id`) is the `client_submission_uuid` unique key at
the grievance insert. It rolls back the request and returns the first delivery's grievance.
Duplicate flagging (`services/duplicates`) is a separate background job after commit and is
unrelated to either race.
