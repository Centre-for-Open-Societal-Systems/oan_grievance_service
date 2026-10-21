# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Grievance Service Provider Source: where a directory sync reads providers from.

What belongs here: the connection record's own rules (https only, no half-filled
credentials on an enabled source) and the admin listing of sources.

What does not belong here: fetching and storing providers. That is
services/service_provider_sync.py.

Credentials live in encrypted Password fields and are never returned by the API.
"""

from urllib.parse import urlsplit

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder.functions import Count

DOCTYPE = "Grievance Service Provider Source"
PROVIDER_DOCTYPE = "Grievance Service Provider"

AUTH_LOGIN = "Login"


class GrievanceServiceProviderSource(Document):
	def validate(self):
		self.source_name = (self.source_name or "").strip()
		self.validate_connection()
		if self.is_enabled:
			self.validate_ready_to_enable()

	def validate_connection(self):
		if self.api_base_url:
			self.api_base_url = self.api_base_url.strip().rstrip("/")
			parts = urlsplit(self.api_base_url)
			if parts.scheme != "https" or not parts.hostname:
				frappe.throw(_("API Base URL must be an https address."), frappe.ValidationError)
			if parts.username or parts.password or parts.query or parts.fragment:
				frappe.throw(
					_("API Base URL must not carry credentials, a query string or a fragment."),
					frappe.ValidationError,
				)
		for fieldname, label in (("login_path", _("Login Path")), ("list_path", _("List Path"))):
			value = (self.get(fieldname) or "").strip()
			if value and not value.startswith("/"):
				frappe.throw(_("{0} must start with a slash.").format(label), frappe.ValidationError)
			self.set(fieldname, value)

	def validate_ready_to_enable(self):
		"""An enabled source must be able to run, so a sync never fails on missing setup."""
		missing = [
			label
			for label, value in (
				(_("API Base URL"), self.api_base_url),
				(_("List Path"), self.list_path),
				(_("List Key"), self.list_key),
			)
			if not value
		]
		if self.auth_mode == AUTH_LOGIN:
			if not self.login_path:
				missing.append(_("Login Path"))
			if not self.api_username:
				missing.append(_("Username"))
		if missing:
			frappe.throw(
				_("Fill in {0} before enabling this source.").format(", ".join(missing)),
				frappe.ValidationError,
			)
		if not self.has_credentials():
			frappe.throw(_("Set the credentials before enabling this source."), frappe.ValidationError)

	def has_credentials(self) -> bool:
		"""Whether the secret this auth mode needs has been stored."""
		field = "api_password" if self.auth_mode == AUTH_LOGIN else "api_token"
		return bool(self.get_password(field, raise_exception=False))


def list_sources() -> list[dict]:
	"""Every source with its last sync result and how many providers it owns."""
	Provider = frappe.qb.DocType(PROVIDER_DOCTYPE)

	def counts(active_only: bool) -> dict:
		query = (
			frappe.qb.from_(Provider)
			.select(Provider.source, Count("*"))
			.where(Provider.source.isnotnull())
			.groupby(Provider.source)
		)
		if active_only:
			query = query.where(Provider.is_active == 1)
		return dict(query.run())

	total, active = counts(False), counts(True)
	items = []
	for name in frappe.get_all(DOCTYPE, order_by="source_name asc", pluck="name"):
		source = frappe.get_doc(DOCTYPE, name)
		items.append(
			{
				"name": source.name,
				"source_type": source.source_type,
				"is_enabled": bool(source.is_enabled),
				"api_base_url": source.api_base_url or None,
				"credentials_configured": source.has_credentials(),
				"last_synced_at": source.last_synced_at,
				"last_sync_status": source.last_sync_status or None,
				"last_sync_message": source.last_sync_message or None,
				"provider_count": total.get(name, 0),
				"active_provider_count": active.get(name, 0),
			}
		)
	return items
