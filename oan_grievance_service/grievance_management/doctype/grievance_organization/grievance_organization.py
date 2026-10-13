# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Grievance Organization: an NGO, FPO or cooperative and its verification status.

What belongs here: field rules and the guard on status changes, so the desk, the API
and any script are held to the same ones.

What does not belong here: the register / edit / decide workflow and the link to the
representative's submitter profile (services/organization.py), and request shapes
(api/v1/organization.py).
"""

import frappe
from frappe import _
from frappe.model.document import Document

from oan_grievance_service.services import constants as C
from oan_grievance_service.services import identity
from oan_grievance_service.services import organization as service


class GrievanceOrganization(Document):
	def validate(self):
		self.normalise()
		self.validate_types()
		self.validate_registration_number()
		self.contact_phone = identity.validate_mobile(self.contact_phone, "contact_phone")
		self.validate_representative()
		self.validate_status()

	def on_update(self):
		service.sync_profile(self)

	def normalise(self):
		for field in service.PROFILE_FIELDS:
			value = self.get(field)
			if isinstance(value, str):
				self.set(field, value.strip())
		self.registration_number = service.normalise_registration_number(self.registration_number)

	def validate_types(self):
		allowed = service.ENTITY_TYPES.get(self.org_type)
		if not allowed:
			frappe.throw(
				_("Organization type must be one of: {0}.").format(", ".join(C.ORG_TYPES)),
				title=_("Invalid Organization Type"),
			)
		if self.entity_type not in allowed:
			frappe.throw(
				_("Entity type '{0}' is not valid for a {1}. Choose one of: {2}.").format(
					self.entity_type, self.org_type, ", ".join(allowed)
				),
				title=_("Invalid Entity Type"),
			)

	def validate_registration_number(self):
		if not identity.REGISTRATION_PATTERN.match(self.registration_number or ""):
			frappe.throw(
				_("Registration number '{0}' is not in a valid format.").format(self.registration_number),
				title=_("Invalid Registration Number"),
			)

	def validate_representative(self):
		"""The authorized representative is an enabled login that holds the submitter role."""
		if not self.is_new() and not self.has_value_changed("representative_user"):
			return
		enabled = frappe.db.get_value("User", self.representative_user, "enabled")
		if not enabled:
			frappe.throw(
				_("The authorized representative must be an enabled user account."),
				title=_("Invalid Representative"),
			)
		if "Grievance Submitter" not in frappe.get_roles(self.representative_user):
			frappe.throw(
				_("The authorized representative must hold the Grievance Submitter role."),
				title=_("Invalid Representative"),
			)

	def validate_status(self):
		before = self.get_doc_before_save()
		previous = before.verification_status if before else None
		service.validate_transition(
			previous if not self.is_new() else None, self.verification_status, self.status_reason
		)
		if not self.is_new() and previous != self.verification_status:
			# Active to In Review is the system voiding a verification, not a decision.
			if self.verification_status == C.ORG_STATUS_IN_REVIEW and previous == C.ORG_STATUS_ACTIVE:
				return
			service.stamp_decision(self)
