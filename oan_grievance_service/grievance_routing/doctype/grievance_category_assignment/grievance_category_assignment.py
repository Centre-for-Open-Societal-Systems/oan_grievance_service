# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""One routing rule per service category for the Category Assignments admin tab.

Saving a record projects it onto the two entities the rest of the service already
reads: a Grievance RBAC Assignment desk (department plus L1/L2 roster, which
auto-routing uses) and the category's Grievance SLA Configuration (window and
auto-escalate). Deactivate rather than delete — the DocType grants no delete
permission, and a retired rule must stay available for audit.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import today

L1_ROLE_LEVEL = "nodal_officer"
L2_ROLE_LEVEL = "senior_nodal_officer"
OFFICER_ROLES = {"Grievance Officer", "Grievance Admin"}


class GrievanceCategoryAssignment(Document):
	def validate(self):
		if not self.sla_days or int(self.sla_days) <= 0:
			frappe.throw(_("SLA Days must be greater than zero."), frappe.ValidationError)
		if self.priority not in ("Low", "Normal", "High"):
			frappe.throw(_("Priority must be Low, Normal, or High."), frappe.ValidationError)
		if self.l1_officer and self.l2_officer and self.l1_officer == self.l2_officer:
			frappe.throw(
				_("L1 and L2 officers must be different users."),
				frappe.ValidationError,
			)
		_assert_officer(self.l1_officer, "L1 officer")
		if self.l2_officer:
			_assert_officer(self.l2_officer, "L2 officer")
		_assert_active_link(
			"Grievance Service Category", self.service_category, "is_active", "Service category"
		)
		_assert_active_link("Grievance Department", self.department, "active", "Department")
		for level in (L1_ROLE_LEVEL, L2_ROLE_LEVEL):
			if not frappe.db.exists("Grievance Role Level", level):
				frappe.throw(
					_("Role level {0} is not configured.").format(level),
					frappe.ValidationError,
				)

	def on_update(self):
		if self.flags.get("projecting"):
			return
		self.flags.projecting = True
		try:
			self._project_sla()
			self._project_desk()
		finally:
			self.flags.projecting = False

	def _project_sla(self):
		"""Keep the category SLA row aligned with this assignment's window and flag."""
		values = {
			"sla_days": int(self.sla_days),
			"auto_escalate": 1 if self.auto_escalate else 0,
			"active": 1 if self.active else 0,
		}
		name = frappe.db.get_value(
			"Grievance SLA Configuration",
			{"service_category": self.service_category},
			"name",
		)
		if name:
			frappe.db.set_value("Grievance SLA Configuration", name, values)
			return
		frappe.get_doc(
			{
				"doctype": "Grievance SLA Configuration",
				"service_category": self.service_category,
				**values,
			}
		).insert()

	def _project_desk(self):
		"""Own one category-scoped desk so auto-routing assigns the L1 officer."""
		if self.rbac_assignment and frappe.db.exists("Grievance RBAC Assignment", self.rbac_assignment):
			desk = frappe.get_doc("Grievance RBAC Assignment", self.rbac_assignment)
		else:
			desk = frappe.new_doc("Grievance RBAC Assignment")
			desk.effective_from = today()
			desk.routing_strategy = "Primary First"
			desk.assigned_by = (
				frappe.session.user if frappe.session.user not in (None, "Guest") else "Administrator"
			)

		desk.department_scope = self.department
		desk.category_scope = self.service_category
		desk.active = 1 if self.active else 0
		desk.routing_strategy = desk.routing_strategy or "Primary First"
		desk.set("officers", [])
		desk.append(
			"officers",
			{
				"user": self.l1_officer,
				"role_level": L1_ROLE_LEVEL,
				"is_primary": 1,
				"active": 1,
			},
		)
		if self.l2_officer:
			desk.append(
				"officers",
				{
					"user": self.l2_officer,
					"role_level": L2_ROLE_LEVEL,
					"reports_to": self.l1_officer,
					"is_primary": 0,
					"active": 1,
				},
			)
		desk.save()
		if self.rbac_assignment != desk.name:
			self.rbac_assignment = desk.name
			self.db_set("rbac_assignment", desk.name, update_modified=False)


def _assert_officer(user, label):
	if not user or not frappe.db.exists("User", user):
		frappe.throw(_("{0} '{1}' does not exist.").format(label, user), frappe.ValidationError)
	if not frappe.db.get_value("User", user, "enabled"):
		frappe.throw(_("{0} '{1}' is disabled.").format(label, user), frappe.ValidationError)
	if not OFFICER_ROLES.intersection(frappe.get_roles(user)):
		frappe.throw(
			_("{0} must be a Grievance Officer or Grievance Admin.").format(label),
			frappe.ValidationError,
		)


def _assert_active_link(doctype, name, flag_field, label):
	if not name or not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} '{1}' does not exist.").format(label, name), frappe.ValidationError)
	if not frappe.db.get_value(doctype, name, flag_field):
		frappe.throw(_("{0} '{1}' is inactive.").format(label, name), frappe.ValidationError)
