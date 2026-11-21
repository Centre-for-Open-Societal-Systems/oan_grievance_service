# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""A person holding an Officer Position in an administrative area.

Many people can hold one position, each in their own area. An assignment names the
person by `user`, or by `email` alone before they first sign in: their first sign-in
fills `user` (positions.claim_on_login). Leave and capacity are the person's, so they
live here rather than on the shared position.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class GrievancePositionAssignment(Document):
	def validate(self):
		self.email = (self.email or "").strip().lower() or None
		if not self.user and not self.email:
			frappe.throw(_("Give the user, or the email they will sign in with."), frappe.ValidationError)
		if self.user and not frappe.db.get_value("User", self.user, "enabled"):
			frappe.throw(
				_("User '{0}' does not exist or is disabled.").format(self.user), frappe.ValidationError
			)
		if self.on_leave and self.leave_from and self.leave_to and str(self.leave_to) < str(self.leave_from):
			frappe.throw(
				_("Leave To date '{0}' cannot be earlier than Leave From date '{1}'.").format(
					self.leave_to, self.leave_from
				),
				frappe.ValidationError,
			)
		duplicate = frappe.db.exists(
			"Grievance Position Assignment",
			{
				"name": ["!=", self.name],
				"position": self.position,
				"administrative_area": self.administrative_area,
				**({"user": self.user} if self.user else {"email": self.email}),
			},
		)
		if duplicate:
			frappe.throw(
				_("{0} already holds this position in this area ({1}).").format(
					self.user or self.email, duplicate
				),
				frappe.DuplicateEntryError,
			)

	def on_update(self):
		from oan_grievance_service.grievance_access_control.positions import clear_position_cache

		clear_position_cache()
		Officer = frappe.qb.DocType("Grievance RBAC Assignment Officer")
		query = (
			frappe.qb.update(Officer)
			.set(Officer.position, self.position)
			.set(Officer.administrative_area, self.administrative_area)
			.where(Officer.assignment == self.name)
		)
		if self.user:
			query = query.set(Officer.user, self.user)
		query.run()

	def on_trash(self):
		from oan_grievance_service.grievance_access_control.positions import clear_position_cache

		clear_position_cache()


def on_doctype_update():
	frappe.db.add_index("Grievance Position Assignment", ["user", "active"])
	frappe.db.add_index("Grievance Position Assignment", ["position", "administrative_area"])
	frappe.db.add_index("Grievance Position Assignment", ["email"])
