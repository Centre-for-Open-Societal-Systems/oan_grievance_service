# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from oan_grievance_service.grievance_management.doctype.grievance_timeline.grievance_timeline import (
	GrievanceTimeline,
)
from oan_grievance_service.services import constants as C

# Channels where the citizen gives a rating to a person, who keys it in for them.
ASSISTED_CHANNELS = ("Call Center", "Walk-in")


class GrievanceFeedback(Document):
	def validate(self):
		if not self.rating or self.rating < 1 or self.rating > 5:
			frappe.throw(_("Rating must be an integer between 1 and 5."), frappe.ValidationError)

		if not self.submitted_at:
			self.submitted_at = now_datetime()

		if not self.grievance:
			frappe.throw(_("Grievance is required."), frappe.ValidationError)

		grievance = frappe.get_doc("Grievance", self.grievance)
		allowed_states = (C.STATE_RESOLVED, C.STATE_CLOSED)
		if grievance.workflow_state not in allowed_states and grievance.status not in allowed_states:
			frappe.throw(
				_("Feedback can only be submitted for grievances in Resolved or Closed status."),
				frappe.ValidationError,
			)

		user = frappe.session.user
		from oan_grievance_service import permissions
		from oan_grievance_service.grievance_management.doctype.grievance_submitter_profile.grievance_submitter_profile import (
			profiles_of,
		)

		if permissions.is_unrestricted(user):
			return

		if not permissions.is_staff(user):
			user_profiles = profiles_of(user)
			if grievance.submitter not in user_profiles and grievance.owner != user:
				frappe.throw(
					_("You can only submit feedback for your own grievances."),
					frappe.PermissionError,
				)
			return

		# An officer may only key in a rating the citizen gave by phone or in person,
		# and never on a case they are handling: the rating is about their own work.
		if self.feedback_channel not in ASSISTED_CHANNELS:
			frappe.throw(
				_("Officers can only record feedback given through Call Center or Walk-in."),
				frappe.PermissionError,
			)
		if grievance.assigned_to == user:
			frappe.throw(
				_("The officer handling a grievance cannot record feedback on it."),
				frappe.PermissionError,
			)

	def after_insert(self):
		# Sync latest rating to grievance for fast reporting and list view display
		frappe.db.set_value(
			"Grievance",
			self.grievance,
			{
				"satisfaction_rating": self.rating,
				"satisfaction_comments": self.comments,
			},
			update_modified=False,
		)

		# Add entry to unified timeline
		body_text = f"Citizen Feedback ({self.feedback_type}): Rating {self.rating}/5."
		if self.comments:
			body_text += f" {self.comments}"
		GrievanceTimeline.record(
			grievance=self.grievance,
			entry_type="feedback",
			is_internal=0,
			body=body_text.strip(),
			author_user=self.submitted_by if not self.author_submitter else None,
			author_submitter=self.author_submitter,
			ref_doctype="Grievance Feedback",
			ref_docname=self.name,
		)
