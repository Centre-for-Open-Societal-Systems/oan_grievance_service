# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Cached helpers for resolving officer positions and availability."""

import frappe
from frappe.utils import getdate, nowdate


def positions_of(user: str, active_only: bool = True) -> list[dict]:
	"""Return the list of positions occupied by `user`, cached per request in frappe.local."""
	if not user:
		return []

	cache = getattr(frappe.local, "grievance_user_positions", None)
	if cache is None:
		cache = frappe.local.grievance_user_positions = {}

	cache_key = (user, active_only)
	if cache_key in cache:
		return cache[cache_key]

	filters = {"occupant": user}
	if active_only:
		filters["active"] = 1

	positions = frappe.get_all(
		"Grievance Officer Position",
		filters=filters,
		fields=[
			"name",
			"position_title",
			"role_level",
			"administrative_area",
			"designation",
			"occupant",
			"reports_to_position",
			"active",
			"max_open_cases",
			"on_leave",
			"leave_from",
			"leave_to",
		],
	)
	cache[cache_key] = positions
	return positions


def is_officer_on_leave(user: str, as_of_date: str | None = None) -> bool:
	"""Check if an officer user is currently on leave via their occupied Position(s)."""
	if not user:
		return False

	positions = positions_of(user)
	if not positions:
		return False

	as_of = getdate(as_of_date or nowdate())
	for pos in positions:
		if not pos.get("on_leave"):
			continue
		l_from = pos.get("leave_from")
		l_to = pos.get("leave_to")
		if not l_from and not l_to:
			return True
		if l_from and as_of < getdate(l_from):
			continue
		if l_to and as_of > getdate(l_to):
			continue
		return True

	return False


def max_open_cases_for(user: str) -> int:
	"""Return the capacity cap for `user` across their active positions (0 = uncapped)."""
	if not user:
		return 0
	positions = positions_of(user)
	caps = [pos.get("max_open_cases") or 0 for pos in positions if pos.get("max_open_cases")]
	return max(caps) if caps else 0
