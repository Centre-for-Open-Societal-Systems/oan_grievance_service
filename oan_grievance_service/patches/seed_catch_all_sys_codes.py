# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Give the catch-all category and type of an existing site their STG-512 codes.

Fresh sites seed the Other category with code SYS and the Other type as SYS on the Unrouted desk.
Seeding skips what exists, so a site installed earlier would keep the old `006` code, and an Other
type with no code and no department. That also lets the seed data add a second Other type, because
the loader cannot tell it from the one already there.

The category code is changed only while no ticket has been issued under it: a code that has appeared
in a ticket number is frozen, so such a site keeps `006` and only the type is updated.
"""

import frappe

from oan_grievance_service.services import constants as C

OLD_SORT_ORDER = 6
NEW_SORT_ORDER = 20


def execute():
	category = C.FALLBACK_SERVICE_CATEGORY
	if not frappe.db.exists("Grievance Service Category", category):
		return
	fix_category(category)
	fix_type(category)


def fix_category(category: str) -> None:
	values = frappe.db.get_value("Grievance Service Category", category, ["code", "sort_order"], as_dict=True)
	if values.code == C.FALLBACK_CATEGORY_CODE:
		return
	issued = frappe.db.exists("Grievance", {"service_category": category, "ticket_number": ["is", "set"]})
	taken = frappe.db.exists(
		"Grievance Service Category", {"code": C.FALLBACK_CATEGORY_CODE, "name": ["!=", category]}
	)
	if issued or taken:
		frappe.logger("patches").info(
			f"Service category '{category}' keeps code {values.code}: it is in use or the code is taken."
		)
		return
	changes = {"code": C.FALLBACK_CATEGORY_CODE}
	if values.sort_order == OLD_SORT_ORDER:
		changes["sort_order"] = NEW_SORT_ORDER
	frappe.db.set_value("Grievance Service Category", category, changes, update_modified=False)


def fix_type(category: str) -> None:
	name = frappe.db.get_value(
		"Grievance Type", {"type_name": C.FALLBACK_GRIEVANCE_TYPE, "service_category": category}, "name"
	)
	if not name:
		return
	values = frappe.db.get_value("Grievance Type", name, ["type_code", "department"], as_dict=True)
	changes = {}
	if not values.type_code and not frappe.db.exists(
		"Grievance Type", {"type_code": C.FALLBACK_TYPE_CODE, "name": ["!=", name]}
	):
		changes["type_code"] = C.FALLBACK_TYPE_CODE
	if not values.department and frappe.db.exists("Grievance Department", C.UNROUTED_DEPARTMENT):
		changes["department"] = C.UNROUTED_DEPARTMENT
	if changes:
		frappe.db.set_value("Grievance Type", name, changes, update_modified=False)
