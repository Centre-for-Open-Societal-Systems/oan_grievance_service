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


# KPI cards for the all-grievances queue. Draft is never a card. Workflow states
# that are not one of these cards roll up into a card so the queue does not grow
# a new status every time the workflow gains a stage.
#
# `workflow_states` are the engine states that count toward the card. `All` is
# every non-draft grievance. `default_terminal` is used only when none of the
# card's states exist on the active Workflow; otherwise terminal is read from
# that Workflow (docstatus 2, or no transition leaving the state).
_STATUS_CARDS = (
	{
		"status": "All",
		"label": "All",
		"order": 1,
		"workflow_states": (),
		"default_terminal": 0,
	},
	{
		"status": "In Progress",
		"label": "In Progress",
		"order": 2,
		"workflow_states": ("Submitted", "Assigned", "In Progress", "Pending Submitter"),
		"default_terminal": 0,
	},
	{
		"status": "Require More Info",
		"label": "Require More Info",
		"order": 3,
		"workflow_states": ("More Info Needed",),
		"default_terminal": 0,
	},
	{
		"status": "Rejected",
		"label": "Rejected",
		"order": 4,
		"workflow_states": ("Rejected",),
		"default_terminal": 1,
	},
	{
		"status": "Resolved",
		"label": "Resolved",
		"order": 5,
		"workflow_states": ("Resolved",),
		"default_terminal": 0,
	},
	{
		"status": "Closed",
		"label": "Closed",
		"order": 6,
		"workflow_states": ("Closed",),
		"default_terminal": 1,
	},
)

_CARD_BY_STATUS = {card["status"]: card for card in _STATUS_CARDS}

# Lower-cased, punctuation-stripped keys the list filter and the cards accept.
_STATUS_ALIASES = {
	"all": "All",
	"in progress": "In Progress",
	"require more info": "Require More Info",
	"more info needed": "Require More Info",
	"rejected": "Rejected",
	"resolved": "Resolved",
	"closed": "Closed",
}
for _card in _STATUS_CARDS:
	for _state in _card["workflow_states"]:
		_STATUS_ALIASES.setdefault(_state.lower(), _card["status"])


def _canonical_status(value: str) -> str | None:
	key = " ".join(str(value).strip().lower().replace("_", " ").replace("-", " ").split())
	return _STATUS_ALIASES.get(key)


def _workflow_terminals() -> dict[str, int] | None:
	"""Terminal flag per Workflow State, or None when Grievance has no active workflow.

	A state is terminal when Frappe marks it cancelled (docstatus 2) or when the
	workflow defines no transition out of it. Duplicate state rows (one per
	allow_edit role) collapse to a single flag.
	"""
	wf_name = frappe.db.get_value("Workflow", {"document_type": "Grievance", "is_active": 1}, "name")
	if not wf_name:
		return None

	rows = frappe.get_all(
		"Workflow Document State",
		filters={"parent": wf_name},
		fields=["state", "doc_status"],
		order_by="idx asc",
		ignore_permissions=True,
	)
	outgoing = set(
		frappe.get_all(
			"Workflow Transition",
			filters={"parent": wf_name},
			pluck="state",
			ignore_permissions=True,
		)
	)
	terminals: dict[str, int] = {}
	for row in rows:
		state = row.get("state")
		if not state or state in terminals or state == "Draft":
			continue
		doc_status = str(row.get("doc_status") or "")
		terminals[state] = 1 if doc_status == "2" or state not in outgoing else 0
	return terminals


def _is_terminal(workflow_states: tuple[str, ...], default: int, terminals: dict[str, int] | None) -> int:
	"""Workflow wins when the card's states are on it; otherwise the card default."""
	if terminals is None:
		return default
	present = [terminals[state] for state in workflow_states if state in terminals]
	if not present:
		return default
	return 1 if all(present) else 0


def _status_cards() -> list[dict]:
	"""The six queue statuses, in display order, with terminal taken from the workflow."""
	terminals = _workflow_terminals()
	cards = []
	for card in _STATUS_CARDS:
		is_terminal = _is_terminal(card["workflow_states"], card["default_terminal"], terminals)
		cards.append(
			{
				"status": card["status"],
				"label": card["label"],
				"order": card["order"],
				"is_terminal": is_terminal,
				"is_open": 0 if is_terminal else 1,
			}
		)
	return cards


def get_status_options() -> list[dict]:
	"""Queue statuses for filters. Draft and any other workflow stage are omitted."""
	return _status_cards()


def public_status(workflow_status: str | None) -> str:
	"""The queue status a workflow state is shown as. Unknown open states are In Progress."""
	if not workflow_status or workflow_status == "Draft":
		return workflow_status or ""
	canonical = _canonical_status(workflow_status)
	return canonical or "In Progress"


def expand_status_filter(values: list[str]) -> list[str] | None:
	"""Workflow states a queue-status filter matches.

	None means All: every non-draft grievance. An empty list means the caller
	passed nothing usable.
	"""
	if not values:
		return []

	selected: list[str] = []
	for raw in values:
		canonical = _canonical_status(str(raw))
		if canonical == "All":
			return None
		if canonical:
			selected.extend(_CARD_BY_STATUS[canonical]["workflow_states"])
			continue
		text = str(raw).strip()
		if text and text != "Draft":
			selected.append(text)

	seen: list[str] = []
	for state in selected:
		if state not in seen:
			seen.append(state)
	return seen


def _mapped_workflow_states() -> set[str]:
	states: set[str] = set()
	for card in _STATUS_CARDS:
		states.update(card["workflow_states"])
	return states


def get_status_summary() -> list[dict]:
	"""Counts for the KPI cards, limited to grievances the caller is allowed to see.

	Draft is excluded. A workflow state that is not one of the cards is counted
	under In Progress so every visible case still sits on exactly one card, and
	All equals the sum of the other cards.
	"""
	rows = frappe.get_list(
		"Grievance",
		filters=[["workflow_state", "!=", "Draft"], ["status", "!=", "Draft"]],
		fields=["status", {"COUNT": "*", "as": "total"}],
		group_by="status",
	)
	counts: dict[str, int] = {}
	for row in rows:
		status = row.get("status") or ""
		if not status or status == "Draft":
			continue
		counts[status] = counts.get(status, 0) + int(row.get("total") or 0)

	mapped = _mapped_workflow_states()
	unmapped = sum(total for status, total in counts.items() if status not in mapped)
	all_count = sum(counts.values())

	cards = []
	for card in _status_cards():
		spec = _CARD_BY_STATUS[card["status"]]
		if card["status"] == "All":
			count = all_count
		else:
			count = sum(counts.get(state, 0) for state in spec["workflow_states"])
			if card["status"] == "In Progress":
				count += unmapped
		card["count"] = count
		cards.append(card)
	return cards


def get_preferred_languages() -> list[dict]:
	"""Supported notification languages."""
	return [
		{"code": "am", "label": "Amharic"},
		{"code": "en", "label": "English"},
	]


def get_jurisdiction_countries() -> list[str]:
	"""Active country names from the Administrative Area tree (fallback: Ethiopia)."""
	country_nodes = frappe.get_all(
		"Grievance Administrative Area",
		filters={"level_name": "Country", "is_active": 1},
		fields=["area_name", "code", "country"],
		order_by="area_name asc",
		ignore_permissions=True,
	)

	names: set[str] = set()
	for node in country_nodes:
		if node.get("country"):
			names.add(node["country"])
		if node.get("area_name"):
			names.add(node["area_name"])

	if not names:
		distinct_countries = frappe.get_all(
			"Grievance Administrative Area",
			filters={"is_active": 1, "country": ["is", "set"]},
			distinct=True,
			pluck="country",
			ignore_permissions=True,
		)
		names = {c.strip() for c in distinct_countries if c and c.strip()}

	return sorted(names) if names else ["Ethiopia"]


def get_phone_extensions(search: str | None = None, country: str | None = None) -> list[dict]:
	"""Phone extensions for active jurisdiction countries only (Administrative Area)."""
	from frappe.geo.country_info import get_all

	all_geo_data = get_all()
	jurisdiction_names = get_jurisdiction_countries()

	extensions = []
	for j_name in jurisdiction_names:
		info = all_geo_data.get(j_name)
		if not info:
			for c_name, c_info in all_geo_data.items():
				if c_name.lower() == j_name.lower() or (c_info.get("code") or "").lower() == j_name.lower():
					info = c_info
					j_name = c_name
					break

		if info and info.get("isd"):
			isd_str = str(info["isd"]).strip()
			if not isd_str.startswith("+"):
				isd_str = f"+{isd_str}"
			extensions.append(
				{
					"country": j_name,
					"code": (info.get("code") or "").upper(),
					"isd": isd_str,
				}
			)

	if not extensions:
		extensions = [{"country": "Ethiopia", "code": "ET", "isd": "+251"}]

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
