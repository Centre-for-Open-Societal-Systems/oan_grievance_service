# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class GrievanceSLAConfiguration(Document):
	def validate(self):
		self.validate_one_active_row()
		if self.sla_days is not None and self.sla_days <= 0:
			frappe.throw(_("SLA Days must be greater than zero."))
		if self.first_response_hours is not None and self.first_response_hours < 0:
			frappe.throw(_("First Response Hours cannot be negative."))
		if self.update_cadence_hours is not None and self.update_cadence_hours < 0:
			frappe.throw(_("Update Cadence Hours cannot be negative."))
		if self.remand_execution_hours is not None and self.remand_execution_hours < 0:
			frappe.throw(_("Remand Execution Hours cannot be negative."))
		if self.holiday_list and not frappe.db.exists("Grievance Holiday List", self.holiday_list):
			frappe.throw(_("Holiday List '{0}' does not exist.").format(self.holiday_list))
		self.validate_state_timers()

	def validate_state_timers(self):
		"""One timer per state, and only an expiry the Workflow can carry out."""
		from frappe.model.workflow import get_workflow

		workflow = get_workflow("Grievance")
		categories = {row.state: row.get("sla_category") for row in workflow.states}
		auto_actions = {
			(row.state, row.action)
			for row in workflow.transitions
			if row.action in ("Auto Close", "Auto Resolve")
		}

		seen = set()
		for row in self.state_timers:
			if row.workflow_state in seen:
				frappe.throw(_("Row {0}: {1} already has a timer.").format(row.idx, row.workflow_state))
			seen.add(row.workflow_state)
			if row.workflow_state not in categories:
				frappe.throw(
					_("Row {0}: {1} is not a state of the Grievance Workflow.").format(
						row.idx, row.workflow_state
					)
				)
			if (row.hours or 0) <= 0:
				frappe.throw(_("Row {0}: Hours must be greater than zero.").format(row.idx))
			if (
				row.on_expiry in ("Auto Close", "Auto Resolve")
				and (row.workflow_state, row.on_expiry) not in auto_actions
			):
				frappe.throw(
					_("Row {0}: the Workflow has no {1} from {2}.").format(
						row.idx, row.on_expiry, row.workflow_state
					)
				)
			if row.on_expiry == "Escalate" and categories[row.workflow_state] == "Stopped":
				frappe.throw(
					_("Row {0}: the SLA clock is stopped in {1}, so there is nothing to escalate.").format(
						row.idx, row.workflow_state
					)
				)

	def validate_one_active_row(self):
		"""One active policy per category, so the row the API edits is the row routing enforces.

		The category row is locked first so two concurrent saves cannot both pass the check.
		"""
		if not self.active or not self.service_category:
			return
		frappe.db.get_value("Grievance Service Category", self.service_category, "name", for_update=True)
		filters = {"service_category": self.service_category, "active": 1}
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		existing = frappe.db.get_value("Grievance SLA Configuration", filters, "name", for_update=True)
		if existing:
			frappe.throw(
				_("An active SLA configuration already exists for {0} ({1}).").format(
					self.service_category, existing
				),
				frappe.DuplicateEntryError,
			)
