# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from oan_grievance_service.services import constants as C


class GrievanceStatus(Document):
	def validate(self):
		self.refuse_unknown_status()

	def refuse_unknown_status(self):
		"""The list is data; the vocabulary is not.

		These rows exist so `Grievance.status` can be a Link rather than a Select
		carrying a second copy of the same eight names. What they are not is an
		invitation to invent a ninth: `ALLOWED_TRANSITIONS` is keyed on these
		names, and a state absent from it is one a grievance can enter and never
		legally leave.
		"""
		if self.status_name in C.ALLOWED_TRANSITIONS:
			return

		frappe.throw(
			_(
				"{0} is not one of the lifecycle states. The states are defined in "
				"services/constants.py and adding one needs a transition rule, not just a row."
			).format(frappe.bold(self.status_name)),
			title=_("Unknown Lifecycle State"),
		)
