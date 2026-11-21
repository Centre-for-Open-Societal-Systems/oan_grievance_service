# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Officer Position: a role in the hierarchy, such as Crop DA or Woreda Crop SMS.

A position holds a role level and the position it reports to. The people who hold it,
and where, are its Grievance Position Assignments, so one position serves every area
and every holder. Department scoping is defined on the Desk (Grievance RBAC Assignment),
allowing a single position (e.g. KAO Head or Woreda SMS) to serve multiple department
desks without duplicate positions.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from oan_grievance_service.grievance_masters.doctype.grievance_role_level.grievance_role_level import (
	GrievanceRoleLevel,
)


class GrievanceOfficerPosition(Document):
	def validate(self):
		if not self.position_title and self.designation:
			self.position_title = self.designation
		elif not self.designation and self.position_title:
			self.designation = self.position_title
		self.validate_hierarchy()

	def on_update(self):
		from oan_grievance_service.grievance_access_control.positions import clear_position_cache

		clear_position_cache()
		self.sync_desk_rows()

	def sync_desk_rows(self):
		"""Keep desk membership child rows synchronized when position facts change."""
		Officer = frappe.qb.DocType("Grievance RBAC Assignment Officer")
		(
			frappe.qb.update(Officer)
			.set(Officer.role_level, self.role_level)
			.set(Officer.designation, self.designation or self.position_title)
			.where(Officer.position == self.name)
		).run()

	def on_trash(self):
		from oan_grievance_service.grievance_access_control.positions import clear_position_cache

		clear_position_cache()

	def validate_hierarchy(self):
		if not self.reports_to_position:
			return

		if self.reports_to_position == self.name:
			frappe.throw(_("A position cannot report to itself."), frappe.ValidationError)

		sup = frappe.db.get_value(
			"Grievance Officer Position",
			self.reports_to_position,
			["name", "role_level", "reports_to_position"],
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


def on_doctype_update():
	frappe.db.add_index("Grievance Officer Position", ["role_level", "active"])
