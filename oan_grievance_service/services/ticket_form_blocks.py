# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""The pieces the form is made of: a labelled value, a row of boxes, a section, a table, a date.

Package-internal: `ticket_form`, `ticket_form_stages` and `ticket_render` share them, and the
render side reads the same dictionaries the form side writes.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from frappe import _
from frappe.utils import get_datetime, get_system_timezone

# --- building blocks -------------------------------------------------------


def _field(label, value, *, pdf=True, show_label=True, block=False, numbered=False):
	"""A labelled value. `pdf=False` keeps it to the CSV; `block` puts the value on lines of its own
	(ruled blanks when empty); `numbered` prints it in a numbered list."""
	return {
		"kind": "field",
		"label": label,
		"value": _text(value),
		"pdf": pdf,
		"show_label": show_label,
		"block": block,
		"numbered": numbered,
	}


def _checks(label, options, *, stacked=False, show_label=True):
	"""A row of boxes, each ticked or not. An option is (text, checked), or (text, checked, detail)
	where the detail is the free text that follows an "Other" box."""
	return {
		"kind": "checks",
		"label": label,
		"stacked": stacked,
		"show_label": show_label,
		"options": [
			{
				"text": option[0],
				"checked": bool(option[1]),
				"detail": option[2] if len(option) > 2 else None,
			}
			for option in options
		],
	}


def _static(text):
	"""Wording printed on the paper form itself; the PDF shows it and the CSV leaves it out."""
	return {"kind": "static", "text": text}


def _ticked(option):
	return f"{option['text']}: {option['detail']}" if option["detail"] else option["text"]


def _section(number, title, items=(), tables=(), note=None):
	items = list(items)
	return {
		"number": number,
		"title": title,
		"items": items,
		"rows": [(i["label"], i["value"]) for i in items if i["kind"] == "field"],
		"checks": [
			(i["label"], [_ticked(o) for o in i["options"] if o["checked"]])
			for i in items
			if i["kind"] == "checks"
		],
		"tables": [table for table in tables if table["rows"]],
		"note": note,
	}


def _table(title, columns, rows):
	return {"title": title, "columns": columns, "rows": [[_text(cell) for cell in row] for row in rows]}


def _in_order(events):
	"""Event tuples (when, *cells) as rows, oldest first; ties keep the order they were read in."""
	ordered = sorted(events, key=lambda event: get_datetime(event[0]) if event[0] else datetime.min)
	return [[_when(event[0]), *event[1:]] for event in ordered]


def _when(value):
	return get_datetime(value).strftime("%Y-%m-%d %H:%M") if value else ""


def _time_zone():
	"""The zone every date on the form is in. Frappe keeps datetimes as the site's local time."""
	name = get_system_timezone()
	try:
		offset = datetime.now(ZoneInfo(name)).strftime("%z")
	except Exception:
		return name
	return f"{name} (UTC{offset[:3]}:{offset[3:]})"


def _size(size_bytes):
	if not size_bytes:
		return ""
	return f"{size_bytes / 1024:.1f} KB" if size_bytes >= 1024 else f"{size_bytes} B"


def _text(value):
	if value is None:
		return ""
	if isinstance(value, bool):
		return _("Yes") if value else _("No")
	return str(value)
