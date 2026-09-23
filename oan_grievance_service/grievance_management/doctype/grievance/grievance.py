# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from pydantic import ValidationError as PydanticValidationError

from oan_grievance_service.services import hooks_handlers, identity, ticket_number

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
		if self.name:
			return
		if getattr(self.flags, "is_draft_wizard", False) and not getattr(self.flags, "in_submit", False):
			key = self.client_submission_uuid or frappe.generate_hash(length=12)
			self.name = f"DRAFT-{key}"
			self.ticket_number = None
			return

		self.name = ticket_number.generate(self.administrative_area, self.service_category)
		self.ticket_number = self.name

	def _validate_mandatory(self):
		if getattr(self.flags, "is_draft_wizard", False) and not getattr(self.flags, "in_submit", False):
			return
		super()._validate_mandatory()

	def validate(self):
		self.keep_status_in_step_with_the_workflow()
		if getattr(self.flags, "is_draft_wizard", False) and not getattr(self.flags, "in_submit", False):
			return

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
		self.guard_the_workflow_move()

	# Workflow
	# --------
	# Frappe's engine moves a grievance by setting `workflow_state` and saving,
	# submitting or cancelling it, so the guards run from validate and the record of
	# the move from the post-save methods -- one of which fires per kind of save.

	def keep_status_in_step_with_the_workflow(self):
		"""`workflow_state` is what the engine drives; `status` mirrors it so every
		reader -- the API, the list filters, the reports -- keeps its field."""
		if not self.workflow_state:
			self.workflow_state = self.status or "Draft"
		self.status = self.workflow_state

	def workflow_move_from(self):
		"""The state this save leaves, or None when the save is not a move."""
		if self.is_new():
			return None
		before = self.get_doc_before_save()
		if not before or before.workflow_state == self.workflow_state:
			return None
		return before.workflow_state

	def guard_the_workflow_move(self):
		from_state = self.workflow_move_from()
		if from_state:
			hooks_handlers.before_workflow_action(self, from_state)

	# Frappe runs `validate` for a save and a submit only. A move between two
	# submitted states arrives as update_after_submit, a rejection as cancel, and
	# each has its own before-method; the sync and the guards must run from those
	# too, or a move on either path would leave `status` behind and skip the checks.

	def before_update_after_submit(self):
		self.keep_status_in_step_with_the_workflow()
		self.guard_the_workflow_move()

	def before_cancel(self):
		self.keep_status_in_step_with_the_workflow()
		self.guard_the_workflow_move()

	def record_the_workflow_move(self):
		from_state = self.workflow_move_from()
		if from_state:
			hooks_handlers.after_workflow_action(self, from_state)

	def on_update(self):
		# A submit runs on_update and then on_submit; the move is on_submit's.
		if self._action == "save":
			self.record_the_workflow_move()

	def on_submit(self):
		self.record_the_workflow_move()

	def on_update_after_submit(self):
		self.record_the_workflow_move()

	def on_cancel(self):
		self.record_the_workflow_move()

	def validate_workflow(self):
		"""Frappe insists a new document enters the workflow at its first state.

		Every intake path inserts a Draft, so that holds for live traffic. It is
		relaxed for a new document so history can be loaded at the state it was
		actually in: a migrated case that closed two years ago was never Draft.
		"""
		if self.is_new():
			return
		super().validate_workflow()

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
	frappe.db.add_index("Grievance", ["status", "sla_due_date"])
	frappe.db.add_index("Grievance", ["assigned_to", "status"])
	frappe.db.add_index("Grievance", ["submitter", "status"])
