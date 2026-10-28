# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""The form as a file: UTF-8 CSV, or a PDF laid out like the paper Annexure."""

import csv
import html
import io
import re

from frappe import _

from oan_grievance_service.services import pdf_engine
from oan_grievance_service.services.ticket_form_blocks import _text, _ticked

# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

# A cell that opens with one of these is run as a formula by a spreadsheet. Complaint text
# is typed by the public, so the cell is defused rather than trusted. A plain number or phone
# number is left alone: it cannot carry a formula.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")
_NUMBERLIKE = re.compile(r"^[+-]?[\d\s().-]+$")


def _csv_safe(text):
	if text and text.startswith(_FORMULA_LEAD) and not _NUMBERLIKE.match(text):
		return "'" + text
	return text


def render_csv(form) -> bytes:
	"""The form as UTF-8 CSV with a byte-order mark, so a spreadsheet reads Amharic correctly.

	One fact per line: Section, Entry, Field, Value. A key/value row has no Entry; a row of
	boxes is one line whose value is the boxes ticked; a table row is named by its table and
	position ("Decisions 2"), one line per filled cell.
	"""
	out = io.StringIO(newline="")
	writer = csv.writer(out, lineterminator="\r\n")

	def put(section, entry, field, value):
		writer.writerow([_csv_safe(section), _csv_safe(entry), _csv_safe(field), _csv_safe(value)])

	writer.writerow([_("Section"), _("Entry"), _("Field"), _("Value")])
	put(form["title"], "", "", "")
	for label, value in form["header"]:
		put(_("Reference"), "", label, value)
	for section in form["sections"]:
		heading = f"{section['number']}. {section['title']}"
		for item in section["items"]:
			if item["kind"] == "field":
				put(heading, "", item["label"], item["value"])
			elif item["kind"] == "checks":
				put(
					heading, "", item["label"], ", ".join(_ticked(o) for o in item["options"] if o["checked"])
				)
		for table in section["tables"]:
			_put_table(put, heading, table)
		if section["note"]:
			put(heading, "", _("Note"), section["note"])
	if form["appendix"]["rows"]:
		_put_table(put, _("Appendix: Timeline"), form["appendix"])
	for label, value in form["footer"]:
		put(_("Export"), "", label, value)
	return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")


def _put_table(put, heading, table):
	for index, row in enumerate(table["rows"], start=1):
		for column, cell in zip(table["columns"], row, strict=True):
			if cell:
				put(heading, f"{table['title']} {index}", column, cell)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

BOX_EMPTY = "☐"
BOX_TICKED = "☒"

# What an ordinary field prints when the case has nothing for it. The CSV and the form data keep
# the value empty; only the PDF shows the dash, so a reader can tell "none" from a cell the
# renderer lost.
EMPTY_VALUE = "—"


def display_value(value):
	"""The text an ordinary PDF field shows: its value, or a dash when there is none.

	None, "" and whitespace are empty. 0 and False are values ("0", "No"), so they stay.
	"""
	text = _text(value)
	return text if text.strip() else EMPTY_VALUE


# Latin first, as the paper form is set; the Ethiopic faces follow so Amharic complaint text
# renders where one is installed (the server image has to carry Noto Sans Ethiopic for that).
PDF_STYLE = """
@page { size: A4; margin: 15mm 16mm 18mm;
	@bottom-right { content: "Page " counter(page) " of " counter(pages); font-size: 8pt; color: #555; } }
body { font-family: "Liberation Sans", Arial, "Noto Sans Ethiopic", "Abyssinica SIL", "Nyala",
	"DejaVu Sans", sans-serif; font-size: 9.5pt; color: #000; }
p.lh { text-align: center; font-weight: bold; text-transform: uppercase; margin: 0 0 7pt; font-size: 12pt; }
p.lh.first { font-size: 13pt; }
p.lh.last { font-size: 14pt; margin-bottom: 12pt; }
h1 { text-align: center; font-size: 13pt; text-transform: uppercase; margin: 4pt 0 10pt; }
table.meta { width: 100%; border-collapse: collapse; margin-bottom: 4pt; }
table.meta td { border: 1px solid #000; padding: 2pt 4pt; vertical-align: top; width: 31%;
	overflow-wrap: break-word; }
table.meta td.hl { width: 19%; font-weight: bold; background: #e3eef7; }
h2 { font-size: 10pt; text-transform: uppercase; margin: 10pt 0 4pt; page-break-after: avoid; }
h3 { font-size: 9pt; margin: 8pt 0 3pt; color: #222; page-break-after: avoid; }
p.line, p.text, p.opt { margin: 0 0 4.5pt; overflow-wrap: break-word; }
p.text { white-space: pre-wrap; }
p.opt { margin-bottom: 3.5pt; }
span.opt { margin-right: 12pt; white-space: nowrap; }
.blank { display: inline-block; min-width: 150pt; height: 10pt; border-bottom: 1px solid #000;
	vertical-align: bottom; }
table.grid { width: 100%; border-collapse: collapse; }
table.grid td, table.grid th { padding: 3pt 5pt; vertical-align: top; border: 1px solid #888; }
table.grid th { background: #eee; text-align: left; font-size: 8.5pt; }
table.grid td { white-space: pre-wrap; overflow-wrap: break-word; font-size: 8.5pt; }
table.grid tr { page-break-inside: avoid; }
.note { margin: 3pt 0; font-style: italic; color: #444; }
.appendix { page-break-before: always; }
.footer { margin-top: 14pt; font-size: 8pt; color: #444; }
"""


def render_pdf(form) -> bytes:
	"""The form as a PDF, laid out like the paper original: letterhead, reference block, numbered sections.

	The PDF is deliberately not digitally signed (decision recorded in docs/ticket-export.md):
	the SHA-256 in the export's audit row is its only integrity control.

	WeasyPrint is loaded and warmed by `pdf_engine`, once per worker, not per export.
	"""
	return pdf_engine.render(_pdf_html(form))


def _pdf_html(form):
	def esc(value):
		return html.escape(value or "")

	blank = '<span class="blank"></span>'

	def shown(item):
		"""A field's value as the PDF prints it: a dash if empty, a line to fill in by hand if the
		paper form leaves one (signature, stamp)."""
		if item["blank_line"] and not item["value"].strip():
			return blank
		return esc(display_value(item["value"]))

	def grid(table):
		head = "".join(f"<th>{esc(column)}</th>" for column in table["columns"])
		body = "".join(
			"<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>" for row in table["rows"]
		)
		return (
			f'<h3>{esc(table["title"])}</h3><table class="grid"><thead><tr>{head}</tr></thead>'
			f"<tbody>{body}</tbody></table>"
		)

	def option(o):
		box = BOX_TICKED if o["checked"] else BOX_EMPTY
		detail = ""
		if o["detail"] is not None:
			detail = f" {esc(o['detail'])}" if o["detail"] else f" {blank}"
		return f"{box} {esc(o['text'])}{':' if o['detail'] is not None else ''}{detail}"

	def render(item, counter):
		kind = item["kind"]
		if kind == "static":
			return f'<p class="text">{esc(item["text"])}</p>'
		if kind == "checks":
			label = f'{esc(item["label"])}:' if item["show_label"] else ""
			if item["stacked"]:
				head = f'<p class="line">{label}</p>' if label else ""
				return head + "".join(f'<p class="opt">{option(o)}</p>' for o in item["options"])
			boxes = " ".join(f'<span class="opt">{option(o)}</span>' for o in item["options"])
			return f'<p class="line">{label} {boxes}</p>'
		if not item["pdf"]:
			return ""
		if item["block"]:
			head = f'<p class="line">{esc(item["label"])}:</p>' if item["show_label"] else ""
			return head + f'<p class="text">{shown(item)}</p>'
		prefix = f"{counter}. " if item["numbered"] else ""
		return f'<p class="line">{prefix}{esc(item["label"])}: {shown(item)}</p>'

	lines = form["letterhead"]
	letterhead = "".join(
		f'<p class="lh{" first" if index == 0 else ""}{" last" if index == len(lines) - 1 else ""}">{esc(line)}</p>'
		for index, line in enumerate(lines)
	)

	# Two label/value pairs to a row. An odd last pair takes the whole row, so the table keeps
	# its right-hand border and the columns above stay aligned.
	header = form["header"]
	meta = ""
	for index in range(0, len(header), 2):
		pairs = header[index : index + 2]
		span = ' colspan="3"' if len(pairs) == 1 else ""
		meta += (
			"<tr>"
			+ "".join(
				f'<td class="hl">{esc(label)}</td><td{span}>{esc(display_value(value))}</td>'
				for label, value in pairs
			)
			+ "</tr>"
		)

	sections = ""
	for section in form["sections"]:
		sections += f"<h2>{section['number']}. {esc(section['title'])}</h2>"
		counter = 0
		for item in section["items"]:
			if item["kind"] == "field" and item["numbered"] and item["pdf"]:
				counter += 1
			sections += render(item, counter)
		sections += "".join(grid(table) for table in section["tables"])
		if section["note"]:
			sections += f'<p class="note">{esc(section["note"])}</p>'

	appendix = ""
	if form["appendix"]["rows"]:
		appendix = (
			f'<div class="appendix"><h2>{esc(_("Appendix: Timeline"))}</h2>{grid(form["appendix"])}</div>'
		)

	footer = " &middot; ".join(f"{esc(label)}: {esc(value)}" for label, value in form["footer"])
	return (
		f'<!doctype html><html lang="{esc(form.get("lang") or "en")}"><head><meta charset="utf-8">'
		f'<title>{esc(form["title"])}</title>'
		f"<style>{PDF_STYLE}</style></head><body>"
		f"{letterhead}<h1>{esc(form['title'])}</h1><table class=\"meta\">{meta}</table>{sections}{appendix}"
		f'<p class="footer">{footer}</p></body></html>'
	)
