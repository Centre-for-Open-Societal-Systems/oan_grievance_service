"""The grievance-specific parts of operational logging.

Everything generic is shared by every OAN app and lives in
`oan_auth_service.api.operational_log`: `emit`, `log_error`, masking and scrubbing, the
correlation id, and (in `request_log`) the request log. See that module and
`docs/OPERATIONAL_LOGGING.md` in oan_auth_service.

This module holds what is about a grievance or the SLA sweeps: the fields that identify a case,
and the framing of a scheduled job.
"""

import logging
import time
from functools import wraps

import frappe
from oan_auth_service.api.operational_log import emit, ensure_correlation_id


def grievance_fields(grievance) -> dict:
	"""The identifying, non-personal facts about a case: a document or a query row.

	`.get` with no default returns None for a field the row does not carry, so this cannot fail
	on either; it is called from lifecycle and sweep code, ahead of `emit`.
	"""
	due = grievance.get("sla_due_date")
	return {
		"grievance": grievance.get("name"),
		"ticket_number": grievance.get("ticket_number"),
		"category": grievance.get("service_category"),
		"department": grievance.get("assigned_dept"),
		"status": grievance.get("status"),
		"assigned_to": grievance.get("assigned_to"),
		"sla_due_at": due.isoformat() if hasattr(due, "isoformat") else due,
	}


def logged_job(name: str):
	"""Frame a scheduled job as `job.started` and `job.finished` (or `job.failed`).

	The job gets a correlation id (a request has one already), so every line the sweep writes,
	including errors, can be pulled with one filter; it is the shared helper's, which is Frappe's
	monitor trace id for the job when the monitor is on. `job.finished` carries the duration, and
	the return value as `processed` when it is a count, such as how many cases moved.
	"""

	def decorator(fn):
		@wraps(fn)
		def wrapper(*args, **kwargs):
			outer = getattr(frappe.local, "request_id", None)
			ensure_correlation_id()
			started = time.perf_counter()
			emit("job.started", job=name)
			try:
				value = fn(*args, **kwargs)
			except Exception as exc:
				duration_ms = round((time.perf_counter() - started) * 1000)
				emit(
					"job.failed",
					logging.ERROR,
					job=name,
					exception=type(exc).__name__,
					duration_ms=duration_ms,
				)
				raise
			else:
				processed = value if isinstance(value, int) and not isinstance(value, bool) else None
				emit(
					"job.finished",
					job=name,
					duration_ms=round((time.perf_counter() - started) * 1000),
					processed=processed,
				)
				return value
			finally:
				if not outer:
					frappe.local.request_id = None

		return wrapper

	return decorator
