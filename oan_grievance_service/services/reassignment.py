"""Reassignment rules: resolve receiving desk, validate, route to receiving dept head, and apply.

Rules:
- Reassignment requires the approval of the receiving department's head.
- For a move within the same department, that is the department's own head.
- Approver = department_head_of(target department, area).
- Decider may act directly if admin, if the desk flag reassignment_requires_approval is OFF,
  or if the user is the head of the target department.
- Category and type follow receiving desks; SLA restarts if category changed.
"""

import frappe
from frappe import _

from oan_grievance_service.grievance_access_control.doctype.grievance_rbac_assignment.grievance_rbac_assignment import (
	department_head_of,
)
from oan_grievance_service.permissions import is_department_head, is_unrestricted
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import notifications
from oan_grievance_service.services.routing import (
	department_desks,
	find_matching_assignment,
	officer_desks,
	pick_officer_by_strategy,
)

TRIGGER_FIELDS = ("assigned_dept", "assigned_to")


def is_reassignment(request) -> bool:
	"""Whether the change request is a reassignment (touches assigned_dept or assigned_to)."""
	changes = getattr(request, "changes", None) or []
	return any(getattr(r, "fieldname", None) in TRIGGER_FIELDS for r in changes)


def target_department(grievance, request):
	"""The target department: request's assigned_dept if changed, else grievance.assigned_dept."""
	changes = getattr(request, "changes", None) or []
	for r in changes:
		if getattr(r, "fieldname", None) == "assigned_dept":
			return r.new_value
	return grievance.assigned_dept


def desk_requires_approval(grievance) -> bool:
	"""Check whether the source desk requires department head approval for reassignment."""
	dept = getattr(grievance, "assigned_dept", None)
	officer = getattr(grievance, "assigned_to", None)

	routing_rule = getattr(grievance, "routing_rule", None)
	if routing_rule:
		rule_dept = frappe.db.get_value("Grievance RBAC Assignment", routing_rule, "department_scope")
		if not rule_dept or rule_dept == dept:
			val = frappe.db.get_value(
				"Grievance RBAC Assignment", routing_rule, "reassignment_requires_approval"
			)
			if val is not None:
				return bool(val)

	if dept and officer:
		desks = officer_desks(grievance, dept, officer)
		if desks:
			val = desks[0].get("reassignment_requires_approval")
			if val is not None:
				return bool(val)
			val = frappe.db.get_value(
				"Grievance RBAC Assignment", desks[0].get("name"), "reassignment_requires_approval"
			)
			if val is not None:
				return bool(val)

	if dept:
		desks = department_desks(grievance, dept)
		if desks:
			val = desks[0].get("reassignment_requires_approval")
			if val is not None:
				return bool(val)
			val = frappe.db.get_value(
				"Grievance RBAC Assignment", desks[0].get("name"), "reassignment_requires_approval"
			)
			if val is not None:
				return bool(val)

	desk = find_matching_assignment(grievance)
	if desk:
		if not dept or not desk.get("department_scope") or desk.get("department_scope") == dept:
			val = desk.get("reassignment_requires_approval")
			if val is not None:
				return bool(val)
			val = frappe.db.get_value(
				"Grievance RBAC Assignment", desk.get("name"), "reassignment_requires_approval"
			)
			if val is not None:
				return bool(val)

	return True


def may_decide(user, grievance, request) -> bool:
	"""Whether `user` may directly approve this reassignment without waiting for higher authority."""
	if is_unrestricted(user):
		return True
	if not desk_requires_approval(grievance):
		return True
	target_dept = target_department(grievance, request)
	return is_department_head(user, target_dept, grievance.administrative_area)


def approver(grievance, request):
	"""The target department's head who must rule on the reassignment. None -> admin queue."""
	target_dept = target_department(grievance, request)
	return department_head_of(target_dept, grievance.administrative_area)


def resolve(grievance, department, officer=None, actor=None, category=None, grievance_type=None) -> dict:
	"""Resolve receiving department, officer, category and type for a reassignment.

	Returns a dict of fields that differ from current grievance values.
	Throws ValidationError if the move is invalid or underspecified.
	"""
	actor = actor or frappe.session.user
	admin = is_unrestricted(actor)

	# 1. Candidate desks
	if officer:
		candidate_desks = officer_desks(grievance, department, officer)
	else:
		candidate_desks = department_desks(grievance, department)

	if not candidate_desks:
		if admin and officer:
			# Unrestricted admin specifying an officer with no matching desk may proceed
			return _resolve_admin_no_desk(
				grievance, department, officer, category=category, grievance_type=grievance_type
			)
		if officer:
			frappe.throw(
				_("Invalid Officer: {0} does not cover {1} in this area.").format(officer, department),
				frappe.ValidationError,
				title=_("Invalid Officer"),
			)
		frappe.throw(
			_("No assignment in {0} covers this area.").format(department),
			frappe.ValidationError,
			title=_("No Desk Available"),
		)

	# 2. Category: "Accepts X" means category_scope is empty/None or equals X
	if category:
		cat_desks = [
			d for d in candidate_desks if not d.get("category_scope") or d.get("category_scope") == category
		]
		if not cat_desks:
			target_name = officer or department
			frappe.throw(
				_("{0} does not handle {1}").format(target_name, category),
				frappe.ValidationError,
				title=_("Category Not Supported"),
			)
		candidate_desks = cat_desks
		chosen_cat = category
	else:
		accepting_current = [
			d
			for d in candidate_desks
			if not d.get("category_scope") or d.get("category_scope") == grievance.service_category
		]
		if accepting_current:
			chosen_cat = grievance.service_category
			candidate_desks = accepting_current
		else:
			distinct_cats = sorted(
				list({d.get("category_scope") for d in candidate_desks if d.get("category_scope")})
			)
			if len(distinct_cats) == 1:
				chosen_cat = distinct_cats[0]
				candidate_desks = [
					d
					for d in candidate_desks
					if not d.get("category_scope") or d.get("category_scope") == chosen_cat
				]
			elif len(distinct_cats) > 1:
				frappe.throw(
					_("Choose target_category: {0}").format(", ".join(distinct_cats)),
					frappe.ValidationError,
					title=_("Choose Category"),
				)
			else:
				chosen_cat = grievance.service_category

	# 3. Type from surviving desks
	if grievance_type:
		type_cat = frappe.db.get_value("Grievance Type", grievance_type, "service_category")
		if not type_cat or type_cat != chosen_cat:
			frappe.throw(
				_("Grievance Type {0} does not belong to category {1}").format(grievance_type, chosen_cat),
				frappe.ValidationError,
				title=_("Invalid Grievance Type"),
			)
		type_desks = [
			d
			for d in candidate_desks
			if not d.get("grievance_type_scope") or d.get("grievance_type_scope") == grievance_type
		]
		if not type_desks:
			target_name = officer or department
			frappe.throw(
				_("{0} does not handle type {1}").format(target_name, grievance_type),
				frappe.ValidationError,
				title=_("Grievance Type Not Supported"),
			)
		candidate_desks = type_desks
		chosen_type = grievance_type
	else:
		fixed_types = {
			d.get("grievance_type_scope") for d in candidate_desks if d.get("grievance_type_scope")
		}
		if len(fixed_types) == 1 and all(d.get("grievance_type_scope") for d in candidate_desks):
			chosen_type = next(iter(fixed_types))
		else:
			current_type = grievance.grievance_type
			current_type_cat = (
				frappe.db.get_value("Grievance Type", current_type, "service_category")
				if current_type
				else None
			)
			if current_type and current_type_cat == chosen_cat:
				chosen_type = current_type
			else:
				# If there is only one type under chosen category, check if desks allow it, else throw
				all_cat_types = frappe.get_all(
					"Grievance Type",
					filters={"service_category": chosen_cat, "is_active": 1},
					pluck="name",
				)
				if len(all_cat_types) == 1:
					chosen_type = all_cat_types[0]
				else:
					frappe.throw(
						_("Choose target_grievance_type for {0}").format(chosen_cat),
						frappe.ValidationError,
						title=_("Choose Grievance Type"),
					)

	# 4. Officer
	if officer:
		chosen_officer = officer
	else:
		current_officer = grievance.assigned_to
		surviving_desk_names = {d.get("name") for d in candidate_desks}
		if current_officer:
			cur_desks = officer_desks(grievance, department, current_officer)
			if any(cd.get("name") in surviving_desk_names for cd in cur_desks):
				chosen_officer = current_officer
			else:
				chosen_officer = None
		else:
			chosen_officer = None

		if not chosen_officer:
			desk_doc = frappe.get_doc("Grievance RBAC Assignment", candidate_desks[0].get("name"))
			chosen_officer = pick_officer_by_strategy(desk_doc, grievance)
			if not chosen_officer:
				frappe.throw(_("No active officer; name one."), frappe.ValidationError, title=_("No Officer"))

	# 5. Difference comparison
	changes = {}
	if department != grievance.assigned_dept:
		changes["assigned_dept"] = department
	if chosen_officer != grievance.assigned_to:
		changes["assigned_to"] = chosen_officer
	if chosen_cat != grievance.service_category:
		changes["service_category"] = chosen_cat
	if chosen_type != grievance.grievance_type:
		changes["grievance_type"] = chosen_type

	if not changes:
		frappe.throw(_("Nothing to change"), frappe.ValidationError, title=_("Nothing To Change"))

	return changes


def _resolve_admin_no_desk(grievance, department, officer, category=None, grievance_type=None) -> dict:
	"""Admin with no desk: category and type come from choices if given, else unchanged."""
	if category:
		if not frappe.db.exists("Grievance Service Category", category):
			frappe.throw(
				_("Service Category '{0}' does not exist.").format(category),
				frappe.ValidationError,
				title=_("Invalid Service Category"),
			)
		chosen_cat = category
	else:
		chosen_cat = grievance.service_category

	if grievance_type:
		type_cat = frappe.db.get_value("Grievance Type", grievance_type, "service_category")
		if not type_cat or type_cat != chosen_cat:
			frappe.throw(
				_("Grievance Type '{0}' does not exist for category '{1}'.").format(
					grievance_type, chosen_cat
				),
				frappe.ValidationError,
				title=_("Invalid Grievance Type"),
			)
		chosen_type = grievance_type
	else:
		current_type = grievance.grievance_type
		current_type_cat = (
			frappe.db.get_value("Grievance Type", current_type, "service_category") if current_type else None
		)
		if current_type and current_type_cat == chosen_cat:
			chosen_type = current_type
		else:
			all_cat_types = frappe.get_all(
				"Grievance Type",
				filters={"service_category": chosen_cat, "is_active": 1},
				pluck="name",
			)
			if len(all_cat_types) == 1:
				chosen_type = all_cat_types[0]
			else:
				frappe.throw(
					_("Choose target_grievance_type for {0}").format(chosen_cat),
					frappe.ValidationError,
					title=_("Choose Grievance Type"),
				)

	changes = {}
	if department != grievance.assigned_dept:
		changes["assigned_dept"] = department
	if officer != grievance.assigned_to:
		changes["assigned_to"] = officer
	if chosen_cat != grievance.service_category:
		changes["service_category"] = chosen_cat
	if chosen_type != grievance.grievance_type:
		changes["grievance_type"] = chosen_type

	if not changes:
		frappe.throw(_("Nothing to change"), frappe.ValidationError, title=_("Nothing To Change"))

	return changes


def validate(grievance, request):
	"""Validate reassignment request changes against receiving desk rules."""
	changes = getattr(request, "changes", None) or []
	proposed_dept = target_department(grievance, request)
	proposed_officer = grievance.assigned_to
	proposed_cat = grievance.service_category
	proposed_type = grievance.grievance_type

	for r in changes:
		fn = getattr(r, "fieldname", None)
		if fn == "assigned_to":
			proposed_officer = r.new_value
		elif fn == "service_category":
			proposed_cat = r.new_value
		elif fn == "grievance_type":
			proposed_type = r.new_value

	if grievance.assigned_to and not proposed_officer:
		frappe.throw(
			_("Cannot clear assigned officer on an assigned grievance."),
			frappe.ValidationError,
			title=_("Invalid Officer"),
		)

	if not is_unrestricted(request.requested_by) and proposed_officer:
		desks = officer_desks(grievance, proposed_dept, proposed_officer)
		accepting = [
			d
			for d in desks
			if (not d.get("category_scope") or d.get("category_scope") == proposed_cat)
			and (not d.get("grievance_type_scope") or d.get("grievance_type_scope") == proposed_type)
		]
		if not accepting:
			frappe.throw(_("Invalid Officer"), frappe.ValidationError, title=_("Invalid Officer"))


def on_pending(grievance, request):
	"""Queue notification when reassignment request goes pending."""
	if request.pending_with:
		notifications.queue(
			grievance, C.EVENT_REASSIGNMENT_REQUESTED, recipient_override=request.pending_with
		)


def on_applied(grievance, request):
	"""Apply side effects of an approved reassignment (SLA reset, notifications)."""
	from oan_grievance_service.services import sla

	changes = getattr(request, "changes", None) or []
	if any(getattr(r, "fieldname", None) == "service_category" for r in changes):
		sla.reset_clock(grievance)

	new_desk = find_matching_assignment(grievance)
	if new_desk:
		grievance.db_set("routing_rule", new_desk.get("name"), update_modified=False)

	officer_row = next((r for r in changes if getattr(r, "fieldname", None) == "assigned_to"), None)
	dept_row = next((r for r in changes if getattr(r, "fieldname", None) == "assigned_dept"), None)

	if officer_row and officer_row.new_value:
		if officer_row.new_value != request.decided_by:
			notifications.queue(grievance, C.EVENT_ASSIGNED_MANUAL, recipient_override=officer_row.new_value)
	elif dept_row and dept_row.new_value:
		notifications.queue(grievance, C.EVENT_ASSIGNED_MANUAL)
