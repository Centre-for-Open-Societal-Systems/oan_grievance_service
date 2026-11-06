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
import uuid
from contextlib import contextmanager
from functools import wraps

import frappe
from oan_auth_service.api.operational_log import correlation_id, emit


def _iso(value) -> str | None:
	if not value:
		return None
	return value.isoformat() if hasattr(value, "isoformat") else str(value)


def grievance_fields(grievance) -> dict:
	"""The identifying, non-personal facts about a case. Works for a doc or a query row.

	Called from lifecycle and sweep code, so it never raises: a case that cannot be read (a stub,
	a half-built document) costs its fields, never the work around the log line.
	"""
	try:
		get = grievance.get if hasattr(grievance, "get") else lambda k, d=None: getattr(grievance, k, d)
		return {
			"grievance": get("name"),
			"ticket_number": get("ticket_number"),
			"category": get("service_category"),
			"department": get("assigned_dept"),
			"status": get("status"),
			"assigned_to": get("assigned_to"),
			"sla_due_at": _iso(get("sla_due_date")),
		}
	except Exception:
		return {}


@contextmanager
def job(name: str, **fields):
	"""Frame a scheduled job or background task as `job.started` and `job.finished`.

	Gives the work its own correlation id (a request has one already), so every line the
	sweep writes, including errors, can be pulled with one filter. `finished` carries the
	duration and whatever the job put in the dict this yields, such as how many cases moved.
	"""
	outer = correlation_id()
	if not outer:
		frappe.local.request_id = f"job-{name}-{uuid.uuid4().hex[:12]}"
	result: dict = {}
	started = time.perf_counter()
	emit("job.started", job=name, **fields)
	try:
		yield result
	except Exception as exc:
		emit(
			"job.failed",
			logging.ERROR,
			job=name,
			exception=type(exc).__name__,
			duration_ms=round((time.perf_counter() - started) * 1000),
		)
		raise
	else:
		emit("job.finished", job=name, duration_ms=round((time.perf_counter() - started) * 1000), **result)
	finally:
		if not outer:
			frappe.local.request_id = None


def logged_job(name: str):
	"""Decorator form of `job`. An integer return value is logged as `processed`."""

	def decorator(fn):
		@wraps(fn)
		def wrapper(*args, **kwargs):
			with job(name) as result:
				value = fn(*args, **kwargs)
				if isinstance(value, int) and not isinstance(value, bool):
					result["processed"] = value
				return value

		return wrapper

	return decorator
