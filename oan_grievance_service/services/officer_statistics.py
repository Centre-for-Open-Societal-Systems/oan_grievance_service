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

from oan_grievance_service.services import constants as C

DESK = "Grievance RBAC Assignment"
OFFICER_ROW = "Grievance RBAC Assignment Officer"

# Grievance Role Level codes seeded by setup/install.py, keyed by the tier the admin tabs show.
LEVEL_CODES = {"L1": "nodal_officer", "L2": "senior_nodal_officer"}
LEVEL_BY_CODE = {code: level for level, code in LEVEL_CODES.items()}


def _roster_query(level: str | None, department: str | None) -> tuple[str, dict]:
	"""FROM/WHERE for distinct (user, role_level) pairs staffing an active desk."""
	codes = [LEVEL_CODES[level]] if level else list(LEVEL_CODES.values())
	values = {"codes": codes}
	department_clause = ""
	if department:
		department_clause = "AND desk.department_scope = %(department)s"
		values["department"] = department
	sql = f"""
		FROM `tab{OFFICER_ROW}` officer
		JOIN `tab{DESK}` desk ON desk.name = officer.parent AND officer.parenttype = %(desk)s
		LEFT JOIN `tabUser` user ON user.name = officer.user
		WHERE officer.active = 1 AND desk.active = 1
			AND officer.role_level IN %(codes)s
			{department_clause}
	"""
	values["desk"] = DESK
	return sql, values


def list_officers(*, level: str | None, department: str | None, start: int, limit: int):
	"""One page of officers and the total, ordered by name then user id."""
	sql, values = _roster_query(level, department)
	total = frappe.db.sql(
		f"SELECT COUNT(*) FROM (SELECT officer.user, officer.role_level {sql} "
		"GROUP BY officer.user, officer.role_level) pairs",
		values,
	)[0][0]
	rows = frappe.db.sql(
		f"""SELECT officer.user AS user, officer.role_level AS role_level,
			MAX(user.full_name) AS full_name
		{sql}
		GROUP BY officer.user, officer.role_level
		ORDER BY MAX(user.full_name), officer.user
		LIMIT %(limit)s OFFSET %(start)s""",
		{**values, "limit": limit, "start": start},
		as_dict=True,
	)
	return rows, total


def statistics_for(users: list[str]) -> dict[str, dict]:
	"""assigned, resolved and average resolution hours for each user, in one query."""
	if not users:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT assigned_to AS user,
			COUNT(*) AS assigned,
			SUM(workflow_state IN %(resolved)s) AS resolved,
			AVG(CASE WHEN workflow_state IN %(resolved)s AND resolved_at IS NOT NULL
				THEN TIMESTAMPDIFF(SECOND, creation, resolved_at) END) / 3600 AS avg_hours
		FROM `tabGrievance`
		WHERE docstatus = 1 AND assigned_to IN %(users)s
		GROUP BY assigned_to
		""",
		{"users": users, "resolved": list(C.RESOLVED_STATES)},
		as_dict=True,
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
