# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Copy a source's providers into the Grievance Service Provider directory.

One run reads the whole list, so the directory ends up holding exactly what the source holds:
a provider the source no longer lists is removed, a new one is added, and one that is still
listed is updated in place and keeps its ID (a grievance stores the ID, so it must not change
from one run to the next). A removed provider that something still links to is deactivated
instead. The source is the truth for the providers it owns.

What belongs here: the run and how it is queued.

What does not belong here: how to reach a source (service_provider_adapters.py) or the rules
of a single provider (the DocType controller).

A run changes nothing unless the whole list was read. An empty list is refused, because a
broken answer must not look like "every provider was removed".
"""

from contextlib import contextmanager

import frappe
from frappe import _
from frappe.utils import now_datetime

from oan_grievance_service.grievance_masters.doctype.grievance_service_provider.grievance_service_provider import (
	DOCTYPE as PROVIDER,
)
from oan_grievance_service.grievance_masters.doctype.grievance_service_provider_source.grievance_service_provider_source import (
	DOCTYPE as SOURCE,
)
from oan_grievance_service.services.resolvers import resolve_administrative_area
from oan_grievance_service.services.service_provider_adapters import SourceError, get_adapter

JOB_METHOD = "oan_grievance_service.services.service_provider_sync.sync_source"
JOB_QUEUE = "long"
JOB_TIMEOUT = 600

STATUS_SUCCESS = "Success"
STATUS_PARTIAL = "Partial"
STATUS_FAILED = "Failed"

# Shown in the source record; the full list would not fit a Small Text.
MAX_MESSAGE_ITEMS = 5


class SyncInProgress(frappe.ValidationError):
	http_status_code = 409


def enqueue_sync(source_name: str) -> str:
	"""Queue a sync of one source and return the job ID. Used by the daily job and the endpoint.

	TODO(STG-454): the endpoint that calls this will change (see api/v1/service_provider_source.py);
	keep the queueing rules here so only the route has to move.

	Raises DoesNotExistError for an unknown source, ValidationError for a disabled one and
	SyncInProgress when a run for the source is already queued or running.
	"""
	if not frappe.db.exists(SOURCE, source_name):
		frappe.throw(_("Source {0} not found.").format(frappe.bold(source_name)), frappe.DoesNotExistError)
	source = frappe.get_doc(SOURCE, source_name)
	if not source.is_enabled:
		frappe.throw(_("Source {0} is disabled.").format(frappe.bold(source.name)), frappe.ValidationError)
	job = frappe.enqueue(
		JOB_METHOD,
		queue=JOB_QUEUE,
		timeout=JOB_TIMEOUT,
		job_id=f"grievance_service_provider_sync:{source.name}",
		deduplicate=True,
		source_name=source.name,
	)
	if job is None:
		frappe.throw(_("A sync for this source is already running."), SyncInProgress)
	return job.id


def enqueue_scheduled_syncs() -> list[str]:
	"""Queue a sync of every enabled source; the scheduler calls this once a day.

	A source whose last run is still going is skipped (the next day's run picks it up), and a
	source that cannot be queued is logged without stopping the others. Returns the names queued.
	"""
	queued = []
	for name in frappe.get_all(SOURCE, filters={"is_enabled": 1}, pluck="name", order_by="name asc"):
		try:
			enqueue_sync(name)
			queued.append(name)
		except SyncInProgress:
			continue
		except Exception:
			frappe.log_error(title=f"oan_grievance_service: scheduled provider sync not queued: {name}")
	return queued


def sync_source(source_name: str) -> dict:
	"""Background job entry point. Frappe commits when the job ends, so the result is saved."""
	return run_sync(source_name)


def run_sync(source_name: str) -> dict:
	"""Read the source and bring the directory in line. Never raises for a source problem.

	The outcome is written on the source (status, message, time); the returned summary has the
	counts. The caller commits.
	"""
	source = frappe.get_doc(SOURCE, source_name)
	if not source.is_enabled:
		return _record(source, STATUS_FAILED, _("The source is disabled."))

	try:
		rows, warnings = get_adapter(source).fetch()
	except SourceError as exc:
		return _record(source, STATUS_FAILED, str(exc))
	if not rows:
		return _record(source, STATUS_FAILED, _("The source returned no providers, so nothing was changed."))

	savepoint = "service_provider_sync"
	frappe.db.savepoint(savepoint)
	try:
		with _sync_flag():
			counts, errors = _apply(source, rows, warnings)
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		frappe.log_error(title="oan_grievance_service: service provider sync failed")
		return _record(source, STATUS_FAILED, _("Unexpected error; see the Error Log. Nothing was changed."))

	status = STATUS_PARTIAL if errors else STATUS_SUCCESS
	return _record(source, status, _summary_message(counts, warnings, errors), counts)


def _apply(source, rows: list[dict], warnings: list[str]) -> tuple[dict, list[str]]:
	# Keyed case-folded, like the database's own comparison, so a differently cased ID finds
	# the provider it already has instead of trying to insert a second one.
	existing = {
		row.external_ref.casefold(): row
		for row in frappe.get_all(
			PROVIDER,
			filters={"source": source.name},
			fields=[
				"name",
				"external_ref",
				"provider_name",
				"provider_type",
				"administrative_area",
				"registered_region",
				"logo",
				"is_active",
			],
		)
	}
	counts = {"created": 0, "updated": 0, "unchanged": 0, "removed": 0, "deactivated": 0}
	errors, unknown_areas, areas = [], set(), {}
	now = now_datetime()

	for row in rows:
		area_name = row["area_name"]
		if area_name and area_name not in areas:
			areas[area_name] = resolve_administrative_area(area_name)
		area = areas.get(area_name)
		if area_name and not area:
			unknown_areas.add(area_name)

		wanted = {
			"provider_name": row["provider_name"],
			"provider_type": source.default_provider_type,
			"administrative_area": area,
			# The region as the source wrote it, so it survives when it matches no area.
			"registered_region": area_name,
			"logo": row["logo"],
			"is_active": 1,
		}
		current = existing.get(row["external_ref"].casefold())
		try:
			if current is None:
				frappe.get_doc(
					{
						"doctype": PROVIDER,
						"source": source.name,
						"external_ref": row["external_ref"],
						"last_synced_at": now,
						**wanted,
					}
				).insert(ignore_permissions=True)
				counts["created"] += 1
			elif any((current.get(k) or None) != (v or None) for k, v in wanted.items()):
				doc = frappe.get_doc(PROVIDER, current.name)
				doc.update({**wanted, "last_synced_at": now})
				doc.save(ignore_permissions=True)
				counts["updated"] += 1
			else:
				frappe.db.set_value(PROVIDER, current.name, "last_synced_at", now, update_modified=False)
				counts["unchanged"] += 1
		except frappe.ValidationError as exc:
			errors.append(f"{row['external_ref']}: {exc}")
			frappe.clear_messages()

	# Judged on the whole list, not on the rows that saved: a provider that failed to save
	# is still listed at the source and must not be removed for it.
	listed = {row["external_ref"].casefold() for row in rows}
	for ref, current in existing.items():
		if ref not in listed:
			_remove_or_deactivate(current, counts)

	if unknown_areas:
		warnings.append(
			_("No administrative area matches {0}; those providers have no area.").format(
				", ".join(sorted(unknown_areas))
			)
		)
	return counts, errors


def _remove_or_deactivate(current, counts: dict) -> None:
	"""Drop a provider the source no longer lists, so the directory holds only what it holds.

	Frappe refuses to delete a record that another record links to. A provider still in use
	(a branch under it, and later a grievance) is deactivated instead, so nothing is left
	pointing at a missing record. The deleted row is archived in the Deleted Document log.
	"""
	try:
		frappe.delete_doc(PROVIDER, current.name, ignore_permissions=True)
		counts["removed"] += 1
	except frappe.LinkExistsError:
		frappe.clear_messages()
		if current.is_active:
			doc = frappe.get_doc(PROVIDER, current.name)
			doc.is_active = 0
			doc.save(ignore_permissions=True)
			counts["deactivated"] += 1


def _summary_message(counts: dict, warnings: list[str], errors: list[str]) -> str:
	parts = [
		_("{0} created, {1} updated, {2} unchanged, {3} removed, {4} deactivated (still in use).").format(
			counts["created"],
			counts["updated"],
			counts["unchanged"],
			counts["removed"],
			counts["deactivated"],
		)
	]
	for label, items in ((_("Errors"), errors), (_("Warnings"), warnings)):
		if items:
			shown = items[:MAX_MESSAGE_ITEMS]
			more = len(items) - len(shown)
			text = "; ".join(shown) + (" " + _("(and {0} more)").format(more) if more else "")
			parts.append(f"{label} ({len(items)}): {text}")
	return "\n".join(parts)


def _record(source, status: str, message: str, counts: dict | None = None) -> dict:
	"""Write the outcome on the source and return it."""
	now = now_datetime()
	source.db_set(
		{"last_synced_at": now, "last_sync_status": status, "last_sync_message": message},
		update_modified=False,
	)
	source.last_synced_at, source.last_sync_status, source.last_sync_message = now, status, message
	return {"source": source.name, "status": status, "message": message, **(counts or {})}


@contextmanager
def _sync_flag():
	"""Marks the run, so the provider controller lets the sync change fields it owns."""
	frappe.flags.in_service_provider_sync = True
	try:
		yield
	finally:
		frappe.flags.in_service_provider_sync = False
