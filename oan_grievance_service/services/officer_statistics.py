# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Officer performance statistics, computed live from Grievance and its RBAC desks.

Nothing here is stored. The roster (who is an L1 or L2 officer) comes from the officer
rows of active Grievance RBAC Assignments. The numbers come from one aggregate over
Grievance per page of officers:

- assigned: submitted grievances whose `assigned_to` is the officer right now. A case
  moved to another officer counts for the new one only.
- resolved: those of them now at Resolved or Closed.
- avg_resolution_hours: mean of creation to `resolved_at` over the resolved ones, the same
  clock the dashboard rollups use. None while the officer has resolved nothing.
- resolution_rate: resolved / assigned as a percentage, 0 when nothing is assigned.
"""

import frappe
from frappe.query_builder import Case, CustomFunction
from frappe.query_builder.functions import Avg, Count, Max, Sum
from pypika.terms import LiteralValue

from oan_grievance_service.services import constants as C

DESK = "Grievance RBAC Assignment"
OFFICER_ROW = "Grievance RBAC Assignment Officer"

# Grievance Role Level codes seeded by setup/install.py, keyed by the tier the admin tabs show.
LEVEL_CODES = {"L1": "nodal_officer", "L2": "senior_nodal_officer"}
LEVEL_BY_CODE = {code: level for level, code in LEVEL_CODES.items()}


def _roster(level: str | None, department: str | None):
	"""Query over officer rows on active desks, joined to the user for the name."""
	Officer = frappe.qb.DocType(OFFICER_ROW)
	Desk = frappe.qb.DocType(DESK)
	User = frappe.qb.DocType("User")
	codes = [LEVEL_CODES[level]] if level else list(LEVEL_CODES.values())
	query = (
		frappe.qb.from_(Officer)
		.join(Desk)
		.on((Desk.name == Officer.parent) & (Officer.parenttype == DESK))
		.left_join(User)
		.on(User.name == Officer.user)
		.where((Officer.active == 1) & (Desk.active == 1) & Officer.role_level.isin(codes))
	)
	if department:
		query = query.where(Desk.department_scope == department)
	return query, Officer, User


def list_officers(*, level: str | None, department: str | None, start: int, limit: int):
	"""One page of officers and the total, ordered by name then user id."""
	query, Officer, User = _roster(level, department)
	pairs = query.select(Officer.user, Officer.role_level).groupby(Officer.user, Officer.role_level)
	total = frappe.qb.from_(pairs.as_("pairs")).select(Count("*")).run()[0][0]
	rows = (
		query.select(Officer.user, Officer.role_level, Max(User.full_name).as_("full_name"))
		.groupby(Officer.user, Officer.role_level)
		.orderby(Max(User.full_name))
		.orderby(Officer.user)
		.limit(limit)
		.offset(start)
		.run(as_dict=True)
	)
	return rows, total


def statistics_for(users: list[str]) -> dict[str, dict]:
	"""assigned, resolved and average resolution hours for each user, in one query."""
	if not users:
		return {}
	G = frappe.qb.DocType("Grievance")
	is_resolved = G.workflow_state.isin(list(C.RESOLVED_STATES))
	seconds = CustomFunction("TIMESTAMPDIFF", ["unit", "start", "end"])(
		LiteralValue("SECOND"), G.creation, G.resolved_at
	)
	rows = (
		frappe.qb.from_(G)
		.select(
			G.assigned_to.as_("user"),
			Count("*").as_("assigned"),
			Sum(Case().when(is_resolved, 1).else_(0)).as_("resolved"),
			(Avg(Case().when(is_resolved & G.resolved_at.notnull(), seconds)) / 3600).as_("avg_hours"),
		)
		.where((G.docstatus == 1) & G.assigned_to.isin(users))
		.groupby(G.assigned_to)
		.run(as_dict=True)
	)
	return {row.user: row for row in rows}


def build_records(officers: list) -> list[dict]:
	"""Join the roster page with live statistics into API records."""
	stats = statistics_for(list({row.user for row in officers}))
	records = []
	for officer in officers:
		row = stats.get(officer.user)
		assigned = int(row.assigned) if row else 0
		resolved = int(row.resolved or 0) if row else 0
		avg_hours = row.avg_hours if row and row.avg_hours is not None else None
		records.append(
			{
				"user": officer.user,
				"full_name": officer.full_name,
				"level": LEVEL_BY_CODE[officer.role_level],
				"role_level": officer.role_level,
				"assigned": assigned,
				"resolved": resolved,
				"avg_resolution_hours": round(float(avg_hours), 2) if avg_hours is not None else None,
				"resolution_rate": round(resolved * 100 / assigned, 1) if assigned else 0.0,
			}
		)
	return records
