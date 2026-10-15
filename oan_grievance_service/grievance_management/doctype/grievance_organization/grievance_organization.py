# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Grievance Organization: an NGO, FPO or cooperative and its verification status.

The status is the Organization Workflow's state. The workflow, and the link to the
representative's submitter profile, are in services/organization.py.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from oan_grievance_service.services import constants as C
from oan_grievance_service.services import identity
from oan_grievance_service.services import organization as service


class GrievanceOrganization(Document):
	def validate(self):
		self.registration_number = (self.registration_number or "").upper()
		self.contact_phone = identity.validate_mobile(self.contact_phone, "contact_phone")
		self.validate_representative()

	def on_update(self):
		service.sync_profile(self)

	def validate_representative(self):
		"""The authorized representative is an enabled login that holds the submitter role."""
		if not self.is_new() and not self.has_value_changed("representative_user"):
			return
		if not frappe.db.get_value("User", self.representative_user, "enabled"):
			frappe.throw(
				_("The authorized representative must be an enabled user account."),
				title=_("Invalid Representative"),
			)
		if C.ROLE_SUBMITTER not in frappe.get_roles(self.representative_user):
			frappe.throw(
				_("The authorized representative must hold the Grievance Submitter role."),
				title=_("Invalid Representative"),
			)
