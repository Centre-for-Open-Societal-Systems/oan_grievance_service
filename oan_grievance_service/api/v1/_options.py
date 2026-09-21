"""Shared dropdown and reference data helpers for API options endpoints."""

import frappe

from oan_grievance_service.services import constants as C


def get_service_categories() -> list[dict]:
	"""Retrieve active grievance service categories."""
	return frappe.get_all(
		"Grievance Service Category",
		filters={"is_active": 1},
		fields=["name as category_name", "code", "sort_order"],
		order_by="sort_order asc, name asc",
		ignore_permissions=True,
	)


def get_grievance_types(service_category: str | None = None) -> list[dict]:
	"""Retrieve active grievance types, optionally filtered by service_category."""
	gtype_filters = [["is_active", "=", 1]]
	if service_category:
		gtype_filters.append(["service_category", "=", service_category])

	return frappe.get_all(
		"Grievance Type",
		filters=gtype_filters,
		fields=["name as grievance_type_id", "type_name", "service_category"],
		order_by="type_name asc",
		ignore_permissions=True,
	)


def active_channels() -> list[str]:
	"""The intake channels currently open, read from the master."""
	return frappe.get_all(
		"Grievance Submission Type",
		filters={"is_active": 1},
		pluck="name",
		order_by="submission_type_name asc",
	)


def get_submission_types() -> list[dict]:
	"""Retrieve active intake submission types."""
	return frappe.get_all(
		"Grievance Submission Type",
		filters={"is_active": 1},
		fields=["name as type_name", "code", "description"],
		order_by="name asc",
		ignore_permissions=True,
	)


def get_departments() -> list[dict]:
	"""Retrieve active grievance departments."""
	return frappe.get_all(
		"Grievance Department",
		filters={"active": 1},
		fields=["name as department_id", "dept_name as department_name", "email_account", "head_of_dept"],
		order_by="dept_name asc",
		ignore_permissions=True,
	)


def get_identity_schemes() -> list[dict]:
	"""Retrieve supported identity schemes and descriptions."""
	return [
		{"scheme": "fayda", "label": "Fayda National ID", "description": "National Digital ID"},
		{
			"scheme": "org",
			"label": "Registration / Certificate Number",
			"description": "Official Organization / Enterprise Registration Number",
		},
		{"scheme": "phone", "label": "Phone Number", "description": "Mobile Phone Number"},
	]


def get_submitter_types() -> list[dict]:
	"""Retrieve active submitter types with their accepted identity schemes."""
	from oan_grievance_service.services.identity import rule_for

	types = frappe.get_all(
		"Grievance Submitter Type",
		filters={"is_active": 1},
		fields=["name as type_name", "code", "description"],
		order_by="name asc",
		ignore_permissions=True,
	)
	for t in types:
		rule = rule_for(t["type_name"])
		t["allowed_schemes"] = list(rule.schemes) if rule else ["phone"]
	return types


def get_role_levels() -> list[dict]:
	"""Retrieve active role levels for escalation hierarchy."""
	return frappe.get_all(
		"Grievance Role Level",
		filters={"is_active": 1},
		fields=["name as level_code", "level_name", "level_order", "escalation_hours", "description"],
		order_by="level_order asc",
		ignore_permissions=True,
	)


def get_status_options() -> list[dict]:
	"""The lifecycle states, read dynamically from the Grievance Workflow."""
	wf_name = frappe.db.get_value("Workflow", {"document_type": "Grievance", "is_active": 1}, "name")
	if wf_name:
		wf_states = frappe.get_all(
			"Workflow Document State",
			filters={"parent": wf_name},
			fields=["state as status", "doc_status"],
			order_by="idx asc",
		)
		outgoing_states = set(
			frappe.get_all("Workflow Transition", filters={"parent": wf_name}, pluck="state")
		)
		results = []
		for s in wf_states:
			status = s["status"]
			doc_status = str(s.get("doc_status", ""))
			if status == "Draft" or doc_status == "0":
				continue
			# Terminal if doc_status is 2 (Cancelled/Rejected) or if no transitions lead out of this state
			is_terminal = 1 if doc_status == "2" or status not in outgoing_states else 0
			is_open = 1 if is_terminal == 0 else 0
			results.append(
				{
					"status": status,
					"label": status,
					"is_open": is_open,
					"is_terminal": is_terminal,
				}
			)
		return results

	# Fallback if no Workflow record is found
	fallback_statuses = [
		("Submitted", 1, 0),
		("Assigned", 1, 0),
		("In Progress", 1, 0),
		("More Info Needed", 1, 0),
		("Pending Submitter", 1, 0),
		("Resolved", 1, 0),
		("Closed", 0, 1),
		("Rejected", 0, 1),
	]
	return [
		{
			"status": st,
			"label": st,
			"is_open": op,
			"is_terminal": term,
		}
		for st, op, term in fallback_statuses
	]


def get_preferred_languages() -> list[dict]:
	"""Supported notification languages."""
	return [
		{"code": "am", "label": "Amharic"},
		{"code": "en", "label": "English"},
	]


def get_phone_extensions(search: str | None = None, country: str | None = None) -> list[dict]:
	"""Retrieve country phone extensions / ISD codes."""
	from frappe.geo.country_info import get_all

	all_geo_data = get_all()
	extensions = []
	for name, info in all_geo_data.items():
		isd = info.get("isd")
		if not isd:
			continue
		isd_str = str(isd).strip()
		if not isd_str.startswith("+"):
			isd_str = f"+{isd_str}"
		code = (info.get("code") or "").upper()
		extensions.append({"country": name, "code": code, "isd": isd_str})

	# Sort primary region (Ethiopia) first, then alphabetically
	extensions.sort(key=lambda x: (x["country"] != "Ethiopia", x["country"]))

	if country:
		c_term = country.strip().upper()
		extensions = [e for e in extensions if e["code"] == c_term or e["country"].upper() == c_term]

	if search:
		s_term = search.strip().lower()
		extensions = [
			e
			for e in extensions
			if s_term in e["country"].lower() or s_term in e["code"].lower() or s_term in e["isd"].lower()
		]

	return extensions


def clear_reference_cache(doc=None, method=None):
	"""Drop the cached lookups. Wired to master changes in hooks.

	Frappe calls doc_events handlers with (doc, method); neither is needed here
	because the whole prefix is dropped rather than one key.
	"""
	frappe.cache().delete_keys("grievance:")
