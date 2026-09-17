# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from oan_grievance_service.services import ticket_number

MIN_DESCRIPTION_LENGTH = 20


ALLOWED_FILING_LEVELS = frozenset(
	{
		"Woreda",
		"Kebele",
		"Village",
		"Ward",
		"Taluka",
		"Sub-County",
		"County",
		"District",
	}
)


class Grievance(Document):
	def autoname(self):
		"""Nine-character ticket number: region, category, sequence, year.

		Named at insert so the sequence is allocated in the same transaction as
		the row it belongs to. See `services.ticket_number` for the encoding;
		the number is mirrored onto its own field because the FSD treats it as
		an attribute of the grievance, and reports and notifications read it by
		name.
		"""
		self.name = ticket_number.generate(self.administrative_area, self.service_category)
		self.ticket_number = self.name

	def validate(self):
		self.validate_description_length()
		self.validate_grievance_type_category()
		self.set_administrative_area_metadata()

	def set_administrative_area_metadata(self):
		"""Denormalise area_lft and capture immutable area_path_code snapshot."""
		if not self.administrative_area:
			return

		area = frappe.get_doc("Grievance Administrative Area", self.administrative_area)

		# Only operational administrative levels (Woreda, Kebele, Village, etc.) can have
		# grievances attached. Macro containers (Country, Region, Zone) and root nodes are rejected.
		if area.level_name not in ALLOWED_FILING_LEVELS or (
			not area.parent_administrative_area and area.is_group
		):
			frappe.throw(
				_(
					"Grievances cannot be attached to administrative level '{0}'. "
					"Please select an operational area such as a Woreda or Kebele."
				).format(area.level_name or _("Unknown")),
				title=_("Invalid Administrative Area"),
			)

		if area.valid_to and str(area.valid_to) <= frappe.utils.today():
			frappe.throw(
				_("The selected Administrative Area '{0}' has been dissolved or reorganized.").format(
					self.administrative_area
				),
				title=_("Dissolved Administrative Area"),
			)

		self.area_lft = area.lft
		if not self.area_path_code:
			self.area_path_code = area.path_code or area.name

	def validate_description_length(self):
		"""FSD 3.2.2: the description is free text with a minimum of 20 characters."""
		description = (self.description or "").strip()
		if len(description) < MIN_DESCRIPTION_LENGTH:
			frappe.throw(
				_("Description must be at least {0} characters.").format(MIN_DESCRIPTION_LENGTH),
				title=_("Description Too Short"),
			)

	def validate_grievance_type_category(self):
		"""FSD 3.2.2: grievance type is loaded per category, so it must belong to one."""
		if not (self.grievance_type and self.service_category):
			return
		parent = frappe.db.get_value("Grievance Type", self.grievance_type, "service_category")
		if parent != self.service_category:
			frappe.throw(
				_("Grievance type {0} belongs to category {1}, not {2}.").format(
					frappe.bold(self.grievance_type),
					frappe.bold(parent),
					frappe.bold(self.service_category),
				),
				title=_("Type Does Not Match Category"),
			)


def on_doctype_update():
	frappe.db.add_index("Grievance", ["area_lft"])
	# The escalation batch selects on this alone, so it is the whole schedule.
	frappe.db.add_index("Grievance", ["next_escalation_at"])
