# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Cached helpers for resolving officer positions and availability.

A position is a role (Crop DA, Woreda Crop SMS). An officer holds it through their rows on
desks (Grievance RBAC Assignment Officer), which carry their area, leave and capacity.
"""

import frappe

ROW = "Grievance RBAC Assignment Officer"
POSITION = "Grievance Officer Position"


def _held(user_filter, active_only: bool) -> list[dict]:
	R = frappe.qb.DocType(ROW)
	P = frappe.qb.DocType(POSITION)
	query = (
		frappe.qb.from_(R)
		.join(P)
		.on(P.name == R.position)
		.select(
			R.name,
			R.position,
			R.user,
			R.administrative_area,
			R.active,
			R.max_open_cases,
			R.on_leave,
			R.role_level,
			P.designation,
			P.position_title,
			P.reports_to_position,
		)
		.where(user_filter(R.user))
	)
	if active_only:
		query = query.where((R.active == 1) & (P.active == 1))
	return query.run(as_dict=True)


def positions_of(user: str, active_only: bool = True) -> list[dict]:
	"""The desk rows `user` holds positions through, with the position's role, cached per request."""
	if not user:
		return []

	cache = getattr(frappe.local, "grievance_user_positions", None)
	if cache is None:
		cache = frappe.local.grievance_user_positions = {}

	cache_key = (user, active_only)
	if cache_key not in cache:
		cache[cache_key] = _held(lambda col: col == user, active_only)
	return cache[cache_key]


def prefetch_positions(users: list[str]) -> None:
	"""Load the active rows of all `users` in one query into the per-request cache."""
	cache = getattr(frappe.local, "grievance_user_positions", None)
	if cache is None:
		cache = frappe.local.grievance_user_positions = {}
	missing = [u for u in set(users) if u and (u, True) not in cache]
	if not missing:
		return
	for user in missing:
		cache[(user, True)] = []
	for row in _held(lambda col: col.isin(missing), True):
		cache[(row.user, True)].append(row)


def supervisor_of(position: str | None, administrative_area: str | None) -> str | None:
	"""The user `position` reports to for `administrative_area`.

	That is a holder of the position's `reports_to_position` whose area is the nearest one
	covering `administrative_area` (the area itself or an ancestor). Holders without an area
	cover everywhere and are the last resort. Several holders at the same area: the first by
	user, so the answer is stable.
	"""
	sup_position = position and frappe.db.get_value(POSITION, position, "reports_to_position")
	if not sup_position:
		return None
	holders = frappe.get_all(
		ROW,
		filters={"position": sup_position, "active": 1, "user": ["is", "set"]},
		fields=["user", "administrative_area"],
		order_by="user asc",
		distinct=True,
	)
	if not holders:
		return None
	bounds = (
		frappe.db.get_value("Grievance Administrative Area", administrative_area, ["lft", "rgt"])
		if administrative_area
		else None
	)
	areas = {
		a.name: a
		for a in frappe.get_all(
			"Grievance Administrative Area",
			filters={"name": ["in", [h.administrative_area for h in holders if h.administrative_area]]},
			fields=["name", "lft", "rgt"],
		)
	}
	best, best_lft = None, -1
	for holder in holders:
		area = areas.get(holder.administrative_area)
		if area is None:
			if best is None:
				best = holder.user
			continue
		covers = bounds and area.lft <= bounds[0] and bounds[1] <= area.rgt
		if covers and area.lft > best_lft:
			best, best_lft = holder.user, area.lft
	return best


def claim_on_login(user: str, principal: dict, login_option: str) -> bool:
	"""`oan_auth_on_delegated_login` hook: give `user` the desk rows waiting on their email.

	Only rows with no user yet are claimed, so it acts on the first sign-in and a later
	login with the same email takes nothing from anyone.
	"""
	email = (principal.get("email") or "").strip().lower()
	if not email:
		return False
	R = frappe.qb.DocType(ROW)
	waiting = (R.email == email) & (R.user.isnull() | (R.user == ""))
	if not frappe.qb.from_(R).select(R.name).where(waiting).limit(1).run():
		return False
	frappe.qb.update(R).set(R.user, user).where(waiting).run()
	clear_position_cache()
	return True


def clear_position_cache(user: str | None = None) -> None:
	"""Clear the per-request position cache in frappe.local."""
	cache = getattr(frappe.local, "grievance_user_positions", None)
	if cache is None:
		return
	if user:
		cache.pop((user, True), None)
		cache.pop((user, False), None)
	else:
		frappe.local.grievance_user_positions = {}


def is_officer_on_leave(user: str) -> bool:
	"""Whether any of `user`'s active desk rows marks them on leave."""
	return any(row.get("on_leave") for row in positions_of(user))


def max_open_cases_for(user: str) -> int:
	"""Return the capacity cap for `user` across their active desk rows (0 = uncapped)."""
	if not user:
		return 0
	positions = positions_of(user)
	caps = [pos.get("max_open_cases") or 0 for pos in positions if pos.get("max_open_cases")]
	return max(caps) if caps else 0
