# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Pre-written response text an officer starts from.

A template is written for one workflow action and scoped by department and service
category, each optional: an empty dimension matches every case. Only administrators
author templates; an officer is offered the active ones that fit the case in front of
them, most specific first, rendered with the case's details.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.workflow import get_workflow
from frappe.query_builder.functions import IfNull
from frappe.utils import formatdate, get_fullname, today
from frappe.utils.jinja import validate_template


class GrievanceResponseTemplate(Document):
	def validate(self):
		if not self.is_active:
			return
		self.validate_workflow_action()
		self.validate_links_active()
		# A syntax error would otherwise surface only when an officer loads the template.
		validate_template(self.body)

	def validate_workflow_action(self):
		# A new action is a workflow change, not master data: templates follow the workflow.
		actions = {row.action for row in get_workflow("Grievance").transitions}
		if self.workflow_action not in actions:
			frappe.throw(
				_("Workflow action '{0}' is not part of the Grievance workflow.").format(
					self.workflow_action
				),
				frappe.ValidationError,
			)

	def validate_links_active(self):
		checks = (
			("department", "Grievance Department", "active"),
			("service_category", "Grievance Service Category", "is_active"),
		)
		for fieldname, doctype, active_field in checks:
			value = self.get(fieldname)
			if value and not frappe.db.get_value(doctype, value, active_field):
				frappe.throw(
					_("{0} '{1}' is not active.").format(_(doctype), value),
					frappe.ValidationError,
				)


def templates_for(grievance, action):
	"""Active templates for workflow `action` that fit the case, most specific first.

	The case is the gate here, not the officer's own scope: whoever may respond to the
	case may use what was written for it, even when it was reassigned to them from
	outside their usual desk.
	"""
	t = frappe.qb.DocType("Grievance Response Template")
	rows = (
		frappe.qb.from_(t)
		.select(
			t.name,
			t.title,
			t.department,
			t.service_category,
			t.body,
		)
		.where(t.is_active == 1)
		.where(t.workflow_action == action)
		.where(IfNull(t.department, "").isin(["", grievance.assigned_dept or ""]))
		.where(IfNull(t.service_category, "").isin(["", grievance.service_category or ""]))
		.run(as_dict=True)
	)
	# Department outranks category: a department's wording is its own, a category's
	# wording is shared across every department that handles it.
	rows.sort(key=lambda r: (-(2 * bool(r.department) + bool(r.service_category)), r.title, r.name))
	return rows


def record_use(template):
	"""Count one response submitted from `template`.

	A single atomic UPDATE: concurrent submissions cannot lose a count, and the row's
	`modified` and change history are left alone, so the audit trail shows edits only.
	"""
	t = frappe.qb.DocType("Grievance Response Template")
	frappe.qb.update(t).set(t.usage_count, t.usage_count + 1).where(t.name == template).run()


def render(template, grievance):
	"""The template's text filled in for this case, ready to send as the action's reason."""
	# nosemgrep: frappe-semgrep-rules.rules.security.frappe-ssti
	return frappe.render_template(template.body or "", render_context(grievance))


def render_context(grievance):
	"""The variables a template may use.

	A curated set rather than the whole case: the document carries contact details,
	internal notes and the IP address, none of which belong in text sent to the
	submitter. The result is plain text, like every reason, so nothing is escaped.
	"""
	from oan_grievance_service.permissions import can_see_identity
	from oan_grievance_service.services import ticket_number as tn

	def text(value):
		return str(value) if value else ""

	return {
		"ticket_number": tn.display(grievance.ticket_number) if grievance.ticket_number else "",
		"service_category": text(grievance.service_category),
		"grievance_type": text(
			grievance.grievance_type
			and frappe.db.get_value("Grievance Type", grievance.grievance_type, "type_name")
		),
		"department": text(
			grievance.assigned_dept
			and frappe.db.get_value("Grievance Department", grievance.assigned_dept, "dept_name")
		),
		"submitter_name": text(grievance.submitter_name) if can_see_identity(grievance) else _("Submitter"),
		"officer_name": text(get_fullname(frappe.session.user)),
		"today": formatdate(today()),
		"sla_due_date": formatdate(grievance.sla_due_date) if grievance.sla_due_date else "",
	}
