# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""The installation-wide SLA settings, as a Single.

A deferral ceiling is not an SLA window. `sla_days`, the auto-escalate and
notify-on-breach flags are per service category and live on `Grievance SLA
Configuration`; what lives here is what has exactly one answer for the whole
installation, which is what makes this a Single rather than another per-category row:

- how far one deferral may push a deadline, and who is allowed to decide it;
- the share of the window a case may consume before it escalates, for every category
  that does not set a threshold of its own.

It also retires two duplicates. `max_deferral_days` and `deferral_requires_l2_approval`
existed as per-category fields that nothing ever read, and the ceiling was actually
taken from the `grievance_max_deferral_days` site config key - three homes for one
number, with no rule for which won. This is the one home.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from oan_grievance_service.services import constants as C

# Who decides a deferral. The Check field below stores it, these are the two names a client sees.
L2_APPROVAL = "l2_approval"
L1_SELF_APPROVE = "l1_self_approve"


class GrievanceDeferralPolicy(Document):
	def set_deferral_approval(self, approval: str):
		"""Store the L2 / L1 self-approve choice on `requires_supervisor_approval`."""
		if approval not in (L2_APPROVAL, L1_SELF_APPROVE):
			frappe.throw(_("Deferral approval must be {0} or {1}.").format(L2_APPROVAL, L1_SELF_APPROVE))
		self.requires_supervisor_approval = 1 if approval == L2_APPROVAL else 0

	def validate(self):
		if self.max_deferral_days is not None and self.max_deferral_days < 0:
			frappe.throw(_("The deferral ceiling cannot be negative."))
		if (self.auto_escalation_threshold or 0) > 100:
			frappe.throw(_("The auto escalation threshold cannot exceed 100%."))


def get_policy():
	"""The deferral policy, with the seed defaults when the Single is untouched."""
	return frappe.get_cached_doc("Grievance Deferral Policy")


def max_deferral_days():
	"""Ceiling on a single deferral request."""
	configured = get_policy().max_deferral_days
	return configured if configured else C.DEFAULT_MAX_DEFERRAL_DAYS


def requires_supervisor_approval():
	"""Whether a deferral must be decided by someone above the assigned officer."""
	return bool(get_policy().requires_supervisor_approval)


def deferral_approval():
	"""Who decides a deferral: `l2_approval` or `l1_self_approve`."""
	return L2_APPROVAL if requires_supervisor_approval() else L1_SELF_APPROVE


def auto_escalation_threshold():
	"""Share of the window consumed before escalation, where a category sets no threshold."""
	return get_policy().auto_escalation_threshold or C.DEFAULT_ESCALATION_THRESHOLD
