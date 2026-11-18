# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from oan_grievance_service.services import constants as C

# What makes a type sensitive or urgent: an administrator's to set (STG-514).
HANDLING_FIELDS = ("is_sensitive", "is_urgent", "urgency_season")


class GrievanceType(Document):
	def validate(self):
		self.type_name = (self.type_name or "").strip()
		self.type_code = (self.type_code or "").strip().upper() or None
		self.external_referral = (self.external_referral or "").strip() or None
		self.validate_unique_in_category()
		self.validate_routing_defaults()
		self.validate_category()
		self.validate_fallback()
		self.validate_handling_set_by_an_administrator()
		self.validate_urgency()

	def validate_handling_set_by_an_administrator(self):
		"""Only an administrator decides whether a type is Sensitive or Urgent, on every write path.

		A Desk Head may write the types of the categories they head (Desk, /api/resource, the
		taxonomy API), so the rule lives here and not only in the endpoint: unflagging a sensitive
		type would send its next cases to Kebele officers.
		"""
		before = None if self.is_new() else self.get_doc_before_save()
		changed = [
			field
			for field in HANDLING_FIELDS
			if (self.get(field) or None) != ((before.get(field) if before else None) or None)
		]
		if not changed:
			return
		from oan_grievance_service.permissions import is_unrestricted

		if not is_unrestricted():
			frappe.throw(
				_("Only an administrator can change whether a grievance type is sensitive or urgent."),
				frappe.PermissionError,
			)

	def validate_urgency(self):
		"""An Urgent type has a live season that makes its cases Urgent inside the window.

		A type that is not Urgent may still follow a season, which then only raises priority
		(harvest marketing), so the season is optional there.
		"""
		if not self.is_urgent:
			return
		if not self.urgency_season:
			frappe.throw(
				_("Grievance type '{0}' is Urgent, so it needs an urgency season.").format(self.type_name),
				frappe.ValidationError,
			)
		active, handling = frappe.db.get_value(
			"Grievance Urgency Season", self.urgency_season, ["is_active", "handling_class"]
		)
		if not active:
			frappe.throw(
				_("Urgency season '{0}' is inactive.").format(self.urgency_season), frappe.ValidationError
			)
		if handling != C.HANDLING_URGENT:
			frappe.throw(
				_("Urgency season '{0}' only raises priority, so it cannot make a type Urgent.").format(
					self.urgency_season
				),
				frappe.ValidationError,
			)

	def validate_routing_defaults(self):
		"""A type's entry level is an active role level, and a type code is used once."""
		if (
			self.entry_role_level
			and (self.is_new() or self.has_value_changed("entry_role_level"))
			and not frappe.db.get_value("Grievance Role Level", self.entry_role_level, "is_active")
		):
			frappe.throw(
				_("Role level '{0}' is not active.").format(self.entry_role_level),
				frappe.ValidationError,
			)
		if self.type_code and (self.is_new() or self.has_value_changed("type_code")):
			# Seeding keys on the code: a second type with it would be the same taxonomy entry twice.
			existing = frappe.db.get_value(
				"Grievance Type", {"type_code": self.type_code, "name": ["!=", self.name or ""]}, "name"
			)
			if existing:
				frappe.throw(
					_("Type code '{0}' is already used by grievance type {1}.").format(
						self.type_code, existing
					),
					frappe.DuplicateEntryError,
				)

	def validate_unique_in_category(self):
		"""A category never holds the same type twice. The same name under another category is fine."""
		existing = frappe.db.get_value(
			"Grievance Type",
			{
				"service_category": self.service_category,
				"type_name": self.type_name,
				"name": ["!=", self.name or ""],
			},
			"name",
		)
		if existing:
			frappe.throw(
				_("Grievance type '{0}' already exists in {1} ({2}).").format(
					self.type_name, self.service_category, existing
				),
				frappe.DuplicateEntryError,
			)

	def validate_category(self):
		"""A type is only offered under an active category, and stays under the one it was made in.

		A routine save of a type whose category was retired later is not blocked: only
		creating or reactivating one is, so a retired category never regains a live type.
		"""
		if not self.is_new() and self.has_value_changed("service_category") and self.in_use():
			frappe.throw(
				_("Grievance type '{0}' is in use, so it cannot be moved to another category.").format(
					self.type_name
				),
				frappe.ValidationError,
			)
		if not self.is_active:
			return
		if (
			self.is_new() or self.has_value_changed("service_category") or self.has_value_changed("is_active")
		) and not frappe.db.get_value("Grievance Service Category", self.service_category, "is_active"):
			frappe.throw(
				_(
					"Service category '{0}' is inactive. Reactivate it before adding or enabling its types."
				).format(self.service_category),
				frappe.ValidationError,
			)

	def validate_fallback(self):
		"""Unclassified cases fall to the default category's catch-all type, so it must stay available."""
		if (
			not self.is_active
			and self.type_name == C.FALLBACK_GRIEVANCE_TYPE
			and frappe.db.get_value("Grievance Service Category", self.service_category, "is_default")
		):
			frappe.throw(
				_("The catch-all grievance type '{0}' cannot be deactivated.").format(self.type_name),
				frappe.ValidationError,
			)

	def in_use(self) -> bool:
		"""Whether any grievance or routing desk refers to this type."""
		return bool(
			frappe.db.exists("Grievance", {"grievance_type": self.name})
			or frappe.db.exists("Grievance RBAC Assignment", {"grievance_type_scope": self.name})
		)


def get_fallback_type(category: str | None) -> str | None:
	"""The type an unclassified case gets inside `category`: its catch-all, else its first active type."""
	if not category:
		return None
	return frappe.db.get_value(
		"Grievance Type",
		{"service_category": category, "type_name": C.FALLBACK_GRIEVANCE_TYPE, "is_active": 1},
		"name",
	) or frappe.db.get_value(
		"Grievance Type", {"service_category": category, "is_active": 1}, "name", order_by="type_name asc"
	)


def resolve_grievance_type(type_identifier: str | None, category: str | None = None) -> str | None:
	"""Resolve a grievance type identifier (DocType name or display type_name) to canonical doc name.

	None when nothing matches. The caller decides whether that is an error; handing
	back the raw input instead would let an unknown string reach a Link field.
	"""
	if not type_identifier:
		return None
	type_identifier = str(type_identifier).strip()
	if frappe.db.exists("Grievance Type", type_identifier):
		return type_identifier
	filters = {"type_name": type_identifier}
	if category:
		filters["service_category"] = category
	resolved = frappe.db.get_value("Grievance Type", filters, "name")
	if resolved:
		return resolved
	return frappe.db.get_value("Grievance Type", {"type_name": type_identifier}, "name")
