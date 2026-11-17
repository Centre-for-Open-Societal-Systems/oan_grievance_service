# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Give the catch-all category and type of an existing site their STG-512 codes.

Fresh sites seed the Other category with code SYS and the Other type as SYS on the Unrouted desk.
Seeding skips what exists, so a site installed earlier would keep the old `006` code, and an Other
type with no code and no department. That also lets the seed data add a second Other type, because
the loader cannot tell it from the one already there.

Both records are saved through their controllers, which refuse what must not change. The category
keeps `006` once any ticket carries it, because a code that has appeared in a ticket number is frozen.
The type does not take part in the ticket number, so it is updated either way.
"""

import frappe
from oan_auth_service.api import operational_log as oplog

from oan_grievance_service.services import constants as C
from oan_grievance_service.setup.install import complete_catch_all_type

OLD_SORT_ORDER = 6
NEW_SORT_ORDER = 20


def execute():
	category = C.FALLBACK_SERVICE_CATEGORY
	if not frappe.db.exists("Grievance Service Category", category):
		return
	move_category(category)
	type_name = frappe.db.get_value(
		"Grievance Type", {"type_name": C.FALLBACK_GRIEVANCE_TYPE, "service_category": category}, "name"
	)
	if type_name:
		complete_catch_all_type(type_name)


def move_category(category: str) -> None:
	doc = frappe.get_doc("Grievance Service Category", category)
	if doc.code == C.FALLBACK_CATEGORY_CODE:
		return
	old_code = doc.code
	doc.code = C.FALLBACK_CATEGORY_CODE
	if doc.sort_order == OLD_SORT_ORDER:
		doc.sort_order = NEW_SORT_ORDER
	try:
		doc.save(ignore_permissions=True)
	except (frappe.ValidationError, frappe.DuplicateEntryError) as error:
		# In use, or the code is taken. A patch must not stop the migration over it.
		frappe.clear_messages()
		oplog.emit(
			"taxonomy.catch_all_code_kept",
			service_category=category,
			code=old_code,
			reason=str(error)[:200],
		)
