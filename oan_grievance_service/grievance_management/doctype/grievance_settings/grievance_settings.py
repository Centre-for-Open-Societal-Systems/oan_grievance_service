# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Installation-wide grievance settings, as a Single."""

import frappe
from frappe.model.document import Document


class GrievanceSettings(Document):
	pass


def auto_routing_enabled() -> bool:
	"""Whether cases go to the matching desk's officer; off sends every case to Unrouted."""
	return bool(frappe.get_cached_doc("Grievance Settings").auto_routing_enabled)
