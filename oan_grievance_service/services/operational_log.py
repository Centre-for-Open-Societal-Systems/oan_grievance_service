"""Structured operational logging.

One JSON object per line in `logs/oan_grievance_service.log` (or stderr when
`FRAPPE_STREAM_LOGGING` is set, as in a container). Every line carries a UTC
timestamp, a level, an `event` name and, inside a request or a scheduled job, the
`correlation_id` that ties it to the rest of that work.

The logger is Frappe's own (`frappe.logger`), so rotation and stream mode come from
the framework. This module adds what it does not provide: a JSON line format, a level
that is not silently ERROR on a production bench, and masking.

Nothing here logs a request or response body, a header value or a query value. The
request log records field *names* and sizes, and every value that is logged goes
through `redact`, so a caller that passes a personal field by mistake still gets it
masked. Free text (exception messages, tracebacks, paths) goes through `scrub_text`.

Site config (all optional):

	grievance_log_level          DEBUG | INFO (default) | WARNING | ERROR
	grievance_log_max_bytes      size of one log file before rotation (default 10 MB)
	grievance_log_backup_count   rotated files kept (default 10)
	grievance_slow_request_ms    duration above which a request is logged as slow (default 2000)

Events are named `<area>.<what>`: `http.request`, `security.*`, `sla.*`, `escalation.*`,
`job.*`, `error`.
"""

import hashlib
import hmac
import json
import logging
import re
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import wraps

import frappe

LOGGER_NAME = "oan_grievance_service"
# `frappe.logger(..., allow_site=False)` names its logger "<module>-all".
_LOGGER_KEY = f"{LOGGER_NAME}-all"

DEFAULT_MAX_BYTES = 10_000_000
DEFAULT_BACKUP_COUNT = 10
DEFAULT_SLOW_REQUEST_MS = 2000

REDACTED = "[REDACTED]"
MASKED = "[MASKED]"

MAX_TEXT = 2000
MAX_FIELD_TEXT = 500
MAX_DEPTH = 5

# A caller-supplied correlation id is honoured only when it is plain. Anything else
# (spaces, newlines, quotes) is how a client forges log lines, so it is replaced.
_CORRELATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]{7,63}")


# --- Masking ----------------------------------------------------------------------------


def mask_email(value) -> str:
	"""`jane.doe@example.com` -> `j***@example.com`."""
	text = str(value or "")
	local, sep, domain = text.rpartition("@")
	if not sep or not local:
		return MASKED
	return f"{local[0]}***@{domain}"


def mask_phone(value) -> str:
	"""Keep the last two digits: enough to tell two numbers apart in a trace, not to call one."""
	digits = re.sub(r"\D", "", str(value or ""))
	return f"***{digits[-2:]}" if len(digits) > 6 else MASKED


def mask_name(value) -> str:
	"""`Abebe Kebede` -> `A*** K***`."""
	parts = str(value or "").split()
	return " ".join(f"{part[0]}***" for part in parts) or MASKED


def mask_identifier(value) -> str:
	"""A national ID, registration number or similar: last two characters at most."""
	text = str(value or "")
	return f"***{text[-2:]}" if len(text) > 8 else MASKED


def mask_ip(value) -> str | None:
	"""IPv4 to its /24, IPv6 to its /48. The gateway's own log holds the full address."""
	text = str(value or "")
	if not text:
		return None
	if ":" in text:
		groups = text.split(":")
		return ":".join(groups[:3]) + "::" if len(groups) > 3 else MASKED
	octets = text.split(".")
	return ".".join([*octets[:3], "0"]) if len(octets) == 4 else MASKED


def mask_user(user) -> str | None:
	"""A User id is an email or a phone-derived name, so it is masked like either."""
	if not user:
		return None
	text = str(user)
	if text in ("Administrator", "Guest"):
		return text
	if "@" in text:
		return mask_email(text)
	if re.fullmatch(r"\+?[\d\s\-]{7,}", text):
		return mask_phone(text)
	return f"{text[0]}***"


def user_ref(user) -> str | None:
	"""A stable, non-reversible handle for a user.

	It lets an operator count one account's failed sign-ins, or follow one user across
	requests, without the log holding who that is. Keyed with the site's secret so the
	handle of a known address cannot be computed from outside.
	"""
	if not user:
		return None
	try:
		conf = frappe.local.conf
		key = conf.get("encryption_key") or conf.get("db_name") or frappe.local.site
	except Exception:
		key = LOGGER_NAME
	return hmac.new(str(key).encode(), str(user).lower().encode(), hashlib.sha256).hexdigest()[:12]


# A key is sensitive when any of these is part of its name; a few short ones must match whole.
_SECRET_PARTS = (
	"password",
	"passwd",
	"secret",
	"token",
	"authorization",
	"cookie",
	"api_key",
	"apikey",
	"credential",
	"signature",
	"private_key",
	"csrf",
)
_SECRET_EXACT = {"pwd", "sid", "otp", "pin", "key", "auth", "session"}

# Personal data, with the way each is masked. Order matters: first match wins.
_PII_RULES = (
	(("email",), mask_email),
	(("mobile", "phone", "msisdn", "telephone"), mask_phone),
	(("submitter_name", "full_name", "first_name", "last_name", "middle_name", "surname"), mask_name),
	(("fayda", "national_id", "registration_number", "account_number", "iban", "tin"), mask_identifier),
)
_PII_PARTS = (
	"address",
	"birth",
	"dob",
	"latitude",
	"longitude",
	"gps",
	"location",
	"description",
	"complaint",
	"message",
	"body",
	"content",
	"remarks",
	"comment",
)
_USER_KEYS = {
	"user",
	"owner",
	"assigned_to",
	"officer",
	"usr",
	"username",
	"attempted_user",
	"from_user",
	"to_user",
	"recipient",
	"approver",
	"requested_by",
	"target_user",
}


def _split(key: str) -> list[str]:
	return [p for p in re.split(r"[^a-z0-9]+", key.lower()) if p]


def is_secret_key(key) -> bool:
	name = str(key).lower()
	return name in _SECRET_EXACT or any(part in name for part in _SECRET_PARTS)


def _mask_for_key(key: str):
	name = str(key).lower()
	if name in _USER_KEYS:
		return mask_user
	for parts, fn in _PII_RULES:
		if any(p in name if len(p) > 3 else p in _split(name) for p in parts):
			return fn
	if any(p in name for p in _PII_PARTS):
		return lambda _v: MASKED
	return None


_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]*")
_AUTH_SCHEME = re.compile(r"\b(Bearer|Basic|token)\s+[A-Za-z0-9._~+/=:\-]{6,}", re.IGNORECASE)
_SECRET_PAIR = re.compile(
	r"""(?ix)
	(["']?\b(?:[a-z_]*(?:password|passwd|secret|token|api_key|apikey|authorization|credential)[a-z_]*
		|pwd|sid|otp|csrf_token)\b["']?\s*[:=]\s*)
	("[^"]*"|'[^']*'|[^\s,;&})\]]+)
	"""
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Long digit runs: Ethiopian mobile numbers (10 digits, or 12 with 251), Fayda ids (16).
# Nine characters or fewer is left alone because a ticket number is nine.
_PHONE = re.compile(r"(?<![\w.])(?:\+\d{10,15}|\d{10,})(?![\w])")


def scrub_text(text, limit: int = MAX_TEXT) -> str:
	"""Remove credentials and personal data from free text, then cap its length.

	For anything a person or a library wrote: exception messages, tracebacks, paths.
	Order matters: a credential is removed before the email pass could half-mask it.
	"""
	if text is None:
		return ""
	value = str(text)
	value = _JWT.sub(REDACTED, value)
	value = _AUTH_SCHEME.sub(lambda m: f"{m.group(1)} {REDACTED}", value)
	value = _SECRET_PAIR.sub(lambda m: f"{m.group(1)}{REDACTED}", value)
	value = _EMAIL.sub(lambda m: mask_email(m.group(0)), value)
	value = _PHONE.sub(lambda m: mask_phone(m.group(0)), value)
	return value if len(value) <= limit else value[:limit] + "...[truncated]"


def redact(value, key=None, _depth: int = 0):
	"""A copy of `value` that is safe to log.

	Keys that name a credential become `[REDACTED]`; keys that name personal data are masked
	(an email keeps its first letter and domain, a phone its last two digits, and free
	text fields are dropped). Remaining strings are scrubbed for anything that looks like
	either. Containers are walked to a fixed depth, so a deep structure cannot cost much.
	"""
	if key is not None:
		if is_secret_key(key):
			return REDACTED
		mask = _mask_for_key(key)
		if mask is not None and value not in (None, "", [], {}):
			if isinstance(value, dict | list | tuple | set):
				return MASKED
			return mask(value)

	if isinstance(value, dict):
		if _depth >= MAX_DEPTH:
			return MASKED
		return {str(k): redact(v, key=k, _depth=_depth + 1) for k, v in value.items()}
	if isinstance(value, list | tuple | set):
		if _depth >= MAX_DEPTH:
			return MASKED
		return [redact(v, _depth=_depth + 1) for v in list(value)[:50]]
	if isinstance(value, str):
		return scrub_text(value, MAX_FIELD_TEXT)
	if isinstance(value, bytes | bytearray):
		return f"<{len(value)} bytes>"
	if value is None or isinstance(value, bool | int | float):
		return value
	return scrub_text(str(value), MAX_FIELD_TEXT)


def scrub_path(path: str) -> str:
	"""A URL path with personal ids removed: an officer is addressed by email, for instance."""
	return scrub_text(path, 300)


# --- Correlation ------------------------------------------------------------------------


def valid_correlation_id(candidate) -> str | None:
	"""`candidate` when it is a plain token of 8 to 64 characters, otherwise None."""
	# fullmatch, not `$`: `$` also matches before a trailing newline.
	if isinstance(candidate, str) and _CORRELATION_ID.fullmatch(candidate):
		return candidate
	return None


def correlation_id() -> str | None:
	"""The id of the request or job in progress. Also what API envelopes call `request_id`."""
	return getattr(frappe.local, "request_id", None)


def ensure_correlation_id(candidate=None, prefix: str = "") -> str:
	"""Set the id for this request or job unless one is already set, and return it.

	The envelope helpers in oan_auth_service read `frappe.local.request_id`, so the id set
	here is the one the caller sees in `request_id` and in the `X-Request-Id` header.
	"""
	current = correlation_id()
	if current:
		return current
	new = valid_correlation_id(candidate) or f"{prefix}{uuid.uuid4()}"
	frappe.local.request_id = new
	return new


# --- Logger -----------------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
	"""`{"ts": ..., "level": ..., "logger": ..., "event": ..., **fields}` on one line."""

	def format(self, record: logging.LogRecord) -> str:
		payload = {
			"ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
			"level": record.levelname,
			"logger": LOGGER_NAME,
			"event": record.getMessage(),
		}
		payload.update(getattr(record, "oan_fields", None) or {})
		return json.dumps(payload, default=str, ensure_ascii=False, separators=(",", ":"))


def _conf(name: str, default):
	try:
		value = frappe.local.conf.get(name)
	except Exception:
		return default
	return default if value in (None, "") else value


def _level_from_conf() -> int:
	level = getattr(logging, str(_conf("grievance_log_level", "INFO")).upper(), None)
	return level if isinstance(level, int) else logging.INFO


def get_logger() -> logging.Logger:
	"""The operational logger, configured once per process."""
	existing = logging.getLogger(_LOGGER_KEY)
	if getattr(existing, "_oan_configured", False):
		return existing

	# One file for the bench rather than one per site as well: records carry `site`, and a
	# second copy of every line would double the disk this uses.
	logger = frappe.logger(
		LOGGER_NAME,
		allow_site=False,
		max_size=int(_conf("grievance_log_max_bytes", DEFAULT_MAX_BYTES)),
		file_count=int(_conf("grievance_log_backup_count", DEFAULT_BACKUP_COUNT)),
	)
	for handler in logger.handlers:
		handler.setFormatter(JsonFormatter())
	# Frappe's default level is ERROR on a production bench, which would drop every event
	# below. Operators choose the level here instead.
	logger.setLevel(_level_from_conf())
	logger._oan_configured = True
	return logger


def emit(event: str, level: int = logging.INFO, **fields) -> None:
	"""Write one event. Never raises: logging must not break the work it describes."""
	try:
		logger = get_logger()
		if not logger.isEnabledFor(level):
			return
		record = {"correlation_id": correlation_id(), "site": getattr(frappe.local, "site", None)}
		record.update(redact(fields))
		logger.log(level, event, extra={"oan_fields": {k: v for k, v in record.items() if v is not None}})
	except Exception:
		pass


def info(event: str, **fields) -> None:
	emit(event, logging.INFO, **fields)


def warning(event: str, **fields) -> None:
	emit(event, logging.WARNING, **fields)


def error(event: str, **fields) -> None:
	emit(event, logging.ERROR, **fields)


# --- Errors -----------------------------------------------------------------------------


def log_error(title: str, message: str | None = None, **kwargs) -> None:
	"""`frappe.log_error` plus a structured line, both free of credentials and personal data.

	The Error Log row keeps the title and the traceback an operator needs, with the
	correlation id on its first line so the row can be matched to the request or job.
	A message that echoes an email, a phone number or a token (an exception raised on
	user input often does) is scrubbed before it is stored. `kwargs` go to
	`frappe.log_error` (for example `defer_insert`).
	"""
	exc_type = sys.exc_info()[0]
	exc_type = exc_type.__name__ if exc_type else None

	body = message if message is not None else frappe.get_traceback()
	clean = scrub_text(body, 10_000)
	cid = correlation_id()
	stored = f"correlation_id: {cid}\n{clean}" if cid else clean
	try:
		frappe.log_error(title=scrub_text(title, 140), message=stored, **kwargs)
	finally:
		emit(
			"error",
			logging.ERROR,
			title=title,
			exception=exc_type,
			detail=clean[:MAX_TEXT],
		)


# --- SLA and escalation -----------------------------------------------------------------


def _iso(value) -> str | None:
	if not value:
		return None
	return value.isoformat() if hasattr(value, "isoformat") else str(value)


def grievance_fields(grievance) -> dict:
	"""The identifying, non-personal facts about a case. Works for a doc or a query row."""
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


def _case_event(name: str, grievance, level: int, fields: dict) -> None:
	"""Emit `name` with the case's identifying fields, whatever shape the case is in.

	Called from lifecycle and sweep code, so a case that cannot be read (a stub, a
	half-built document) costs the line, never the work around it.
	"""
	try:
		emit(name, level, **{**grievance_fields(grievance), **fields})
	except Exception:
		pass


def sla_event(event: str, grievance, level: int = logging.INFO, **fields) -> None:
	"""A `sla.*` event about one case."""
	_case_event(f"sla.{event}", grievance, level, fields)


def escalation_event(event: str, grievance, level: int = logging.INFO, **fields) -> None:
	"""An `escalation.*` event about one case."""
	_case_event(f"escalation.{event}", grievance, level, fields)


def security_event(event: str, level: int = logging.WARNING, **fields) -> None:
	"""A `security.*` event. Pass identifiers, never credentials: values are redacted anyway."""
	emit(f"security.{event}", level, **fields)


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
	info("job.started", job=name, **fields)
	try:
		yield result
	except Exception as exc:
		error(
			"job.failed",
			job=name,
			exception=type(exc).__name__,
			duration_ms=round((time.perf_counter() - started) * 1000),
		)
		raise
	else:
		info("job.finished", job=name, duration_ms=round((time.perf_counter() - started) * 1000), **result)
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
