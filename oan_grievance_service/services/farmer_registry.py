"""Farmer lookup in the OpenG2P Farmer Registry (Staff API), to pre-fill a submitter.

The caller's own IAM session is forwarded: the registry sees the Development Agent,
not this service, so its role checks decide what the agent may read. Configure the
registry in Grievance Settings.
"""

import logging
import uuid

import frappe
import requests
from frappe import _
from frappe.utils import now_datetime
from oan_auth_service.api import iam_tokens
from oan_auth_service.api import operational_log as oplog

SEARCH_PATH = "/register-data/search_in_a_register"
RECORD_PATH = "/register-data/get_subject_record"
TAB_PATH = "/register-data/get_tab_records"
FARMER_TAB = "farmer_farmer_tab"
LOCATION_TAB = "farmer_location_tab"
TIMEOUT_SECONDS = 15
# The Staff API rejects any request whose pagination block lacks these, even for a single-record read.
EMPTY_PAGINATION = {"current_page": 1, "page_size": 50, "sort_by": "", "filter_by": "", "search_text": ""}


def find_farmer(fayda_id: str) -> dict:
	"""Registry ID and name of the farmer with this Fayda ID (one search call)."""
	base_url, register_id = _registry()
	wanted = _digits(fayda_id)

	hits = _post(
		_forwarded_session(),
		f"{base_url}{SEARCH_PATH}",
		{"register_id": register_id},
		pagination={"current_page": 1, "page_size": 10, "search_text": wanted},
	)
	# The search is a text match, so only a hit with exactly this Fayda ID counts.
	hit = next((h for h in hits or [] if _digits(h.get("foundational_id")) == wanted), None)
	if not hit:
		frappe.throw(_("No farmer with Fayda ID '{0}' was found.").format(fayda_id), frappe.DoesNotExistError)

	return {
		"registry_id": hit["internal_record_id"],
		"fayda_id": wanted,
		"submitter_name": hit.get("record_name"),
	}


def farmer_contact_details(registry_id: str) -> dict:
	"""Phone, email and location of a farmer found by `find_farmer`."""
	base_url, register_id = _registry()
	session = _forwarded_session()

	record = _post(
		session,
		f"{base_url}{RECORD_PATH}",
		{"subject_register_id": register_id, "subject_record_id": registry_id},
	)
	record = dict(record or {})
	for tab in (FARMER_TAB, LOCATION_TAB):
		sections = _post(
			session,
			f"{base_url}{TAB_PATH}",
			{"subject_register_id": register_id, "subject_record_id": registry_id, "tab_id": tab},
		)
		_merge_tab_rows(record, sections)

	return {
		"registry_id": registry_id,
		"contact_mobile": _primary_phone(record),
		"contact_email": record.get("email") or record.get("email_address"),
		"region": record.get("region_name"),
		"zone": record.get("zone_name"),
		"woreda": record.get("woreda_name"),
		"kebele": record.get("kebele_name"),
	}


def _registry() -> tuple[str, str]:
	settings = frappe.get_cached_doc("Grievance Settings")
	base_url = (settings.farmer_registry_url or "").rstrip("/")
	if not (base_url and settings.farmer_register_id):
		frappe.throw(_("The farmer registry is not configured."), frappe.ValidationError)
	return base_url, settings.farmer_register_id


def _failed(url: str, **fields) -> None:
	"""One `farmer_registry.error` line: which call failed and how, never what it carried.

	The request payload and the response body are farmer records (names, phone numbers, national
	IDs), and a scrubber cannot find a name in JSON, so neither is logged: only the status, the
	registry's own error reason, the *names* of the payload fields, and the size of the reply.
	A log line, not `frappe.log_error`: that row is rolled back when the request then raises.
	"""
	oplog.emit("farmer_registry.error", logging.ERROR, url=url, **fields)


def _digits(value) -> str:
	return "".join(ch for ch in str(value or "") if ch.isdigit())


def _forwarded_session() -> requests.Session:
	"""A session carrying the caller's IAM access token and a fresh CSRF pair."""
	request = frappe.local.request
	token = _bearer(request.headers.get("Authorization")) or _cookie(request, iam_tokens.ACCESS_COOKIE)
	if not token:
		frappe.throw(_("An agent sign-in is required to search the farmer registry."), frappe.PermissionError)

	# The Staff API wants the double-submit pair (the same value as header and cookie) on every POST. The
	# UI proxy forwards only the bearer token, so the pair is made here rather than taken from the caller.
	csrf = frappe.generate_hash(length=32)
	session = requests.Session()
	session.headers.update(
		{"Authorization": f"Bearer {token}", iam_tokens.CSRF_HEADER: csrf, "Accept": "application/json"}
	)
	session.cookies.set(iam_tokens.CSRF_COOKIE, csrf)
	return session


def _bearer(header: str | None) -> str:
	scheme, _sep, token = (header or "").partition(" ")
	return token.strip() if scheme.lower() == "bearer" else ""


def _cookie(request, base_name: str) -> str:
	"""A cookie by its base name, whatever login-option prefix it carries."""
	for name, value in request.cookies.items():
		if name.endswith(base_name) and value:
			return value
	return ""


def _post(session: requests.Session, url: str, payload: dict, pagination: dict | None = None):
	envelope = {
		"request_header": {
			"request_id": str(uuid.uuid4()),
			"request_timestamp": now_datetime().isoformat(),
			"sender_app_mnemonic": "oan_grievance_service",
			"sender_app_url": frappe.utils.get_url(),
			"instance_id": "",
		},
		"request_body": {
			"request_payload": payload,
			"pagination_request": {**EMPTY_PAGINATION, **(pagination or {})},
		},
	}
	try:
		response = session.post(url, json=envelope, timeout=TIMEOUT_SECONDS)
	except requests.RequestException as e:
		_failed(url, reason="unreachable", exception=type(e).__name__, detail=str(e))
		frappe.throw(_("The farmer registry is unavailable."), frappe.ValidationError)

	if response.status_code != 200:
		_failed(
			url,
			status=response.status_code,
			payload_keys=sorted(payload),
			response_bytes=len(response.content),
		)
	if response.status_code in (401, 403):
		frappe.throw(_("The registry did not accept this sign-in."), frappe.PermissionError)
	if not response.ok:
		frappe.throw(_("Farmer lookup failed."), frappe.ValidationError)

	body = response.json()
	header = body.get("response_header") or {}
	if str(header.get("response_status", "")).upper() == "ERROR":
		message = header.get("response_error_message") or ""
		_failed(
			url,
			status=response.status_code,
			registry_status="ERROR",
			# Registry-authored text, scrubbed by `emit` for emails, phone numbers, IDs and credentials.
			reason=message,
			payload_keys=sorted(payload),
			response_bytes=len(response.content),
		)
		frappe.throw(_(message or "Farmer lookup failed."))
	return (body.get("response_body") or {}).get("response_payload")


def _merge_tab_rows(record: dict, sections) -> None:
	"""Fold get_tab_records sections into the record; phone rows go under `_phones`."""
	for section in sections or []:
		rows = [r for r in (section.get("records") or []) if isinstance(r, dict)]
		if not rows:
			continue
		if "phone_number" in rows[0]:
			record.setdefault("_phones", []).extend(rows)
			continue
		for key, value in rows[0].items():
			record.setdefault(key, value)


def _primary_phone(record: dict) -> str | None:
	phones = record.get("_phones") or []
	primary = next((p for p in phones if p.get("is_primary")), phones[0] if phones else {})
	numbers = [p for p in record.get("phone_numbers") or [] if isinstance(p, dict) and p.get("number")]
	from_record = next((p for p in numbers if p.get("is_primary")), numbers[0] if numbers else {})
	return primary.get("phone_number") or from_record.get("number") or record.get("phone_number")
