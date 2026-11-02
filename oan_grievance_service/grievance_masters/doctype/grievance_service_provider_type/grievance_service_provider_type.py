# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Grievance Service Provider Type: the kinds of provider (Bank, Cooperative, Market, ...).

A master, so the list is data an administrator can extend, not a list typed into the
DocType JSON and the code. The first set is seeded from setup/install.py.

A type is retired with `is_active`, never deleted: providers that already have it keep it.
"""

import frappe
from frappe import _
from frappe.model.document import Document

DOCTYPE = "Grievance Service Provider Type"


class GrievanceServiceProviderType(Document):
	def validate(self):
		self.type_name = (self.type_name or "").strip()


def ensure_usable(type_name: str) -> None:
	"""Refuse a retired type for a new or changed provider. Existence is Frappe's Link check."""
	if frappe.db.get_value(DOCTYPE, type_name, "is_active") == 0:
		frappe.throw(
			_("Provider type {0} is not active.").format(frappe.bold(type_name)), frappe.ValidationError
		)
