# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class GrievanceDuplicate(Document):
	def validate(self):
		if self.grievance == self.duplicate_of:
			frappe.throw(_("A grievance cannot be a duplicate of itself."), title=_("Invalid Duplicate"))
		if self.is_confirmed and self.is_dismissed:
			frappe.throw(
				_("A duplicate cannot be both confirmed and dismissed."), title=_("Conflicting Decision")
			)
