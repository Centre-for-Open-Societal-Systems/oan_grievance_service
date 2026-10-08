# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document

from oan_grievance_service.services import constants as C

CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]*$")
CODE_LENGTH = 30


class GrievanceType(Document):
	def validate(self):
		self.type_name = (self.type_name or "").strip()
		self.code = (self.code or "").strip().upper() or generate_code(
			self.type_name, self.service_category, exclude=self.name
		)
		self.validate_code_format()
		self.validate_unique_in_category()
		self.validate_category()
		self.validate_fallback()

	def validate_code_format(self):
		if len(self.code) > CODE_LENGTH or not CODE_PATTERN.match(self.code):
			frappe.throw(
				_(
					"Code '{0}' is not valid. Use up to {1} letters, digits, hyphens or underscores, "
					"starting with a letter or digit."
				).format(self.code, CODE_LENGTH),
				frappe.ValidationError,
			)

	def validate_unique_in_category(self):
		"""Name and code each identify one type within its category, never across categories."""
		for fieldname, label in (("type_name", _("name")), ("code", _("code"))):
			existing = frappe.db.get_value(
				"Grievance Type",
				{
					"service_category": self.service_category,
					fieldname: self.get(fieldname),
					"name": ["!=", self.name or ""],
				},
				"name",
			)
			if existing:
				frappe.throw(
					_("A grievance type with {0} '{1}' already exists in {2} ({3}).").format(
						label, self.get(fieldname), self.service_category, existing
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


def generate_code(type_name: str, service_category: str | None, exclude: str | None = None) -> str:
	"""A code for a type that was given none: its name in capitals, made unique in its category."""
	base = re.sub(r"[^A-Z0-9]+", "_", (type_name or "").upper()).strip("_")[:CODE_LENGTH] or "TYPE"
	taken = set(
		frappe.get_all(
			"Grievance Type",
			filters={"service_category": service_category, "name": ["!=", exclude or ""]},
			pluck="code",
		)
	)
	code, suffix = base, 1
	while code in taken:
		suffix += 1
		tail = f"_{suffix}"
		code = f"{base[: CODE_LENGTH - len(tail)]}{tail}"
	return code


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
