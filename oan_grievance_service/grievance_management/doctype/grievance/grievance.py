# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from pydantic import ValidationError as PydanticValidationError

from oan_grievance_service.services import identity, ticket_number

# Re-export for callers; single source of truth lives on identity.
ALLOWED_FILING_LEVELS = identity.ALLOWED_FILING_LEVELS
MIN_DESCRIPTION_LENGTH = identity.MIN_DESCRIPTION_LENGTH


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
		# Frappe already enforces reqd / Link / Select. Domain-only rules below.
		try:
			identity.validate_submission_payload(
				{
					"contact_mobile": self.contact_mobile,
					"administrative_area": self.administrative_area,
					"service_category": self.service_category,
					"grievance_type": self.grievance_type,
					"description": self.description,
				}
			)
		except PydanticValidationError as e:
			# Desk / DocType path expects frappe.ValidationError; API gets pydantic details.
			parts = []
			for err in e.errors():
				loc = ".".join(str(item) for item in err["loc"])
				parts.append(f"{loc}: {err['msg']}" if loc else err["msg"])
			frappe.throw("; ".join(parts), title=_("Incomplete Submission"))
		self.set_administrative_area_metadata()

	def set_administrative_area_metadata(self):
		"""Denormalise area_lft and capture immutable area_path_code snapshot."""
		if not self.administrative_area:
			return

		area = identity.validate_filing_area(self.administrative_area)
		self.area_lft = area.lft
		if not self.area_path_code:
			self.area_path_code = area.path_code or area.name


def on_doctype_update():
	frappe.db.add_index("Grievance", ["area_lft"])
	# The escalation batch selects on this alone, so it is the whole schedule.
	frappe.db.add_index("Grievance", ["next_escalation_at"])
