# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from oan_grievance_service.services import constants as C
from oan_grievance_service.services import ticket_number


class GrievanceServiceCategory(Document):
	def validate(self):
		self.category_name = (self.category_name or "").strip()
		self.code = (self.code or "").strip().upper()
		ticket_number.validate_category_code(self.code, self.category_name)
		self.validate_unique_code()
		self.validate_code_is_unissued()
		self.validate_default()

	def on_update(self):
		self.retire_types()
		self.take_over_as_default()

	def retire_types(self):
		"""Retiring a category retires its types: none may be offered under an inactive parent."""
		if self.is_active or self.is_new() or not self.has_value_changed("is_active"):
			return
		for name in frappe.get_all(
			"Grievance Type", filters={"service_category": self.name, "is_active": 1}, pluck="name"
		):
			child = frappe.get_doc("Grievance Type", name)
			child.is_active = 0
			# A consequence of the category's own write, which was already permission-checked.
			child.save(ignore_permissions=True)

	def take_over_as_default(self):
		"""Promoting a category clears the flag on the previous default and gives it a catch-all type.

		Unclassified cases fall to the default category and, inside it, to the catch-all type, so
		the new default must have one.
		"""
		if not self.is_default or not self.has_value_changed("is_default"):
			return
		for name in frappe.get_all(
			"Grievance Service Category",
			filters={"is_default": 1, "name": ["!=", self.name]},
			pluck="name",
		):
			previous = frappe.get_doc("Grievance Service Category", name)
			previous.flags.demoting = True
			previous.is_default = 0
			previous.save(ignore_permissions=True)
		self.ensure_catch_all_type()

	def ensure_catch_all_type(self):
		existing = frappe.db.get_value(
			"Grievance Type", {"service_category": self.name, "type_name": C.FALLBACK_GRIEVANCE_TYPE}
		)
		if existing:
			kind = frappe.get_doc("Grievance Type", existing)
			if kind.is_active:
				return
			kind.is_active = 1
		else:
			kind = frappe.get_doc(
				{
					"doctype": "Grievance Type",
					"service_category": self.name,
					"type_name": C.FALLBACK_GRIEVANCE_TYPE,
					"is_active": 1,
				}
			)
		kind.save(ignore_permissions=True)

	def before_rename(self, old, new, merge=False):
		"""Links follow a rename, but a merge would fold two ticket codes into one."""
		if merge:
			frappe.throw(_("Service categories cannot be merged."), frappe.ValidationError)

	def validate_unique_code(self):
		existing = frappe.db.get_value(
			"Grievance Service Category", {"code": self.code, "name": ["!=", self.name or ""]}, "name"
		)
		if existing:
			frappe.throw(
				_("Ticket code '{0}' is already used by service category '{1}'.").format(self.code, existing),
				frappe.DuplicateEntryError,
			)

	def validate_code_is_unissued(self):
		"""A code that has appeared in a ticket number is frozen: that ticket must keep its meaning."""
		if self.is_new() or not self.has_value_changed("code"):
			return
		if frappe.db.exists("Grievance", {"service_category": self.name, "ticket_number": ["is", "set"]}):
			frappe.throw(
				_("Ticket code of '{0}' cannot change: tickets have already been issued under it.").format(
					self.name
				),
				frappe.ValidationError,
			)

	def validate_default(self):
		"""Exactly one category is the default: it is active, and it only stops being one when another takes over."""
		if self.is_default and not self.is_active:
			frappe.throw(
				_(
					"'{0}' is the default category, so it cannot be inactive. Make another category the default first."
				).format(self.name or self.category_name),
				frappe.ValidationError,
			)
		if (
			not self.is_new()
			and not self.is_default
			and self.has_value_changed("is_default")
			and not getattr(self.flags, "demoting", False)
		):
			frappe.throw(
				_("Exactly one category must be the default. Make another category the default instead."),
				frappe.ValidationError,
			)
