"""Repoint free-text zone values at Zone records, then load the hierarchy.

`Woreda.zone` and `Grievance.zone` were Data fields holding a zone name typed by
hand. They are now Links. Existing rows carry a name that may or may not match a
seeded Zone, and a Link column holding an unresolvable name is worse than an
empty one: it renders as a broken link and every report joining through it drops
the row silently.
"""

import frappe

from oan_grievance_service.setup import locations


def execute():
	locations.seed_locations()

	_relink("Woreda", "region")
	_relink("Grievance", "region")


def _relink(doctype, region_field):
	"""Match the old free-text zone name against Zone records in the same region.

	Scoped by region because zone names repeat across regions -- Ethiopia has
	several called "Western" -- so an unscoped name match would attach woredas to
	a zone in the wrong region.
	"""
	rows = frappe.db.sql(
		f"""SELECT name, zone, `{region_field}` AS region
		    FROM `tab{doctype}`
		    WHERE zone IS NOT NULL AND zone != ''""",
		as_dict=True,
	)

	resolved = unresolved = 0
	for row in rows:
		if frappe.db.exists("Zone", row.zone):
			continue  # already a valid link

		match = frappe.db.get_value("Zone", {"zone_name": row.zone, "region": row.region}, "name")
		if match:
			frappe.db.set_value(doctype, row.name, "zone", match, update_modified=False)
			resolved += 1
		else:
			# Clear rather than leave dangling. The woreda still carries its region,
			# so nothing that matters is lost and the field can be set by hand.
			frappe.db.set_value(doctype, row.name, "zone", None, update_modified=False)
			unresolved += 1

	if resolved or unresolved:
		print(f"{doctype}.zone: {resolved} linked, {unresolved} cleared")
