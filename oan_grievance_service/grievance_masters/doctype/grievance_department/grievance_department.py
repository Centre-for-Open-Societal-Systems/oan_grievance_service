# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import validate_email_address

from oan_grievance_service.grievance_access_control.doctype.grievance_rbac_assignment.grievance_rbac_assignment import (
	OFFICER_ROLES,
)


class GrievanceDepartment(Document):
	def validate(self):
		self.validate_email_account()
		self.validate_head_of_dept()

	def validate_email_account(self):
		"""The department is notified at this address, so it must be a valid email address."""
		self.email_account = (self.email_account or "").strip()
		if not validate_email_address(self.email_account):
			frappe.throw(
				_("'{0}' is not a valid department email address.").format(self.email_account),
				frappe.ValidationError,
			)

	def validate_head_of_dept(self):
		"""The head must be an enabled officer or admin.

		Checked only when the head changes, so a head who is disabled later never blocks
		an unrelated edit of the department.
		"""
		if not self.head_of_dept or not self.has_value_changed("head_of_dept"):
			return
		label = _("Head of department")
		if not frappe.db.get_value("User", self.head_of_dept, "enabled"):
			frappe.throw(
				_("{0} '{1}' does not exist or is disabled.").format(label, self.head_of_dept),
				frappe.ValidationError,
			)
		if not OFFICER_ROLES.intersection(frappe.get_roles(self.head_of_dept)):
			frappe.throw(
				_("{0} '{1}' must be a Grievance Officer or Grievance Admin.").format(
					label, self.head_of_dept
				),
				frappe.ValidationError,
			)
