# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""The installation-wide SLA settings, as a Single.

A deferral ceiling is not an SLA window. SLA days are the service categories' SLA Rules,
and the auto-escalate and notify-on-breach flags are each desk's (Grievance RBAC
Assignment). What lives here is what has exactly one answer for the whole installation,
which is what makes this a Single rather than a per-category row:

- how far one deferral may push a deadline, and who is allowed to decide it;
- the state timers: how long a case may sit in a state, and what happens then.

`max_deferral_days` is the only limit on a deferral's length: nothing in the code caps it
further. It ships at 30 days.

It also retires two duplicates. `max_deferral_days` and `deferral_requires_l2_approval`
existed as per-category fields that nothing ever read, and the ceiling was actually
taken from the `grievance_max_deferral_days` site config key - three homes for one
number, with no rule for which won. This is the one home.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from oan_grievance_service.services import constants as C


class GrievanceDeferralPolicy(Document):
	def validate(self):
		if self.max_deferral_days is not None and self.max_deferral_days < 0:
			frappe.throw(_("The deferral ceiling cannot be negative."))
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


def get_policy():
	"""The deferral policy, with the seed defaults when the Single is untouched."""
	return frappe.get_cached_doc("Grievance Deferral Policy")


def max_deferral_days():
	"""Ceiling on a single deferral request."""
	return get_policy().max_deferral_days or C.DEFAULT_MAX_DEFERRAL_DAYS


def requires_supervisor_approval():
	"""Whether a deferral must be decided by someone above the assigned officer."""
	return bool(get_policy().requires_supervisor_approval)
def seed_state_timers():
	"""Give the policy the `DEFAULT_STATE_TIMERS` rows when it has none. Returns the states added."""
	policy = frappe.get_doc("Grievance Deferral Policy")
	if policy.state_timers:
		return []
	for state, hours, on_expiry in C.DEFAULT_STATE_TIMERS:
		policy.append("state_timers", {"workflow_state": state, "hours": hours, "on_expiry": on_expiry})
	policy.save(ignore_permissions=True)
	return [state for state, _hours, _on_expiry in C.DEFAULT_STATE_TIMERS]
