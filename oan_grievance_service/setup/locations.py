"""Load the Ethiopian administrative hierarchy: 14 regions, 101 zones, 1141 woredas.

The source is the OCHA COD-AB boundary dataset (validOn 2021-12-14), shipped as
PostGIS dumps. Only the names and P-codes are carried into `location_data.json`;
the polygon geometry is 99% of the 43 MB source and answers no question the
grievance form asks. If mapping is ever needed, the boundaries are re-joined to
these rows on `pcode`.

Everything joins on `pcode`, never on name. The source disagrees with itself
about names across levels -- region ET08 is "South Ethiopia People" in the
regions file, "South Ethiopian" in the zones file and "South Ethiopia" in the
woredas file -- and names are not unique either: Ethiopia has two woredas called
Sululta, both in Oromia.
"""

import json
import pathlib

import frappe

DATA_FILE = pathlib.Path(__file__).parent / "location_data.json"


def load_data():
	return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def seed_locations():
	"""Insert any administrative area that is not present yet.

	Idempotent: existing rows are matched on `pcode` and left alone, so an
	administrator's corrections to a name or code survive a re-run.
	"""
	data = load_data()

	region_by_pcode = _seed_regions(data["regions"])
	zone_by_pcode = _seed_zones(data["zones"], region_by_pcode)
	_seed_woredas(data["woredas"], region_by_pcode, zone_by_pcode)

	frappe.db.commit()


def _seed_regions(rows):
	"""Regions are already seeded by name in install.py, so this backfills the
	P-code onto those rows rather than inserting alongside them.

	Matching is on `code`, not on name. install.py seeds "South Ethiopia" while the
	COD-AB dataset calls the same region "South Ethiopia People"; matching on name
	would create a second Region row for ET08 and split its woredas across both.
	"""
	by_pcode = {}
	existing_by_code = {r.code: r.name for r in frappe.get_all("Region", fields=["name", "code"]) if r.code}

	for row in rows:
		name = existing_by_code.get(row["code"])
		if name:
			if not frappe.db.get_value("Region", name, "pcode"):
				frappe.db.set_value("Region", name, "pcode", row["pcode"], update_modified=False)
		else:
			name = (
				frappe.get_doc(
					{
						"doctype": "Region",
						"region_name": row["name"],
						"code": row["code"],
						"pcode": row["pcode"],
						"is_active": 1,
					}
				)
				.insert(ignore_permissions=True)
				.name
			)
		by_pcode[row["pcode"]] = name

	return by_pcode


def _seed_zones(rows, region_by_pcode):
	by_pcode = {z.pcode: z.name for z in frappe.get_all("Zone", fields=["name", "pcode"]) if z.pcode}

	for row in rows:
		if row["pcode"] in by_pcode:
			continue
		by_pcode[row["pcode"]] = (
			frappe.get_doc(
				{
					"doctype": "Zone",
					"zone_name": row["name"],
					"region": region_by_pcode[row["region_pcode"]],
					"pcode": row["pcode"],
					"code": row["code"],
					"path_code": row["path_code"],
					"is_active": 1,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	return by_pcode


def _seed_woredas(rows, region_by_pcode, zone_by_pcode):
	existing = {w.pcode for w in frappe.get_all("Woreda", fields=["pcode"]) if w.pcode}

	for row in rows:
		if row["pcode"] in existing:
			continue
		frappe.get_doc(
			{
				"doctype": "Woreda",
				"woreda_name": row["name"],
				"region": region_by_pcode[row["region_pcode"]],
				"zone": zone_by_pcode[row["zone_pcode"]],
				"pcode": row["pcode"],
				"code": row["code"],
				"path_code": row["path_code"],
				"is_active": 1,
			}
		).insert(ignore_permissions=True)
		existing.add(row["pcode"])
