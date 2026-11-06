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

Config (all optional). The first three belong to the one log file every site on the bench
shares, so they are read from `common_site_config.json` once per process; a value in a
single site's `site_config.json` cannot apply to just that site and is ignored.

	grievance_log_level          DEBUG | INFO (default) | WARNING | ERROR
	grievance_log_max_bytes      size of one log file before rotation (default 10 MB)
	grievance_log_backup_count   rotated files kept (default 10)

and, per site (`site_config.json` or common):

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
import threading
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
# An Error Log row keeps the end of a traceback (where the exception message is), and the
# structured line keeps its last line separately plus a shorter tail.
ERROR_LOG_LIMIT = 30_000
ERROR_DETAIL_LIMIT = 4000
ERROR_MESSAGE_LIMIT = 500
# `Error Log.method`, the title column, holds 140 characters; more raises.
ERROR_TITLE_LIMIT = 140

TRUNCATED_END = "...[truncated]"
TRUNCATED_START = "[truncated]..."

# The levels an operator may choose. NOTSET and CRITICAL would silently drop or keep too much.
VALID_LEVELS = {
	"DEBUG": logging.DEBUG,
	"INFO": logging.INFO,
	"WARNING": logging.WARNING,
	"ERROR": logging.ERROR,
}
MIN_MAX_BYTES = 10_000


class Scrubbed(str):
	"""Text that has already been through `scrub_text`, so `redact` leaves it as it is.

	Without this a long, already-cleaned field would be scrubbed a second time and cut to
	`MAX_FIELD_TEXT`, which for a traceback throws away the exception message at the end.
	"""

	__slots__ = ()


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
# A credential has a shape: an `Authorization` header (or the same key in a dict or JSON dump)
# followed by its value, or a long opaque `Bearer` token. The bare words "token", "basic" and
# "bearer" in a sentence ("Invalid token provided") are not credentials and are left alone.
_AUTH_HEADER = re.compile(
	r"""(?ix)
	(\bauthorization["']?\s*[:=]\s*["']?)
	(?:(?:bearer|basic|token)\s+)?
	[^\s"',;]+
	"""
)
_BEARER_OPAQUE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=\-]{20,}")
_SECRET_PAIR = re.compile(
	# The key must start where a name starts (the lookbehind) and its affixes are bounded. An
	# unanchored `[a-z_]*` before the keyword is tried from every position in a long run of
	# letters, which takes seconds on a 30,000-character local variable.
	r"""(?ix)
	(["']?(?<![A-Za-z0-9_])
	(?:[a-z_]{0,40}(?:password|passwd|secret|token|api_key|apikey|authorization|credential)[a-z_]{0,40}
		|pwd|sid|otp|csrf_token)(?![A-Za-z0-9_])["']?\s*[:=]\s*)
	("[^"]*"|'[^']*'|[^\s,;&})\]]+)
	"""
)
# Personal fields in a dict or keyword dump, as a local variable's repr shows them. Used on
# tracebacks only: in prose, "description: too short" is not data worth hiding.
_PII_PAIR = re.compile(
	r"""(?ix)
	(["']?(?<![A-Za-z0-9_])
	[a-z_]{0,40}(?:email|mobile|phone|msisdn|submitter_name|full_name|first_name|last_name
		|middle_name|surname|fayda|national_id|address|description|complaint)[a-z_]{0,40}
	(?![A-Za-z0-9_])["']?\s*[:=]\s*)
	("[^"]*"|'[^']*'|[^\s,;&})\]]+)
	"""
)
# Starts only at the beginning of a run of address characters, and the local part is at most
# 64 characters (the standard limit). Without the lookbehind the match is retried from every
# position of a long run of letters or digits, which is quadratic.
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,255}\.[A-Za-z]{2,}")
# Phone numbers have a shape too, so a timestamp or an id is not mistaken for one:
#   Ethiopian mobiles, 0911223344 or +251911223344 (a 10-digit 09/07 or 251 + 9 prefix);
#   Fayda ids, exactly 16 digits;
#   an international number with a +, with or without spaces and dashes;
#   a national number starting 0 that is written in groups, "0911 223 344".
# Not matched: epoch times, order ids, a nine-character ticket number such as 012345678.
_PHONE = re.compile(
	r"""(?x)
	(?<![\w.])
	(?:
		(?:\+?251|0)[79]\d{8}
		| \d{16}
		| \+\d{1,3}(?:[\s\-]?\d{2,4}){3,4}
		| 0\d{2,3}(?:[\s\-]\d{2,4}){2,3}
	)
	(?![\w])
	"""
)
# One local variable in a `get_traceback(with_context=True)` listing: `      name = value`.
_LOCAL_VAR = re.compile(r"^(\s{4,})([A-Za-z_][A-Za-z0-9_]*) = ")


def _cut(value: str, limit: int, keep: str) -> str:
	"""`value` within `limit` characters in all, marker included, from its head or its tail."""
	if len(value) <= limit:
		return value
	marker = TRUNCATED_START if keep == "tail" else TRUNCATED_END
	if limit <= len(marker):
		# No room for a marker as well: a hard cut still honours the limit.
		return (value[-limit:] if keep == "tail" else value[:limit]) if limit > 0 else ""
	room = limit - len(marker)
	return marker + value[-room:] if keep == "tail" else value[:room] + marker


# Only this many times the limit is ever scanned. The patterns below are linear, but a
# traceback listing every local variable of a request can be megabytes, and what is cut away
# afterwards need not be read at all.
SCAN_FACTOR = 4


def _precap(value: str, limit: int, keep: str) -> str:
	"""`value` reduced to the part that can survive a cut to `limit`, plus a margin."""
	room = max(limit, 1) * SCAN_FACTOR
	if len(value) <= room:
		return value
	if keep == "tail":
		value = value[-room:]
		# The first line is probably cut mid-way; half of a token is not worth scanning.
		return value.split("\n", 1)[1] if "\n" in value else value
	return value[:room]


def scrub_text(text, limit: int = MAX_TEXT, keep: str = "head") -> str:
	"""Remove credentials and personal data from free text, then cap its length.

	For anything a person or a library wrote: exception messages, tracebacks, paths.
	Order matters: a credential is removed before the email pass could half-mask it.
	The result is never longer than `limit`, marker included. `keep="tail"` keeps the end,
	which is where a traceback's exception message is.
	"""
	if text is None:
		return ""
	value = _precap(str(text), limit, keep)
	value = _JWT.sub(REDACTED, value)
	value = _AUTH_HEADER.sub(lambda m: f"{m.group(1)}{REDACTED}", value)
	value = _BEARER_OPAQUE.sub(f"Bearer {REDACTED}", value)
	value = _SECRET_PAIR.sub(lambda m: f"{m.group(1)}{REDACTED}", value)
	value = _EMAIL.sub(lambda m: mask_email(m.group(0)), value)
	value = _PHONE.sub(lambda m: mask_phone(m.group(0)), value)
	return _cut(value, limit, keep)


def scrub_traceback(text, limit: int = ERROR_LOG_LIMIT) -> str:
	"""`scrub_text` for a traceback, which may list each frame's local variables.

	Frappe already blanks variables named `password` or `token` there; this also blanks one
	named for a credential or personal field (`submitter_name = ...`), and personal keys inside
	a dict or document dump. The end is kept when it is cut: that is the exception message.
	"""
	lines = []
	for line in _precap(str(text or ""), limit, "tail").split("\n"):
		match = _LOCAL_VAR.match(line)
		if match:
			prefix, name = match.group(0), match.group(2)
			if is_secret_key(name):
				line = f"{prefix}{REDACTED}"
			elif _mask_for_key(name) is not None:
				line = f"{prefix}{MASKED}"
			else:
				line = _PII_PAIR.sub(lambda m: f"{m.group(1)}{MASKED}", line)
		lines.append(line)
	return scrub_text("\n".join(lines), limit, keep="tail")


def last_line(text) -> str:
	"""The last non-blank line of `text`: a traceback's exception type and message."""
	for line in reversed(str(text or "").splitlines()):
		if line.strip():
			return line.strip()
	return ""


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
	if isinstance(value, Scrubbed):
		return value
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
	"""A setting read from the current site's config (which includes the common config)."""
	try:
		value = frappe.local.conf.get(name)
	except Exception:
		return default
	return default if value in (None, "") else value


def _process_conf(name: str, default):
	"""A setting that applies to the whole process, read from `common_site_config.json`.

	The log file, its size and its level are shared by every site a worker serves. Reading them
	from whichever site happens to log first would let that site's config decide for all.
	"""
	try:
		from frappe.config import get_common_site_config

		value = get_common_site_config(cached=True).get(name)
	except Exception:
		return default
	return default if value in (None, "") else value


def _int_conf(name: str, default: int, minimum: int) -> int:
	try:
		value = int(_process_conf(name, default))
	except (TypeError, ValueError):
		return default
	return value if value >= minimum else default


def _level_from_conf() -> int:
	"""The configured level when it is one of DEBUG, INFO, WARNING or ERROR, otherwise INFO.

	NOTSET would pass everything through to the root logger's level and CRITICAL would drop
	every event below it, so neither is accepted.
	"""
	return VALID_LEVELS.get(str(_process_conf("grievance_log_level", "INFO")).strip().upper(), logging.INFO)


def slow_request_ms() -> float:
	try:
		value = float(_conf("grievance_slow_request_ms", DEFAULT_SLOW_REQUEST_MS))
	except (TypeError, ValueError):
		return float(DEFAULT_SLOW_REQUEST_MS)
	return value if value > 0 else float(DEFAULT_SLOW_REQUEST_MS)


# Workers are threaded; two first requests must not each add a set of handlers.
_CONFIGURE_LOCK = threading.Lock()


def get_logger() -> logging.Logger:
	"""The operational logger, configured once per process."""
	existing = logging.getLogger(_LOGGER_KEY)
	if getattr(existing, "_oan_configured", False):
		return existing

	with _CONFIGURE_LOCK:
		if getattr(existing, "_oan_configured", False):
			return existing

		# One file for the bench rather than one per site as well: records carry `site`, and a
		# second copy of every line would double the disk this uses.
		logger = frappe.logger(
			LOGGER_NAME,
			allow_site=False,
			max_size=_int_conf("grievance_log_max_bytes", DEFAULT_MAX_BYTES, MIN_MAX_BYTES),
			file_count=_int_conf("grievance_log_backup_count", DEFAULT_BACKUP_COUNT, 1),
		)
		for handler in logger.handlers:
			handler.setFormatter(JsonFormatter())
		# Frappe's default level is ERROR on a production bench, which would drop every event
		# below. Operators choose the level here instead.
		logger.setLevel(_level_from_conf())
		logger._oan_configured = True
		return logger


def _with_user_refs(fields: dict) -> dict:
	"""Add `<field>_ref` beside every user field.

	A masked user (`j***@example.com`) cannot tell two officers with the same initial and
	domain apart, so a case moving between them would read as moving to itself. The ref is the
	same for the same account and different for another, and reveals neither.
	"""
	extra = {}
	for key, value in fields.items():
		if str(key).lower() in _USER_KEYS and isinstance(value, str) and value and f"{key}_ref" not in fields:
			if ref := user_ref(value):
				extra[f"{key}_ref"] = ref
	return {**fields, **extra} if extra else fields


def emit(event: str, level: int = logging.INFO, **fields) -> None:
	"""Write one event. Never raises: logging must not break the work it describes."""
	try:
		logger = get_logger()
		if not logger.isEnabledFor(level):
			return
		record = {"correlation_id": correlation_id(), "site": getattr(frappe.local, "site", None)}
		record.update(redact(_with_user_refs(fields)))
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

	Called from `except` blocks, so it never raises: a failure to store the row is noted on the
	structured line (`error_log_failed`) rather than replacing the error being handled.

	The Error Log row has the correlation id on its first line, then what `frappe.log_error`
	would have stored (with no message, the traceback with each frame's variables), scrubbed.
	Variables named for a credential or a personal field are blanked, and an email, phone number
	or token anywhere in the text is masked. When the text is too long the *end* is kept, because
	a traceback's exception message is its last line.

	The structured line carries the title, the exception type, that last line as
	`summary`, and the tail of the plain traceback as `detail`. `kwargs` go to
	`frappe.log_error` (for example `defer_insert`).
	"""
	exc = sys.exc_info()[0]
	exc_type = exc.__name__ if exc else None

	if message is None:
		body = frappe.get_traceback(with_context=True)
		plain = frappe.get_traceback()
	else:
		body = plain = message

	cid = correlation_id()
	stored = scrub_traceback(body)
	if cid:
		stored = f"correlation_id: {cid}\n{stored}"
	# One line: `frappe.log_error` takes a title with a line break to be a swapped traceback.
	title = scrub_text(" ".join(str(title or "Error").split()), ERROR_TITLE_LIMIT)

	stored_ok = True
	try:
		frappe.log_error(title=title, message=stored, **kwargs)
	except Exception:
		stored_ok = False

	emit(
		"error",
		logging.ERROR,
		title=Scrubbed(title),
		exception=exc_type,
		summary=Scrubbed(scrub_text(last_line(plain), ERROR_MESSAGE_LIMIT)),
		detail=Scrubbed(scrub_text(plain, ERROR_DETAIL_LIMIT, keep="tail")),
		error_log_failed=None if stored_ok else True,
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
