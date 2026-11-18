# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Officer Position representing the organizational seat/post in the hierarchy.

A position belongs to an administrative area jurisdiction, holds a role level,
and may optionally have an occupant (User). Positions are seeded upfront and remain
stable across staff turnover and leaves. Department scoping is defined on the Desk
(Grievance RBAC Assignment), allowing a single position (e.g. KAO Head or Woreda SMS)
to serve multiple department desks without duplicate positions.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from oan_grievance_service.grievance_masters.doctype.grievance_role_level.grievance_role_level import (
	GrievanceRoleLevel,
)


class GrievanceOfficerPosition(Document):
	def validate(self):
		self.validate_occupant()
		self.validate_hierarchy()
		self.validate_leave()

	def validate_leave(self):
		if self.on_leave and self.leave_from and self.leave_to:
			if str(self.leave_to) < str(self.leave_from):
				frappe.throw(
					_("Leave To date '{0}' cannot be earlier than Leave From date '{1}'.").format(
						self.leave_to, self.leave_from
					),
					frappe.ValidationError,
				)

	def is_currently_on_leave(self, as_of_date: str | None = None) -> bool:
		if not self.on_leave:
			return False
		today = as_of_date or frappe.utils.today()
		if self.leave_from and str(self.leave_from) > today:
			return False
		if self.leave_to and str(self.leave_to) < today:
			return False
		return True

	def validate_occupant(self):
		if not self.occupant:
			return

		if not frappe.db.exists("User", self.occupant):
			frappe.throw(
				_("Occupant user '{0}' does not exist.").format(self.occupant), frappe.ValidationError
			)

		# Ensure user is enabled
		if not frappe.db.get_value("User", self.occupant, "enabled"):
			frappe.throw(
				_("Occupant user '{0}' is disabled in the system.").format(self.occupant),
				frappe.ValidationError,
			)

	def validate_hierarchy(self):
		if not self.reports_to_position:
			return

		if self.reports_to_position == self.name:
			frappe.throw(_("A position cannot report to itself."), frappe.ValidationError)

		sup = frappe.db.get_value(
			"Grievance Officer Position",
			self.reports_to_position,
			["name", "role_level", "administrative_area", "reports_to_position"],
			as_dict=True,
		)
		if not sup:
			frappe.throw(
				_("Supervisory position '{0}' does not exist.").format(self.reports_to_position),
				frappe.ValidationError,
			)

		# Circular loop detection
		visited = {self.name, self.reports_to_position}
		curr = sup.reports_to_position
		while curr:
			if curr == self.name:
				frappe.throw(
					_("Circular reporting loop detected: {0} reports up to {1}.").format(
						self.name, self.reports_to_position
					),
					frappe.ValidationError,
				)
			if curr in visited:
				break
			visited.add(curr)
			curr = frappe.db.get_value("Grievance Officer Position", curr, "reports_to_position")

		# Seniority validation: supervisor must be on a higher active rung
		order = {r.name: r.level_order for r in GrievanceRoleLevel.get_chain()}
		self_order = order.get(self.role_level)
		sup_order = order.get(sup.role_level)

		if self_order is not None and sup_order is not None:
			if sup_order <= self_order:
				frappe.throw(
					_(
						"Supervisor position '{0}' (level order {1}) must have a higher role level than '{2}' ({3})."
					).format(sup.name, sup_order, self.name, self_order),
					frappe.ValidationError,
				)

		# Area containment check: supervisor's area must be an ancestor of or equal to this position's area
		if (
			self.administrative_area
			and sup.administrative_area
			and self.administrative_area != sup.administrative_area
		):
			from oan_grievance_service.services.routing import _extract_case_area_bounds

			pos_bounds = _extract_case_area_bounds(self)
			sup_bounds = _extract_case_area_bounds(sup)
			if pos_bounds and sup_bounds:
				_area_name, p_lft, p_rgt = pos_bounds
				_sup_area_name, s_lft, s_rgt = sup_bounds
				if not (s_lft <= p_lft and p_rgt <= s_rgt):
					frappe.throw(
						_("Supervisor position area '{0}' does not cover this position's area '{1}'.").format(
							sup.administrative_area, self.administrative_area
						),
						frappe.ValidationError,
					)


def on_doctype_update():
	frappe.db.add_index("Grievance Officer Position", ["role_level", "active"])
	frappe.db.add_index("Grievance Officer Position", ["administrative_area"])
	frappe.db.add_index("Grievance Officer Position", ["occupant"])
