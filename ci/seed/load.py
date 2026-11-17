"""Load demo data exported from a Frappe site into a site that already has its apps.

Run from the bench's sites/ directory with the bench's python:

    load.py <site> <seed directory>

Only creates what is missing, so it is safe on every deploy. A row counts as present by its name, and a
category or type also by its code or name. Records edited or added after the first run stay as they are,
and a deleted demo record comes back. Emails stay muted and document controllers do not run, so loading
sends nothing.
"""

import json
import os
import shutil
import sys

import frappe
from frappe.model import default_fields
from frappe.utils.password import update_password

# These change the schema or cached metadata when saved, so they go through their controllers.
CONTROLLER_DOCTYPES = {
	"Role",
	"Workflow State",
	"Workflow Action Master",
	"Workflow",
	"Custom Field",
	"Property Setter",
	"Notification",
}
# PostgreSQL rejects '' in these columns; MariaDB exports contain it.
TYPED_FIELDS = {
	"Date",
	"Datetime",
	"Time",
	"Int",
	"Long Int",
	"Float",
	"Currency",
	"Percent",
	"Check",
	"Duration",
}


# A record can already exist under another name: the app seeds its own catch-all category and type, and a
# site loaded before a seed change holds records the data now names differently. The loader skips a row
# when one of these keys is already taken, so the same taxonomy entry is not added twice.
ALTERNATE_KEYS = {
	"Grievance Service Category": [("code",), ("category_name",)],
	"Grievance Type": [("type_code",), ("service_category", "type_name")],
	"Grievance SLA Configuration": [("service_category",)],
}


# The seed name of a row that was skipped, to the name of the record already on the site, per doctype.
# Rows loaded later that link to the skipped one are pointed at the real record, since db_insert does not
# check links and would otherwise leave them pointing at nothing.
RENAMED = {}

# Ticket codes are upper case. The controllers enforce that, and the loader does not run them.
CODE_FIELDS = {"Grievance Service Category": "code", "Grievance Type": "type_code"}


def existing_under_another_name(doctype, row):
	"""The name of the record that already holds one of the row's alternate keys, if any."""
	for fields in ALTERNATE_KEYS.get(doctype, []):
		filters = {field: row.get(field) for field in fields}
		if any(value in (None, "") for value in filters.values()):
			continue
		name = frappe.db.exists(doctype, filters)
		if name:
			return name
	return None


def exists_under_another_name(doctype, row):
	return bool(existing_under_another_name(doctype, row))


def clean_code(doctype, row):
	field = CODE_FIELDS.get(doctype)
	if field and row.get(field) is not None:
		row[field] = str(row[field]).strip().upper() or None


def normalize(row, doctype):
	row["doctype"] = doctype
	for field in frappe.get_meta(doctype).fields:
		value = row.get(field.fieldname)
		if field.fieldtype in TYPED_FIELDS and value == "":
			row[field.fieldname] = None
		elif field.fieldtype == "Data" and field.unique and value == "":
			# Two rows with '' would collide on the unique index, where NULLs do not.
			row[field.fieldname] = None
		elif field.fieldtype == "Link" and value in RENAMED.get(field.options, {}):
			row[field.fieldname] = RENAMED[field.options][value]
		elif field.fieldtype in ("Table", "Table MultiSelect") and value:
			for child in value:
				normalize(child, field.options)


def insert(row, doctype):
	normalize(row, doctype)
	doc = frappe.get_doc(row)
	if doctype in CONTROLLER_DOCTYPES:
		doc.insert(ignore_permissions=True, ignore_links=True, ignore_mandatory=True, set_name=row["name"])
		return
	doc.db_insert()
	for child in doc.get_all_children():
		child.db_insert()


def load_doctype(doctype, rows, password):
	created = failed = skipped = 0
	for row in rows:
		name = row["name"]
		if frappe.db.exists(doctype, name):
			continue
		clean_code(doctype, row)
		existing = existing_under_another_name(doctype, row)
		if existing:
			RENAMED.setdefault(doctype, {})[name] = existing
			skipped += 1
			continue
		frappe.db.savepoint("seed_row")
		try:
			insert(row, doctype)
			if doctype == "User" and password:
				update_password(name, password, logout_all_sessions=False)
			created += 1
		except Exception as e:
			frappe.db.rollback(save_point="seed_row")
			failed += 1
			print(f"FAILED {doctype} {name}: {e}")
	frappe.db.commit()  # nosemgrep
	extra = f", {skipped} already present under another name" if skipped else ""
	print(f"{doctype}: created {created} of {len(rows)}, failed {failed}{extra}")
	return failed


def load_single(doctype, values):
	current = frappe.db.get_singles_dict(doctype)
	for key, value in values.items():
		if key in current or key.startswith("_") or not frappe.get_meta(doctype).has_field(key):
			continue
		frappe.db.set_single_value(doctype, key, value, update_modified=False)
	frappe.db.commit()  # nosemgrep
	print(f"{doctype}: single loaded")


def copy_files(seed):
	for kind in ("public", "private"):
		source = os.path.join(seed, "files", kind)
		target = frappe.get_site_path(kind, "files")
		for root, _dirs, names in os.walk(source):
			for name in names:
				path = os.path.join(target, os.path.relpath(os.path.join(root, name), source))
				if not os.path.exists(path):
					os.makedirs(os.path.dirname(path), exist_ok=True)
					shutil.copyfile(os.path.join(root, name), path)


# Doctypes whose seed rows need fields that a build of the app may not have yet. Checked as a whole before
# anything loads. Other doctypes are not: their exports carry fields of other branches that a site may lack
# without harm.
PREFLIGHT_DOCTYPES = ("Grievance Type",)


def preflight(seed, manifest):
	"""Refuse to load anything when the data holds a field this build of the site does not have.

	A seed ahead of the app would otherwise fail row by row after the earlier doctypes were committed,
	leaving a half-loaded site and a failed deploy.
	"""
	missing = {}
	for doctype in PREFLIGHT_DOCTYPES:
		if doctype not in manifest["files"] or not frappe.db.exists("DocType", doctype):
			continue
		with open(os.path.join(seed, "data", manifest["files"][doctype])) as f:  # nosemgrep
			rows = json.load(f)
		known = {field.fieldname for field in frappe.get_meta(doctype).fields} | set(default_fields)
		unknown = {key for row in rows for key in row} - known - {"doctype"}
		if unknown:
			missing[doctype] = sorted(unknown)
	if missing:
		detail = "; ".join(f"{doctype}: {', '.join(keys)}" for doctype, keys in missing.items())
		sys.exit(
			f"Seed data has fields this site lacks ({detail}). "
			"Deploy the app version that adds them, run bench migrate, then load again."
		)


def main(site, seed):
	frappe.init(site=site, sites_path=".")
	frappe.connect()
	frappe.set_user("Administrator")  # nosemgrep
	frappe.flags.in_import = True
	frappe.flags.mute_emails = True
	password = os.environ.get("SEED_USER_PASSWORD")
	with open(os.path.join(seed, "manifest.json")) as f:  # nosemgrep
		manifest = json.load(f)
	preflight(seed, manifest)
	copy_files(seed)
	failed = 0
	for doctype in manifest["order"]:
		if not frappe.db.exists("DocType", doctype):
			print(f"{doctype}: not on this site, skipped")
			continue
		with open(os.path.join(seed, "data", manifest["files"][doctype])) as f:  # nosemgrep
			rows = json.load(f)
		if doctype in manifest["singles"]:
			load_single(doctype, rows[0])
		else:
			failed += load_doctype(doctype, rows, password)
	frappe.clear_cache()
	frappe.destroy()
	if failed:
		sys.exit(f"{failed} records failed")


if __name__ == "__main__":
	main(sys.argv[1], sys.argv[2])
