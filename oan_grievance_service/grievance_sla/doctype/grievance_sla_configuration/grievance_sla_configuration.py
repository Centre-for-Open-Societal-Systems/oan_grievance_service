# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from oan_grievance_service.services import constants as C


def get_active_config(name: str):
	"""The active SLA configuration with this name, or DoesNotExistError.

	An inactive row is not served: routing ignores it, so editing it would change nothing
	a case ever sees.
	"""
	if not name or not frappe.db.exists("Grievance SLA Configuration", {"name": name, "active": 1}):
		frappe.throw(
			_("SLA configuration '{0}' was not found.").format(name),
			frappe.DoesNotExistError,
		)
	return frappe.get_doc("Grievance SLA Configuration", name)


def save_sla_configuration(
	service_category: str,
	sla_days: int,
	auto_escalate: bool = True,
	notify_on_breach: bool | None = None,
):
	"""Create or update the active SLA configuration for a service category."""
	name = frappe.db.get_value(
		"Grievance SLA Configuration",
		{"service_category": service_category, "active": 1},
		"name",
	)
	sla = (
		frappe.get_doc("Grievance SLA Configuration", name)
		if name
		else frappe.new_doc("Grievance SLA Configuration")
	)
	sla.service_category = service_category
	sla.sla_days = sla_days
	sla.auto_escalate = 1 if auto_escalate else 0
	if notify_on_breach is not None:
		sla.notify_on_breach = 1 if notify_on_breach else 0
	sla.active = 1
	if sla.is_new():
		for state, hours, on_expiry in C.DEFAULT_STATE_TIMERS:
			sla.append("state_timers", {"workflow_state": state, "hours": hours, "on_expiry": on_expiry})
	sla.save()
	return sla


class GrievanceSLAConfiguration(Document):
	def validate(self):
		self.validate_one_active_row()
		if self.sla_days is not None and self.sla_days <= 0:
			frappe.throw(_("SLA Days must be greater than zero."))
		if (self.auto_escalation_threshold or 0) > 100:
			frappe.throw(_("Auto Escalation Threshold cannot exceed 100%."))
		if self.first_response_hours is not None and self.first_response_hours < 0:
			frappe.throw(_("First Response Hours cannot be negative."))
		if self.update_cadence_hours is not None and self.update_cadence_hours < 0:
			frappe.throw(_("Update Cadence Hours cannot be negative."))
		if self.remand_execution_hours is not None and self.remand_execution_hours < 0:
			frappe.throw(_("Remand Execution Hours cannot be negative."))
		if self.holiday_list and not frappe.db.exists("Grievance Holiday List", self.holiday_list):
			frappe.throw(_("Holiday List '{0}' does not exist.").format(self.holiday_list))
		self.validate_state_timers()
		self.validate_level_turnarounds()

	def validate_level_turnarounds(self):
		"""A turnaround is a positive number of days, for an active level, set once per level and type.

		A type belongs to this category: a row for another category's type would never apply.
		"""
		seen = set()
		for row in self.level_turnarounds:
			if (row.turnaround_days or 0) <= 0:
				frappe.throw(_("Row {0}: Turnaround must be greater than zero.").format(row.idx))
			key = (row.role_level, row.grievance_type or "")
			if key in seen:
				frappe.throw(
					_("Row {0}: {1} already has a turnaround{2}.").format(
						row.idx,
						row.role_level,
						_(" for {0}").format(row.grievance_type) if row.grievance_type else "",
					)
				)
			seen.add(key)
			if not frappe.db.get_value("Grievance Role Level", row.role_level, "is_active"):
				frappe.throw(_("Row {0}: {1} is not an active role level.").format(row.idx, row.role_level))
			if row.grievance_type:
				category = frappe.db.get_value("Grievance Type", row.grievance_type, "service_category")
				if category != self.service_category:
					frappe.throw(
						_("Row {0}: {1} belongs to {2}, not {3}.").format(
							row.idx, row.grievance_type, category, self.service_category
						)
					)

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
