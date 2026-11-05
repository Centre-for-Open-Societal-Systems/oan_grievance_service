# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Installation-wide grievance settings, as a Single."""

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class GrievanceSettings(Document):
	def validate(self):
		from oan_grievance_service.services import duplicates

		duplicates.validate_tuning(
			flt(self.duplicate_weight_category),
			flt(self.duplicate_weight_type),
			flt(self.duplicate_threshold),
			flt(self.duplicate_high_threshold),
		)


def auto_routing_enabled() -> bool:
	"""Whether cases go to the matching desk's officer; off sends every case to Unrouted."""
	return bool(frappe.get_cached_doc("Grievance Settings").auto_routing_enabled)
