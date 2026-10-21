# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Adapters that read providers from an external source for the directory sync.

An adapter returns rows in one shape, whatever the source looks like:

	{"external_ref": "PB-0452", "provider_name": "HDFC Bank", "area_name": "Addis Ababa"}

What belongs here: talking to the source and turning its answer into rows.

What does not belong here: storing anything. That is service_provider_sync.py.

Response bodies are never logged or put in an error message: the login response carries a
token. Errors say what failed, not what the source said.
"""

import requests
from frappe import _

from oan_grievance_service.grievance_masters.doctype.grievance_service_provider_source.grievance_service_provider_source import (
	AUTH_LOGIN,
)

# Connect, then read. A source that stalls must not hold a worker for the whole job timeout.
REQUEST_TIMEOUT = (10, 30)


class SourceError(Exception):
	"""The source could not be read. The message is safe to show an admin."""


class ApiAdapter:
	"""A JSON API that signs in with a login call or a stored bearer token (the A2C catalog)."""

	def __init__(self, source):
		self.source = source

	def fetch(self) -> tuple[list[dict], list[str]]:
		"""Return (rows, warnings). Raises SourceError when the source cannot be read."""
		token = self._token()
		items = self._get_list(token)
		return self._rows(items)

	def _token(self) -> str:
		source = self.source
		if source.auth_mode != AUTH_LOGIN:
			token = source.get_password("api_token", raise_exception=False)
			if not token:
				raise SourceError(_("No bearer token is stored for this source."))
			return token

		password = source.get_password("api_password", raise_exception=False)
		if not (source.api_username and password):
			raise SourceError(_("No login is stored for this source."))
		body = self._request(
			"POST",
			source.login_path,
			json={"usr": source.api_username, "pwd": password},
			what=_("Login"),
		)
		data = body.get("data") if isinstance(body, dict) else None
		token = (data.get("token") or data.get("access_token")) if isinstance(data, dict) else None
		if not token:
			raise SourceError(_("The login answered without a token."))
		return token

	def _get_list(self, token: str) -> list:
		source = self.source
		body = self._request(
			"GET",
			source.list_path,
			headers={"Authorization": f"Bearer {token}"},
			what=_("Provider list"),
		)
		data = body.get("data") if isinstance(body, dict) else None
		items = data.get(source.list_key) if isinstance(data, dict) else None
		if not isinstance(items, list):
			raise SourceError(
				_("The answer has no list under data.{0}. Check the List Key.").format(source.list_key)
			)
		return items

	def _request(self, method: str, path: str, what: str, **kwargs) -> dict:
		url = f"{self.source.api_base_url}{path}"
		try:
			response = requests.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
		except requests.RequestException as exc:
			raise SourceError(_("{0} request failed: {1}.").format(what, type(exc).__name__)) from exc
		if response.status_code != 200:
			raise SourceError(_("{0} was refused: HTTP {1}.").format(what, response.status_code))
		try:
			return response.json()
		except ValueError as exc:
			raise SourceError(_("{0} did not answer with JSON.").format(what)) from exc

	def _rows(self, items: list) -> tuple[list[dict], list[str]]:
		source = self.source
		rows, warnings, seen = [], [], set()
		for position, item in enumerate(items, 1):
			if not isinstance(item, dict):
				warnings.append(_("Item {0} is not an object and was skipped.").format(position))
				continue
			ref = str(item.get(source.map_external_id) or "").strip()
			name = str(item.get(source.map_name) or "").strip()
			if not (ref and name):
				warnings.append(_("Item {0} has no ID or name and was skipped.").format(position))
				continue
			# Case-folded, because the database compares IDs without regard to case: PB-1 and
			# pb-1 are one provider there, and storing both would hit the unique constraint.
			if ref.casefold() in seen:
				warnings.append(_("{0} appears twice in the answer; the first one was used.").format(ref))
				continue
			seen.add(ref.casefold())
			area = str(item.get(source.map_area) or "").strip() if source.map_area else ""
			rows.append({"external_ref": ref, "provider_name": name, "area_name": area or None})
		return rows, warnings


ADAPTERS = {"API": ApiAdapter}


def get_adapter(source):
	"""The adapter for a source's type."""
	adapter = ADAPTERS.get(source.source_type)
	if not adapter:
		raise SourceError(_("Source type {0} is not supported.").format(source.source_type))
	return adapter(source)
