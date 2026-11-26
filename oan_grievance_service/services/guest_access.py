"""Link-and-PIN access to a sensitive anonymous case (STG-518).

The submitter of a sensitive anonymous case is sent, once, a link and a PIN. With both they open
that one case, without signing in, for as long as it is not closed. Who they are does not matter:
a submitter with a login gets the link too, and can still sign in.

What is kept, and where:

- `Grievance Guest Access`, one row per case: an HMAC-SHA256 of the link's token and a salted
  PBKDF2 of the PIN, both keyed with a pepper from site_config (`guest_access_pepper`), so a copy
  of the database alone cannot test a 6-digit PIN. Neither value can be read back.
- `pending_secret` (a Password field, so encrypted) holds the token and PIN only until the
  message carrying them has gone; `clear_pending_secret_when_sent` then removes it.
- The Grievance Notification Log holds the message masked; `outbound_text` puts the link and PIN
  back only into the text that leaves the system, as `notifications._outbound_message` does for
  a sensitive case's status PIN.
- A verified session lives in Redis under a hash of its token, with an idle and an absolute limit.

A guest request runs as one service user that holds only the `Grievance Guest` role
(setup/service_users.py), with `frappe.local.guest_grievance` naming the case its session is
bound to. permissions.py lets that user reach that case and nothing else.

Undecided product values (Q2, Q3, Q5, Q8) are read from site_config with placeholder defaults,
in `settings()` and `link_ending_states()`, so each is one place to change.
"""

import hashlib
import hmac
import logging
import math
import secrets
import time
from dataclasses import dataclass
from functools import wraps

import frappe
from frappe import _
from frappe.translate import print_language
from frappe.utils import add_to_date, cint, get_datetime, now_datetime, sbool
from frappe.utils.password import get_decrypted_password, remove_encrypted_password
from oan_auth_service.api import operational_log as oplog
from oan_auth_service.api.utils import check_rate_limit, error_response
from passlib.hash import pbkdf2_sha256

from oan_grievance_service.services import classification
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import ticket_number as tn

ACCESS = "Grievance Guest Access"
NOTIFICATION_LOG = "Grievance Notification Log"

STATUS_ACTIVE = "Active"
STATUS_LOCKED = "Locked"
STATUS_CLOSED = "Closed"

SOURCE_FILING = "Filing"
SOURCE_BACKFILL = "Backfill"
EVENT_FOR_SOURCE = {
	SOURCE_FILING: C.EVENT_GUEST_ACCESS_ISSUED,
	SOURCE_BACKFILL: C.EVENT_GUEST_ACCESS_BACKFILL,
}

# 16 bytes is 128 bits; `token_urlsafe` writes them as 22 URL-safe characters.
TOKEN_BYTES = 16
# What a stored message shows in place of the token and the PIN.
MASK = "******"

# The guest session travels in its own header, not `Authorization`: the auth middleware checks
# any bearer token on a public path as a JWT and would refuse this one.
SESSION_HEADER = "X-Grievance-Guest-Session"
SESSION_KEY_PREFIX = "grievance_guest_session:"
SESSION_TOKEN_BYTES = 32
MAX_SESSION_TOKEN_LENGTH = 128

# Error codes the frontend branches on.
CODE_INVALID = "GUEST_ACCESS_INVALID"
CODE_LOCKED = "GUEST_ACCESS_LOCKED"
CODE_CLOSED = "GUEST_ACCESS_CLOSED"
CODE_SESSION_EXPIRED = "GUEST_SESSION_EXPIRED"
CODE_NOT_CONFIGURED = "GUEST_ACCESS_UNAVAILABLE"


@dataclass(frozen=True)
class Settings:
	pepper: str | None
	base_url: str | None
	pin_length: int
	max_attempts: int
	lock_minutes: int
	idle_minutes: int
	absolute_minutes: int
	verify_ip_limit: int
	verify_ticket_limit: int
	verify_window_seconds: int
	message_limit: int
	message_window_seconds: int

	@property
	def ready(self) -> bool:
		return bool(self.pepper and self.base_url)


def settings() -> Settings:
	"""The feature's settings, from site_config.

	`guest_access_pepper` (a long random secret) and `guest_access_base_url` (the portal's origin,
	e.g. https://grievance.example.org) have no default: without them no link is issued and
	verification refuses everything. Every other value is a PLACEHOLDER until product decides it:
	PIN length and lockout (Q3), session timeouts (Q5, the portal's own idle defaults).
	"""
	conf = frappe.conf
	return Settings(
		pepper=conf.get("guest_access_pepper") or None,
		base_url=(conf.get("guest_access_base_url") or "").strip().rstrip("/") or None,
		# PLACEHOLDER (Q3): the status PIN's length.
		pin_length=cint(conf.get("guest_access_pin_length")) or classification.PIN_LENGTH,
		# PLACEHOLDER (Q3): wrong PINs in a row before the link locks, and for how long.
		max_attempts=cint(conf.get("guest_access_max_attempts")) or 5,
		lock_minutes=cint(conf.get("guest_access_lock_minutes")) or 15,
		# PLACEHOLDER (Q5): session idle and absolute limits.
		idle_minutes=cint(conf.get("guest_access_idle_minutes")) or 15,
		absolute_minutes=cint(conf.get("guest_access_absolute_minutes")) or 60,
		# PLACEHOLDER: verify attempts per client address and per ticket, per window.
		verify_ip_limit=cint(conf.get("guest_access_verify_ip_limit")) or 30,
		verify_ticket_limit=cint(conf.get("guest_access_verify_ticket_limit")) or 10,
		verify_window_seconds=cint(conf.get("guest_access_verify_window_seconds")) or 15 * 60,
		# PLACEHOLDER: replies a guest session may post per window.
		message_limit=cint(conf.get("guest_access_message_limit")) or 30,
		message_window_seconds=cint(conf.get("guest_access_message_window_seconds")) or 60 * 60,
	)


def link_ending_states() -> tuple[str, ...]:
	"""The case states in which the link and PIN stop working.

	PLACEHOLDER (Q2): Closed only, the safest reading of "until the grievance is closed". Closed
	has no outbound transition, so the reopen question does not arise for it. A site may list
	others (Rejected, Duplicate) in `guest_access_ending_states`.
	"""
	configured = frappe.conf.get("guest_access_ending_states")
	return tuple(configured) if configured else (C.STATE_CLOSED,)


def link_has_ended(grievance) -> bool:
	return (grievance.get("workflow_state") or "") in link_ending_states()


def is_eligible(grievance) -> bool:
	"""Whether a case gets a link: sensitive, anonymous, reachable, and not closed.

	Whether the submitter has a login does not matter. A case with no mobile number and no email
	address gets no link (Q9).
	"""
	return bool(
		grievance.get("is_sensitive")
		and grievance.get("is_anonymous")
		and (grievance.get("contact_mobile") or grievance.get("contact_email"))
		and not link_has_ended(grievance)
	)


# Credentials
# -----------


def token_hash(pepper: str, token: str) -> str:
	"""HMAC-SHA256 of the link token. The token is 128 random bits, so no slow hash is needed."""
	return hmac.new(pepper.encode(), f"token:{token}".encode(), hashlib.sha256).hexdigest()


def _peppered_pin(pepper: str, pin: str) -> str:
	return hmac.new(pepper.encode(), f"pin:{pin}".encode(), hashlib.sha256).hexdigest()


def pin_hash(pepper: str, pin: str) -> str:
	"""Salted PBKDF2-SHA256 of the PIN, keyed with the pepper first."""
	return pbkdf2_sha256.hash(_peppered_pin(pepper, pin))


def pin_matches(pepper: str, pin: str, stored: str | None) -> bool:
	if not stored:
		return False
	try:
		return pbkdf2_sha256.verify(_peppered_pin(pepper, pin), stored)
	except (ValueError, TypeError):
		return False


_DUMMY_PIN_HASH = None


def _spend_a_pin_check(pepper: str, pin: str) -> None:
	"""Run one PIN check against nothing, so a refusal before the PIN costs what one after it does.

	Without it, an unknown ticket or a wrong token answers measurably faster than a wrong PIN,
	which tells a caller that the half they guessed was right.
	"""
	global _DUMMY_PIN_HASH
	if _DUMMY_PIN_HASH is None:
		_DUMMY_PIN_HASH = pbkdf2_sha256.hash(secrets.token_hex(16))
	pin_matches(pepper or "-", pin or "", _DUMMY_PIN_HASH)


# Issuing
# -------


def issue_for(grievance, source: str = SOURCE_FILING) -> str | None:
	"""Give an eligible case its link and PIN and queue the message that carries them.

	Returns the access record's name, or None when the case is not eligible, already has one,
	the feature is not configured, or no channel could take the message (no Notification enabled
	for the event, or no address on the case). In that last case the record is not kept, so the
	backfill can issue it later. A filing sends at once; a backfill leaves its messages to the
	notification sweep, which paces them.
	"""
	from oan_grievance_service.services import notifications

	if not is_eligible(grievance) or frappe.db.exists(ACCESS, {"grievance": grievance.name}):
		return None
	conf = settings()
	if not conf.ready:
		oplog.emit(
			"guest_access.not_configured",
			logging.WARNING,
			grievance=grievance.name,
			missing=[
				key
				for key, value in (
					("guest_access_pepper", conf.pepper),
					("guest_access_base_url", conf.base_url),
				)
				if not value
			],
		)
		return None

	token = secrets.token_urlsafe(TOKEN_BYTES)
	pin = classification.new_pin(conf.pin_length)
	access = frappe.get_doc(
		{
			"doctype": ACCESS,
			"grievance": grievance.name,
			"status": STATUS_ACTIVE,
			"source": source,
			"token_hash": token_hash(conf.pepper, token),
			"pin_hash": pin_hash(conf.pepper, pin),
			"pending_secret": f"{token}:{pin}",
		}
	).insert(ignore_permissions=True)

	rows = notifications.queue(grievance, EVENT_FOR_SOURCE[source], dispatch_now=source == SOURCE_FILING)
	if not rows:
		frappe.delete_doc(ACCESS, access.name, force=True, ignore_permissions=True)
		oplog.emit("guest_access.not_sent", logging.WARNING, grievance=grievance.name, source=source)
		return None

	oplog.emit(
		"guest_access.issued",
		grievance=grievance.name,
		source=source,
		channels=sorted({row.channel for row in rows}),
	)
	return access.name


def issue_on_filing(grievance) -> None:
	"""`issue_for` at filing, which it must never break: a failure is logged and the case stands."""
	save_point = "guest_access_issue"
	frappe.db.savepoint(save_point)
	try:
		issue_for(grievance, SOURCE_FILING)
	except Exception:
		frappe.db.rollback(save_point=save_point)
		oplog.log_error(title="Guest access link not issued", message=frappe.get_traceback())


def message_text(grievance, event_code: str, token: str | None = None, pin: str | None = None):
	"""(message, subject) carrying the link and PIN, masked unless both are given.

	The message names the reference number, the link and the PIN, and nothing else: no category,
	office or officer, and not the word "sensitive".
	TODO: copy pending (Q8). The English and Amharic wording and the base URL are product's.
	"""
	ref = tn.display(grievance.ticket_number) or grievance.name
	base = settings().base_url or ""
	link = f"{base}/g/{grievance.ticket_number}#k={token or MASK}"
	shown_pin = pin or MASK
	subject = _("Grievance {0}").format(ref)
	if event_code == C.EVENT_GUEST_ACCESS_BACKFILL:
		message = _(
			"You can now view your grievance {0} online at {1} PIN: {2}. Do not share this message."
		).format(ref, link, shown_pin)
	else:
		message = _("Grievance {0}. View it at {1} PIN: {2}. Do not share this message.").format(
			ref, link, shown_pin
		)
	return message, subject


def _access_name(grievance_name: str) -> str | None:
	return frappe.db.get_value(ACCESS, {"grievance": grievance_name}, "name")


def shown_on_submit() -> bool:
	"""TEMPORARY, until SMS and email are delivering: whether the filing answer shows the link and PIN.

	Off unless site_config sets `guest_access_show_on_submit`. Turning it off again, once the
	messages arrive, is the whole removal on this side.
	"""
	return sbool(frappe.conf.get("guest_access_show_on_submit"))


def link_for_display(grievance) -> dict | None:
	"""The case's link and PIN, plain, for the submitter's own screen right after filing.

	Only while `shown_on_submit` is on and the message carrying them is still unsent (the
	secret is cleared once it has gone), so it can be shown in the filing's answer and nowhere
	later. The caller decides who may see it: never someone who filed on the submitter's behalf.
	"""
	if not shown_on_submit():
		return None
	name = _access_name(grievance.name)
	secret = get_decrypted_password(ACCESS, name, "pending_secret", raise_exception=False) if name else None
	base = settings().base_url
	if not secret or ":" not in secret or not base:
		return None
	token, pin = secret.split(":", 1)
	return {"link": f"{base}/g/{grievance.ticket_number}#k={token}", "pin": pin}


def outbound_text(row, grievance) -> str:
	"""The text a queued link row sends: its message with the token and PIN put in.

	Raises when the secret is gone (already sent, or the record removed), so the row is marked
	Failed rather than sent masked.
	"""
	name = _access_name(grievance.name)
	secret = get_decrypted_password(ACCESS, name, "pending_secret", raise_exception=False) if name else None
	if not secret or ":" not in secret:
		raise ValueError("No pending access link for this grievance; nothing to send.")
	token, pin = secret.split(":", 1)
	with print_language(row.get("language")):
		return message_text(grievance, row.event, token, pin)[0]


def clear_pending_secret_when_sent(grievance_name: str) -> None:
	"""Remove the plain token and PIN once no link message for the case is left to send.

	Called after each link row is handled. A failed row is not retried, so a failure counts as
	handled too: nothing would ever read the secret again.
	"""
	still_queued = frappe.db.exists(
		NOTIFICATION_LOG,
		{"grievance": grievance_name, "event": ["in", list(C.GUEST_ACCESS_EVENTS)], "status": "Queued"},
	)
	if still_queued:
		return
	name = _access_name(grievance_name)
	if not name:
		return
	remove_encrypted_password(ACCESS, name, "pending_secret")
	frappe.db.set_value(ACCESS, name, "pending_secret", None, update_modified=False)


# Verifying
# ---------


@dataclass
class VerifyResult:
	ok: bool
	code: str | None = None
	status: int = 200
	session_token: str | None = None
	grievance: str | None = None
	retry_after_seconds: int | None = None
	expires_at: float | None = None


def _security_event(event: str, **fields) -> None:
	oplog.emit(f"security.guest_access.{event}", logging.WARNING, **fields)


def verify(ticket_number: str, token: str, pin: str, client_ip: str | None) -> VerifyResult:
	"""Check a link's ticket and token and the PIN typed, and open a session on success.

	An unknown ticket, a wrong token and a wrong PIN all give the same answer, at the same cost.
	Only a wrong PIN with the right link counts toward the lock: counting a wrong token would let
	anyone who knows a ticket number (they are sequential) keep its submitter locked out.
	Locked and closed are told only to a caller who holds the right link.
	"""
	conf = settings()
	invalid = VerifyResult(ok=False, code=CODE_INVALID, status=401)
	if not conf.ready:
		_spend_a_pin_check(conf.pepper, pin)
		oplog.emit("guest_access.not_configured", logging.WARNING, during="verify")
		return VerifyResult(ok=False, code=CODE_NOT_CONFIGURED, status=503)

	normalized = tn.normalize(ticket_number or "")
	check_rate_limit(
		f"grievance_guest:verify:ip:{client_ip or 'unknown'}",
		conf.verify_ip_limit,
		conf.verify_window_seconds,
	)
	check_rate_limit(
		f"grievance_guest:verify:ticket:{normalized}", conf.verify_ticket_limit, conf.verify_window_seconds
	)

	grievance = frappe.db.get_value(
		"Grievance", {"ticket_number": normalized}, ["name", "workflow_state"], as_dict=True
	)
	access = (
		frappe.db.get_value(
			ACCESS,
			{"grievance": grievance.name},
			["name", "token_hash", "pin_hash", "status", "failed_attempts", "locked_until"],
			as_dict=True,
		)
		if grievance
		else None
	)
	expected = (access.token_hash if access else None) or ""
	if not (access and hmac.compare_digest(token_hash(conf.pepper, token or ""), expected)):
		_spend_a_pin_check(conf.pepper, pin)
		_security_event("refused", reason="unknown_link", grievance=grievance.name if grievance else None)
		return invalid

	now = now_datetime()
	if access.locked_until and get_datetime(access.locked_until) > now:
		_spend_a_pin_check(conf.pepper, pin)
		_security_event("refused", reason="locked", grievance=grievance.name)
		retry = math.ceil((get_datetime(access.locked_until) - now).total_seconds())
		return VerifyResult(ok=False, code=CODE_LOCKED, status=429, retry_after_seconds=max(retry, 1))

	if not pin_matches(conf.pepper, pin or "", access.pin_hash):
		attempts = cint(access.failed_attempts) + 1
		updates = {"failed_attempts": attempts}
		if attempts >= conf.max_attempts:
			updates = {
				"failed_attempts": 0,
				"locked_until": add_to_date(now, minutes=conf.lock_minutes),
				"status": STATUS_LOCKED,
			}
			_security_event("locked", grievance=grievance.name, minutes=conf.lock_minutes)
		else:
			_security_event("refused", reason="wrong_pin", grievance=grievance.name, attempts=attempts)
		frappe.db.set_value(ACCESS, access.name, updates, update_modified=False)
		return invalid

	if link_has_ended(grievance):
		frappe.db.set_value(
			ACCESS, access.name, {"status": STATUS_CLOSED, "failed_attempts": 0}, update_modified=False
		)
		return VerifyResult(ok=False, code=CODE_CLOSED, status=410)

	if access.status != STATUS_ACTIVE or cint(access.failed_attempts) or access.locked_until:
		frappe.db.set_value(
			ACCESS,
			access.name,
			{"status": STATUS_ACTIVE, "failed_attempts": 0, "locked_until": None},
			update_modified=False,
		)
	session_token, expires_at = _open_session(grievance.name, access.name, conf)
	oplog.emit("guest_access.verified", grievance=grievance.name)
	return VerifyResult(ok=True, session_token=session_token, grievance=grievance.name, expires_at=expires_at)


# Sessions
# --------


def _session_key(session_token: str) -> str:
	# Keyed by a hash, so a Redis dump holds no usable session.
	return SESSION_KEY_PREFIX + hashlib.sha256(session_token.encode()).hexdigest()


def _session_ttl(data: dict, conf: Settings) -> int:
	remaining = data["absolute_at"] - time.time()
	return max(1, math.ceil(min(conf.idle_minutes * 60, remaining)))


def _open_session(grievance_name: str, access_name: str, conf: Settings) -> tuple[str, float]:
	session_token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
	data = {
		"grievance": grievance_name,
		"access": access_name,
		"absolute_at": time.time() + conf.absolute_minutes * 60,
	}
	frappe.cache.set_value(_session_key(session_token), data, expires_in_sec=_session_ttl(data, conf))
	return session_token, data["absolute_at"]


def end_session(session_token: str | None) -> None:
	if session_token and len(session_token) <= MAX_SESSION_TOKEN_LENGTH:
		frappe.cache.delete_value(_session_key(session_token))


def session_token_from_request() -> str | None:
	raw = (frappe.get_request_header(SESSION_HEADER) or "").strip()
	return raw if raw and len(raw) <= MAX_SESSION_TOKEN_LENGTH else None


@dataclass
class SessionCheck:
	ok: bool
	code: str | None = None
	status: int = 200
	grievance: str | None = None
	expires_at: float | None = None
	idle_seconds: int | None = None


def touch_session(session_token: str | None) -> SessionCheck:
	"""Check a session and slide its idle limit forward. Ends it when its case has closed.

	The absolute limit never moves: the idle window is cut short so the key expires by then.
	"""
	expired = SessionCheck(ok=False, code=CODE_SESSION_EXPIRED, status=401)
	if not session_token:
		return expired
	key = _session_key(session_token)
	data = frappe.cache.get_value(key)
	if not data or time.time() >= data.get("absolute_at", 0):
		frappe.cache.delete_value(key)
		return expired

	grievance = frappe.db.get_value("Grievance", data["grievance"], ["name", "workflow_state"], as_dict=True)
	access_status = frappe.db.get_value(ACCESS, data["access"], "status")
	if not grievance or not access_status:
		frappe.cache.delete_value(key)
		return expired
	if access_status == STATUS_CLOSED or link_has_ended(grievance):
		frappe.cache.delete_value(key)
		if access_status != STATUS_CLOSED:
			frappe.db.set_value(ACCESS, data["access"], "status", STATUS_CLOSED, update_modified=False)
		return SessionCheck(ok=False, code=CODE_CLOSED, status=410)

	conf = settings()
	ttl = _session_ttl(data, conf)
	frappe.cache.set_value(key, data, expires_in_sec=ttl)
	return SessionCheck(
		ok=True,
		grievance=grievance.name,
		expires_at=data["absolute_at"],
		idle_seconds=min(conf.idle_minutes * 60, ttl),
	)


def _error_message(code: str) -> str:
	if code == CODE_LOCKED:
		return _("Too many wrong PINs. Try again later.")
	if code == CODE_CLOSED:
		return _("This grievance is closed, so this link no longer works.")
	if code == CODE_SESSION_EXPIRED:
		return _("Your session ended. Open the link again.")
	if code == CODE_NOT_CONFIGURED:
		return _("Viewing a grievance by link is not available right now.")
	return _("Check the SMS/Email for the correct PIN.")


def guest_error(code: str, status: int, details: dict | None = None) -> dict:
	"""The error envelope of a guest route, with its HTTP status set."""
	frappe.response["http_status_code"] = status
	return error_response(message=_error_message(code), code=code, details=details)


def as_guest(fn):
	"""Run a guest route as the guest service user, bound to its session's case.

	The wrapped function gets the case's name as `grievance`. A missing, expired or ended session
	is answered here and the route never runs. The user and binding are restored afterwards.
	"""
	from oan_grievance_service.setup.service_users import GUEST_USER_EMAIL

	@wraps(fn)
	def wrapper(*args, **kwargs):
		# The case is the session's. One the client names is dropped, never used.
		kwargs.pop("grievance", None)
		check = touch_session(session_token_from_request())
		if not check.ok:
			return guest_error(check.code, check.status)

		previous_user = frappe.session.user
		form_dict = frappe.local.form_dict
		# The guest user holds one role, which reaches only the case named next.
		frappe.set_user(GUEST_USER_EMAIL)  # nosemgrep: frappe-semgrep-rules.rules.security.frappe-setuser
		frappe.local.form_dict = form_dict
		frappe.local.guest_grievance = check.grievance
		try:
			return fn(*args, grievance=check.grievance, **kwargs)
		finally:
			frappe.local.guest_grievance = None
			frappe.set_user(previous_user)  # nosemgrep: frappe-semgrep-rules.rules.security.frappe-setuser
			frappe.local.form_dict = form_dict

	return wrapper


def bound_grievance() -> str | None:
	"""The case the current guest session is bound to, or None outside a guest request."""
	return getattr(frappe.local, "guest_grievance", None)


# Backfill
# --------


def backfill_candidates() -> list[str]:
	"""Cases filed before the feature that would get a link now: eligible and without a record."""
	grievance = frappe.qb.DocType("Grievance")
	access = frappe.qb.DocType(ACCESS)
	query = (
		frappe.qb.from_(grievance)
		.left_join(access)
		.on(access.grievance == grievance.name)
		.select(grievance.name)
		.where(grievance.is_sensitive == 1)
		.where(grievance.is_anonymous == 1)
		.where(grievance.workflow_state.notin(list(link_ending_states())))
		.where(
			(grievance.contact_mobile.isnotnull() & (grievance.contact_mobile != ""))
			| (grievance.contact_email.isnotnull() & (grievance.contact_email != ""))
		)
		.where(access.name.isnull())
		.orderby(grievance.creation)
	)
	return [row[0] for row in query.run()]


def backfill_existing(dry_run: bool = True, limit: int | None = None) -> dict:
	"""Issue a link and PIN, once, to each open sensitive anonymous case that has none.

	Run by hand after deploy, never by a migrate, so no message leaves in the middle of one:

		bench --site <site> execute oan_grievance_service.services.guest_access.backfill_existing
		bench --site <site> execute oan_grievance_service.services.guest_access.backfill_existing \\
			--kwargs "{'dry_run': False}"

	The first, a dry run, only counts. The real run creates the records and queues the messages,
	and the notification sweep sends them at its own pace (100 per run, every 5 minutes). A case
	that already has a record is skipped, so running it again sends nothing twice. Commits after
	each case, so an interrupted run keeps what it did. `bench execute` prints what it returns.
	"""
	candidates = backfill_candidates()
	if limit:
		candidates = candidates[: cint(limit)]
	# A dry run unless told plainly otherwise: a "0" or "false" from the command line counts as no.
	if sbool(dry_run):
		return {"dry_run": True, "matching": len(candidates), "configured": settings().ready}

	issued = skipped = failed = 0
	for name in candidates:
		try:
			if issue_for(frappe.get_doc("Grievance", name), SOURCE_BACKFILL):
				issued += 1
			else:
				skipped += 1
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		except Exception:
			frappe.db.rollback()
			failed += 1
			oplog.log_error(title="Guest access backfill failed for a case", message=frappe.get_traceback())
	result = {
		"dry_run": False,
		"matching": len(candidates),
		"issued": issued,
		"skipped": skipped,
		"failed": failed,
	}
	oplog.emit("guest_access.backfill", **result)
	return result
