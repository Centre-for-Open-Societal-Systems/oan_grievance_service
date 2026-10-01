# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

OFFICER_ROLES = {"Grievance Officer", "Grievance Admin"}
# Scope fields that make a desk more specific than a category-only desk.
NARROWING_SCOPES = ("administrative_area_scope", "grievance_type_scope", "service_provider_scope")


class GrievanceRBACAssignment(Document):
	def validate(self):
		# Area-aware desks predate these rules and are left alone.
		if self.is_category_only():
			self.validate_no_repeated_officer()
			self.reject_duplicate_desk()
			if self.active and self.links_changed():
				self.validate_links()

	def is_category_only(self) -> bool:
		return bool(self.category_scope) and not any(self.get(field) for field in NARROWING_SCOPES)

	def validate_no_repeated_officer(self):
		seen = set()
		for row in self.officers:
			if row.user in seen:
				frappe.throw(_("Officer '{0}' is listed more than once.").format(row.user))
			seen.add(row.user)

	def reject_duplicate_desk(self):
		"""One category-only desk per (category, department), inactive desks included.

		A check-then-insert race would let two concurrent saves both pass, and no unique
		index can cover this partial scope. So the category row is locked first: the second
		save waits for the first to commit, then its locking read sees the new desk.
		"""
		frappe.db.get_value("Grievance Service Category", self.category_scope, "name", for_update=True)
		filters = {
			"category_scope": self.category_scope,
			"department_scope": self.department_scope,
			"administrative_area_scope": ["is", "not set"],
			"grievance_type_scope": ["is", "not set"],
			"service_provider_scope": ["is", "not set"],
		}
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		existing = frappe.db.get_value("Grievance RBAC Assignment", filters, "name", for_update=True)
		if existing:
			frappe.throw(
				_("A category assignment already exists for {0} in {1} ({2}).").format(
					self.category_scope, self.department_scope, existing
				),
				frappe.DuplicateEntryError,
			)

	def links_changed(self) -> bool:
		"""Whether a save touches what the link checks cover, so a routine save is never blocked by drift."""
		before = self.get_doc_before_save()
		if self.is_new() or not before:
			return True
		if any(
			before.get(field) != self.get(field) for field in ("category_scope", "department_scope", "active")
		):
			return True
		return _active_users(before.officers) != _active_users(self.officers)

	def validate_links(self):
		for row in self.officers:
			if row.active:
				_assert_officer(row.user)
		_assert_active("Grievance Service Category", self.category_scope, "is_active", _("Service category"))
		_assert_active("Grievance Department", self.department_scope, "active", _("Department"))


def _active_users(rows) -> set[str]:
	return {row.user for row in rows if row.active}


def _assert_officer(user: str):
	label = _("Officer")
	if not frappe.db.exists("User", user):
		frappe.throw(_("{0} '{1}' does not exist.").format(label, user), frappe.ValidationError)
	if not frappe.db.get_value("User", user, "enabled"):
		frappe.throw(_("{0} '{1}' is disabled.").format(label, user), frappe.ValidationError)
	if not OFFICER_ROLES.intersection(frappe.get_roles(user)):
		frappe.throw(
			_("{0} '{1}' must be a Grievance Officer or Grievance Admin.").format(label, user),
			frappe.ValidationError,
		)


def _assert_active(doctype: str, name: str, flag_field: str, label: str):
	if not frappe.db.get_value(doctype, name, flag_field):
		frappe.throw(_("{0} '{1}' is inactive.").format(label, name), frappe.ValidationError)
