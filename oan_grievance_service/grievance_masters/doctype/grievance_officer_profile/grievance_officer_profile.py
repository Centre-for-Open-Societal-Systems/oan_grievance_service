# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""One profile for every Nodal (L1) and Senior Nodal (L2) officer.

The two tiers share a shape and differ only in `level`, so they are one entity rather than
two. The checks live here, not in the API, so they hold for the desk and for any other writer.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import validate_email_address, validate_phone_number

LEVELS = ("L1", "L2")
STATUSES = ("Active", "On Leave", "Inactive")


class GrievanceOfficerProfile(Document):
	def validate(self):
		self.normalize_contact()
		self.validate_department()
		self.validate_region()
		self.validate_reports_to()
		self.validate_service_categories()

	def normalize_contact(self):
		self.full_name = (self.full_name or "").strip()
		self.email = (self.email or "").strip().lower()
		if not validate_email_address(self.email):
			frappe.throw(_("{0} is not a valid email address.").format(self.email), frappe.ValidationError)
		self.phone = (self.phone or "").strip() or None
		if self.phone and not validate_phone_number(self.phone):
			frappe.throw(_("{0} is not a valid phone number.").format(self.phone), frappe.ValidationError)

	def validate_department(self):
		"""A new or moved profile needs an active department. An existing one can be retired in place."""
		before = self.get_doc_before_save()
		if before and before.department == self.department:
			return
		if not frappe.db.get_value("Grievance Department", self.department, "active"):
			frappe.throw(_("Department {0} is not active.").format(self.department), frappe.ValidationError)

	def validate_region(self):
		if not self.region:
			return
		if frappe.db.get_value("Grievance Administrative Area", self.region, "level_name") != "Region":
			frappe.throw(_("{0} is not a region.").format(self.region), frappe.ValidationError)

	def validate_reports_to(self):
		"""Only an L2 reports to someone, and that someone is an L1."""
		if not self.reports_to:
			return
		if self.level != "L2":
			frappe.throw(_("Only an L2 officer can report to an L1 officer."), frappe.ValidationError)
		if self.reports_to == self.name:
			frappe.throw(_("An officer cannot report to themselves."), frappe.ValidationError)
		if frappe.db.get_value("Grievance Officer Profile", self.reports_to, "level") != "L1":
			frappe.throw(_("{0} is not an L1 officer.").format(self.reports_to), frappe.ValidationError)

	def validate_service_categories(self):
		seen = set()
		for row in list(self.service_categories):
			if row.service_category in seen:
				self.remove(row)
			seen.add(row.service_category)
		inactive = [
			category
			for category in seen
			if not frappe.db.get_value("Grievance Service Category", category, "is_active")
		]
		before = self.get_doc_before_save()
		already = {row.service_category for row in before.service_categories} if before else set()
		if set(inactive) - already:
			frappe.throw(
				_("Service category {0} is not active.").format(", ".join(sorted(set(inactive) - already))),
				frappe.ValidationError,
			)


def on_doctype_update():
	frappe.db.add_index("Grievance Officer Profile", ["level", "status"])
	frappe.db.add_index("Grievance Officer Profile", ["department"])
