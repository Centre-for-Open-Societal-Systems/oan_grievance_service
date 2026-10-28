# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""STG-469: a grievance and its timeline exported as the SOW Annexure complaint form.

The form is built once from the case and rendered twice, so most of these tests read the
built form; the CSV and PDF tests check only what rendering itself can get wrong (formula
cells, markup, a file that is not a PDF). The endpoint tests cover who may export and what
the audit trail records for each attempt.
"""

import csv
import hashlib
import io
import unittest
from datetime import datetime, timedelta
from unittest import mock

import frappe
import frappe.api
from frappe.tests.utils import FrappeTestCase
from frappe.translate import print_language
from oan_auth_service.api.middleware import validate_jwt_request
from pypdf import PdfReader

from oan_grievance_service.api.router import ensure_routes_registered
from oan_grievance_service.api.v1 import grievance
from oan_grievance_service.grievance_management.doctype.grievance_timeline.grievance_timeline import (
	GrievanceTimeline,
)
from oan_grievance_service.services import (
	audit,
	response_body,
	ticket_export,
	ticket_form,
	ticket_form_blocks,
	ticket_render,
)
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import ticket_number as tn
from oan_grievance_service.tests.fixtures import a_department, a_grievance, discard_grievance
from oan_grievance_service.tests.test_category_assignment import _keep_transaction, _user
from oan_grievance_service.tests.test_router import make_test_request

# Fixed, so a date in the form can be written out in full below.
BASE = datetime(2026, 9, 1, 9, 0)


def at(days, hours=0):
	return BASE + timedelta(days=days, hours=hours)


def stamp(days, hours=0):
	return at(days, hours).strftime("%Y-%m-%d %H:%M")


def _has_ethiopic(text):
	"""Whether any character is in the Ethiopic block (U+1200 to U+137F)."""
	return any(chr(0x1200) <= char <= chr(0x137F) for char in text)


def _has_ethiopic_font():
	import subprocess

	try:
		return bool(
			subprocess.run(["fc-list", ":lang=am", "family"], capture_output=True, text=True).stdout.strip()
		)
	except OSError:
		return False


def sections_of(form):
	return {section["number"]: section for section in form["sections"]}


def rows_of(section):
	return dict(section["rows"])


def table_of(section, title):
	return next(table for table in section["tables"] if table["title"] == title)


class TicketExportTestCase(FrappeTestCase):
	"""A closed, twice-reviewed case with every stage of the workflow on record."""

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.cache.delete_keys("grievance:export-denials:*")
		suffix = frappe.generate_hash(length=6)
		self.officer = _user(f"export-officer-{suffix}@example.com", "Dawit Officer")
		self.submitter = _user(
			f"export-submitter-{suffix}@example.com", "Almaz Bekele", role=C.ROLE_SUBMITTER
		)
		self.addCleanup(self._remove_users)
		self.addCleanup(frappe.local.response.pop, "type", None)
		self.case = self._full_case()
		self.addCleanup(discard_grievance, self.case.name)

	def _remove_users(self):
		frappe.set_user("Administrator")
		for user in (self.officer, self.submitter):
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)

	# --- fixtures ----------------------------------------------------------

	def _history(
		self, case, days, action, from_status, to_status, user=None, reason=None, notes=None, auto=0
	):
		return frappe.get_doc(
			{
				"doctype": "Grievance Status History",
				"grievance": case.name,
				"from_status": from_status,
				"to_status": to_status,
				"action": action,
				"is_automated": auto,
				"changed_by": user,
				"timestamp": at(days),
				"reason": reason,
				"notes": notes,
			}
		).insert(ignore_permissions=True)

	def _note(self, case, entry_type, body, days, user=None, internal=0, author_type=None):
		return GrievanceTimeline.record(
			grievance=case.name,
			entry_type=entry_type,
			body=body,
			is_internal=internal,
			author_user=user,
			author_type=author_type,
			created_on=at(days),
		)

	def _full_case(self):
		case = a_grievance(
			submitter_name="Almaz Bekele",
			contact_mobile="+251911234567",
			contact_email="almaz@example.com",
			description="The recorded plot size differs from my landholding certificate.",
			desired_outcome="Correct the official land record.",
			assigned_dept=a_department(),
			assigned_to=self.officer,
		)
		frappe.db.set_value("Grievance", case.name, "owner", self.submitter, update_modified=False)

		# Inserted out of order on purpose: the form orders by time, not by insertion.
		self._history(
			case, 7, "Close Case", "Resolved", "Closed", self.submitter, reason="Accepted the outcome."
		)
		self._history(case, 2, "In Progress", "Assigned", "In Progress", self.officer)
		self._history(case, 1, "Assign", "Submitted", "Assigned", auto=1)
		self._history(
			case,
			4,
			"Resolve",
			"In Progress",
			"Resolved",
			self.officer,
			reason=response_body.compose("Re-measured the plot.", "The record was corrected."),
			notes="Checked with the woreda office.",
		)
		self._history(
			case, 5, "Reopen", "Resolved", "In Progress", self.submitter, reason="Boundary still wrong."
		)
		self._history(
			case,
			6,
			"Partially Resolve",
			"In Progress",
			"Resolved",
			self.officer,
			reason="Boundary rechecked.",
		)

		self._note(case, "submission", case.description, 0, self.submitter)
		self._note(case, "note", "Site visit planned for Thursday.", 3, self.officer, internal=1)
		self._note(case, "response", "We re-measured the plot.", 4, self.officer, author_type="officer")
		self._note(case, "message", "Thank you, but the boundary is wrong.", 4.5, self.submitter)
		self._note(case, "escalation", "Escalated after the SLA passed.", 5.5, self.officer)

		frappe.get_doc(
			{
				"doctype": "Grievance Attachment",
				"grievance": case.name,
				"file_name": "land-certificate.pdf",
				"file_url": "/private/files/land-certificate.pdf",
				"document_type": "Land title",
				"mime_type": "application/pdf",
				"size_bytes": 2048,
				"scan_status": "Clean",
				"uploaded_by_user": self.officer,
			}
		).insert(ignore_permissions=True)

		frappe.db.set_value(
			"Grievance",
			case.name,
			{
				"status": C.STATE_CLOSED,
				"workflow_state": C.STATE_CLOSED,
				"reopen_count": 1,
				"escalated": 1,
				"escalated_at": at(5, 12),
				"resolved_at": at(6),
				"sla_due_date": at(9),
			},
			update_modified=False,
		)
		frappe.get_doc(
			{
				"doctype": "Grievance Feedback",
				"grievance": case.name,
				"rating": 4,
				"feedback_type": "Resolution",
				"submitted_at": at(7, 1),
				"submitted_by": self.submitter,
				"comments": "Fixed in the end.",
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Grievance Notification Log",
				"grievance": case.name,
				"event": "grievance_closed",
				"recipient": "+251911234567",
				"channel": "SMS",
				"status": "Sent",
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Grievance Notification Log",
				"grievance": case.name,
				"event": "grievance_closed",
				"recipient": "almaz@example.com",
				"channel": "Email",
				"status": "Failed",
			}
		).insert(ignore_permissions=True)
		return frappe.get_doc("Grievance", case.name)

	def _form(self, user="Administrator", case=None):
		frappe.set_user(user)
		return ticket_export.build_form(frappe.get_doc("Grievance", (case or self.case).name))

	def _export(self, ticket=None, **kwargs):
		"""Call the endpoint; its Response on success, the error envelope otherwise."""
		return grievance.export_form(ticket_number=ticket or self.case.ticket_number, **kwargs)

	def _events(self, **filters):
		return frappe.get_all(
			"Grievance Access Audit Event",
			filters=filters,
			fields=["user", "role", "grievance", "action", "decision", "scope_evaluated", "source"],
			order_by="creation desc",
		)


class TestFormMapping(TicketExportTestCase):
	def test_header_block(self):
		header = dict(self._form()["header"])
		self.assertEqual(header["Grievance Reference No."], tn.display(self.case.ticket_number))
		self.assertEqual(header["Grievance Category"], self.case.service_category)
		self.assertEqual(header["Grievance Status"], "Closed")
		self.assertEqual(header["Receiving Office"], self.case.assigned_dept)
		self.assertEqual(header["Date Submitted"], ticket_form_blocks._when(self.case.creation))
		# The system has no priority; the field stays blank rather than take a stand-in.
		self.assertEqual(header["Priority"], "")
		self.assertEqual(header["Document Type"], "Grievance Record")

	def test_letterhead_names_the_region_and_the_filing_office_from_the_case(self):
		from oan_grievance_service.services.administrative_area import get_administrative_hierarchy

		hierarchy = get_administrative_hierarchy(self.case.administrative_area)
		letterhead = self._form()["letterhead"]
		self.assertEqual(letterhead[0], "Federal Democratic Republic of Ethiopia")
		self.assertIn(f"{hierarchy['region']} Administration", letterhead)
		self.assertIn(f"{hierarchy.get('woreda') or hierarchy['zone']} Administration", letterhead)

	def test_complainant_description_and_requested_action(self):
		sections = sections_of(self._form())
		complainant = rows_of(sections[1])
		self.assertEqual(complainant["Full Name"], "Almaz Bekele")
		self.assertEqual(complainant["Telephone"], "+251911234567")
		self.assertEqual(complainant["Email"], "almaz@example.com")
		self.assertEqual(complainant["Occupation"], "")
		self.assertEqual(
			rows_of(sections[2])["Subject"],
			frappe.db.get_value("Grievance Type", self.case.grievance_type, "type_name"),
		)
		self.assertEqual(rows_of(sections[3])["Description"], self.case.description)
		self.assertEqual(rows_of(sections[6])["Requested Action"], "Correct the official land record.")

	def test_all_twelve_sections_in_order(self):
		form = self._form()
		self.assertEqual([s["number"] for s in form["sections"]], list(range(1, 13)))

	def test_documents_are_listed_without_the_file_location(self):
		documents = sections_of(self._form())[5]
		self.assertEqual(rows_of(documents)["Documents Attached"], "1")
		row = table_of(documents, "Attached Files")["rows"][0]
		self.assertEqual(row[1:5], ["land-certificate.pdf", "Land title", "application/pdf", "2.0 KB"])
		self.assertEqual(row[6], "Clean")
		self.assertNotIn("/private/files", repr(documents))

	def test_the_documents_checklist_ticks_only_what_an_attachment_names(self):
		# "Land title" is not one of the form's items, so the one fixture file ticks Other.
		self.assertEqual(
			dict(sections_of(self._form())[5]["checks"])["Supporting Documents"],
			["Other Supporting Documents"],
		)

		for file_name, kind in (
			("id.pdf", "copy of identification document"),
			("deed.pdf", "Land Registration Document"),
		):
			frappe.get_doc(
				{
					"doctype": "Grievance Attachment",
					"grievance": self.case.name,
					"file_name": file_name,
					"file_url": f"/private/files/{file_name}",
					"document_type": kind,
					"mime_type": "application/pdf",
					"size_bytes": 10,
					"uploaded_by_user": self.officer,
				}
			).insert(ignore_permissions=True)
		options = {
			o["text"]: o["checked"]
			for o in next(i for i in sections_of(self._form())[5]["items"] if i["kind"] == "checks")[
				"options"
			]
		}
		self.assertEqual(
			options,
			{
				"Copy of Identification Document": True,
				"Previous Landholding Certificate": False,
				"Land Registration Document": True,
				"Site / Location Sketch": False,
				"Previous Correspondence": False,
				"Other Supporting Documents": True,
			},
		)

	def test_assignment_section(self):
		assignment = sections_of(self._form())[7]
		rows = rows_of(assignment)
		self.assertEqual(rows["Date"], stamp(1))
		self.assertEqual(rows["Assigned Department"], self.case.assigned_dept)
		self.assertEqual(rows["Assigned Officer"], "Dawit Officer")
		self.assertEqual(rows["Target Response Date"], stamp(9))
		history = table_of(assignment, "Assignment History")["rows"]
		self.assertEqual(history, [[stamp(1), "Assigned", "", "System"]])

	def test_a_reassignment_is_part_of_the_assignment_history_for_staff(self):
		other = _user(f"export-other-{frappe.generate_hash(length=6)}@example.com", "Hana Officer")
		self.addCleanup(frappe.delete_doc, "User", other, force=True, ignore_permissions=True)
		# A request can only be raised on an open case; this one was reassigned before it closed.
		state = {"status": C.STATE_IN_PROGRESS, "workflow_state": C.STATE_IN_PROGRESS}
		frappe.db.set_value("Grievance", self.case.name, state, update_modified=False)
		request = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.case.name,
				"subject": "Move to Hana",
				"status": "Approved",
				"requested_by": self.officer,
				"decided_by": "Administrator",
				"decided_at": at(3),
				"changes": [{"fieldname": "assigned_to", "old_value": self.officer, "new_value": other}],
			}
		).insert(ignore_permissions=True)
		# The controller stamps the decision time on insert; the form must show when it was decided.
		frappe.db.set_value(
			"Grievance Change Request", request.name, "decided_at", at(3), update_modified=False
		)
		closed = {"status": C.STATE_CLOSED, "workflow_state": C.STATE_CLOSED}
		frappe.db.set_value("Grievance", self.case.name, closed, update_modified=False)

		rows = table_of(sections_of(self._form())[7], "Assignment History")["rows"]
		self.assertEqual([r[1] for r in rows], ["Assigned", "Reassignment approved"])
		self.assertEqual(rows[1][0], stamp(3))
		self.assertIn("Officer: Dawit Officer -> Hana Officer", rows[1][2])

		# A submitter is not told who handled the case.
		rows = table_of(sections_of(self._form(self.submitter))[7], "Assignment History")["rows"]
		self.assertEqual([r[1] for r in rows], ["Assigned"])

	def test_investigation_section_orders_events_and_holds_internal_notes_for_staff(self):
		investigation = sections_of(self._form())[8]
		rows = rows_of(investigation)
		self.assertEqual(rows["Investigation Date"], stamp(2))
		# FSD D-1: the resolution summary holds the findings. The latest decision here is a plain
		# one with no summary, so the findings come from the response before it.
		self.assertEqual(rows["Findings"], "The record was corrected.")
		events = table_of(investigation, "Investigation Events")["rows"]
		self.assertEqual(
			[(e[0], e[1], e[3]) for e in events],
			[(stamp(2), "Work started", "Dawit Officer"), (stamp(3), "Internal note", "Dawit Officer")],
		)
		self.assertEqual(events[1][2], "Site visit planned for Thursday.")

	def test_decision_section_has_every_decision_in_order_with_its_review_round(self):
		decision = sections_of(self._form())[9]
		self.assertEqual(rows_of(decision)["Decisions Recorded"], "2")
		# The form's own lines carry the latest decision; the table below keeps them all.
		latest = rows_of(decision)
		self.assertEqual((latest["Decision Date"], latest["Outcome"]), (stamp(6), "Partially Resolve"))
		self.assertEqual(latest["Determination"], "Boundary rechecked.")
		# That one has no action taken, so the line is blank rather than borrowed from the first.
		self.assertEqual(latest["Action Taken"], "")
		rows = table_of(decision, "Decisions")["rows"]
		self.assertEqual(
			rows,
			[
				[
					"1",
					stamp(4),
					"Resolve",
					"Resolved",
					"Dawit Officer",
					"Re-measured the plot.",
					"The record was corrected.",
				],
				["2", stamp(6), "Partially Resolve", "Resolved", "Dawit Officer", "", "Boundary rechecked."],
			],
		)

	def test_response_section_lists_the_conversation_oldest_first(self):
		response = sections_of(self._form())[10]
		rows = rows_of(response)
		self.assertEqual(rows["Response Date"], stamp(4))
		self.assertEqual(rows["Responses Provided"], "1")
		# Only the SMS that went out; the failed email is not a way the complainant was reached.
		self.assertEqual(rows["Notified By"], "SMS")
		self.assertEqual(dict(response["checks"])["Communication Method"], ["SMS"])
		conversation = table_of(response, "Communication")["rows"]
		self.assertEqual(
			[(r[0], r[1], r[2], r[3]) for r in conversation],
			[
				(stamp(4), "1", "Department response", "Department"),
				# A complainant's message is not a department response and takes no number.
				(stamp(4, 12), "", "Message", "Complainant"),
			],
		)
		# The note is internal and the escalation is not a message.
		self.assertNotIn("Site visit", repr(conversation))

	def test_appeal_section(self):
		appeal = sections_of(self._form())[11]
		rows = rows_of(appeal)
		self.assertEqual(rows["Reopen Count"], "1")
		self.assertEqual(rows["Escalated"], "Yes")
		self.assertEqual(rows["Escalated At"], stamp(5, 12))
		self.assertEqual(rows["Reason for Further Action"], "Boundary still wrong.")
		self.assertEqual(rows["Satisfaction Rating"], "4/5")
		# The case was reopened, which is the complainant asking for further action.
		self.assertEqual(dict(appeal["checks"])["Complainant Satisfaction"], ["Further Action Requested"])
		events = table_of(appeal, "Appeal Events")["rows"]
		self.assertEqual(
			[(e[0], e[1], e[2]) for e in events],
			[
				(stamp(5), "Reopened", "Boundary still wrong."),
				(stamp(5, 12), "Escalated", "Escalated after the SLA passed."),
			],
		)
		self.assertEqual(
			table_of(appeal, "Satisfaction Feedback")["rows"][0][2:], ["4/5", "Fixed in the end."]
		)
		self.assertIsNone(appeal["note"])

	def test_closure_section(self):
		closure = sections_of(self._form())[12]
		rows = rows_of(closure)
		self.assertEqual(rows["Current Status"], "Closed")
		# Closed, and still flagged as escalated; not referred, and not open again.
		self.assertEqual(dict(closure["checks"])["Final Status"], ["Closed", "Escalated"])
		self.assertEqual(rows["Closure Date"], stamp(7))
		self.assertEqual(rows["Closure Remarks"], "Accepted the outcome.")
		self.assertEqual(rows["Closed By"], "Complainant")
		self.assertEqual(rows["Resolved At"], stamp(6))
		self.assertEqual(rows["Responsible Officer"], "Dawit Officer")
		# Signed and stamped on paper; the system holds neither.
		self.assertEqual((rows["Signature"], rows["Official Stamp"]), ("", ""))
		history = table_of(closure, "Closure History")["rows"]
		self.assertEqual(
			history, [[stamp(7), "Close Case", "Closed", "Accepted the outcome.", "Complainant"]]
		)

	def test_appendix_is_the_whole_timeline_oldest_first(self):
		appendix = self._form()["appendix"]
		dates = [row[1] for row in appendix["rows"]]
		self.assertEqual(dates, sorted(dates))
		types = [row[2] for row in appendix["rows"]]
		self.assertEqual(types[:3], ["submission", "note", "response"])
		self.assertIn("Visibility", appendix["columns"])


class TestMissingStages(TicketExportTestCase):
	def test_a_new_case_leaves_every_later_stage_empty_and_says_so(self):
		fresh = a_grievance(description="Nothing has happened to this grievance yet, apart from filing.")
		self.addCleanup(discard_grievance, fresh.name)
		sections = sections_of(self._form(case=fresh))

		self.assertEqual(rows_of(sections[7])["Date"], "")
		self.assertEqual(rows_of(sections[8])["Investigation Date"], "")
		self.assertEqual(rows_of(sections[8])["Findings"], "")
		self.assertEqual(rows_of(sections[9])["Decisions Recorded"], "0")
		self.assertEqual(rows_of(sections[10])["Response Date"], "")
		self.assertEqual(rows_of(sections[12])["Closure Date"], "")
		for number in (7, 8, 9, 10, 11, 12):
			self.assertTrue(sections[number]["note"], msg=f"section {number}")
			self.assertEqual(sections[number]["tables"], [], msg=f"section {number}")
		self.assertEqual(rows_of(sections[5])["Documents Attached"], "0")
		self.assertEqual(rows_of(sections[11])["Reopen Count"], "0")
		self.assertEqual(rows_of(sections[11])["Escalated"], "No")

	def test_a_rejection_reason_is_not_a_finding(self):
		rejected = a_grievance(description="This grievance is not one the platform handles, at all.")
		self.addCleanup(discard_grievance, rejected.name)
		self._history(rejected, 1, "Reject", "Submitted", "Rejected", self.officer, reason="Out of scope.")
		sections = sections_of(self._form(case=rejected))
		self.assertEqual(rows_of(sections[8])["Findings"], "")
		# It is a decision, and it closes the case.
		self.assertEqual(table_of(sections[9], "Decisions")["rows"][0][2], "Reject")
		self.assertEqual(rows_of(sections[12])["Closure Remarks"], "Out of scope.")

	def test_a_new_case_still_renders_in_both_formats(self):
		fresh = a_grievance(description="Nothing has happened to this grievance yet, apart from filing.")
		self.addCleanup(discard_grievance, fresh.name)
		form = self._form(case=fresh)
		self.assertTrue(ticket_export.render_csv(form).startswith(b"\xef\xbb\xbf"))
		self.assertTrue(ticket_export.render_pdf(form).startswith(b"%PDF"))


class TestVisibility(TicketExportTestCase):
	def test_a_submitter_gets_the_public_record_only(self):
		form = self._form(self.submitter)
		sections = sections_of(form)
		everything = repr(form)

		self.assertNotIn("Site visit planned", everything)
		self.assertNotIn("Checked with the woreda office", everything)
		self.assertNotIn("Dawit Officer", everything)
		self.assertNotIn("Visibility", form["appendix"]["columns"])
		self.assertEqual(rows_of(sections[7])["Assigned Officer"], "")
		self.assertNotIn("Routed Automatically", rows_of(sections[7]))
		self.assertNotIn("Notified By", rows_of(sections[10]))
		self.assertEqual(rows_of(sections[12])["Responsible Officer"], "")
		self.assertEqual(table_of(sections[8], "Investigation Events")["rows"][0][3], "Officer")
		self.assertEqual(rows_of(sections[1])["Full Name"], "Almaz Bekele")

	def test_an_anonymous_submitters_identity_stays_sealed_for_an_officer(self):
		frappe.db.set_value("Grievance", self.case.name, "is_anonymous", 1, update_modified=False)
		complainant = rows_of(sections_of(self._form(self.officer))[1])
		self.assertEqual(complainant["Full Name"], "Anonymous Submitter")
		self.assertEqual(complainant["Telephone"], "")
		self.assertEqual(complainant["Email"], "")
		self.assertNotIn("Almaz", repr(self._form(self.officer)))
		self.assertNotIn("+251911234567", repr(self._form(self.officer)))

	def test_the_exporter_is_named_for_staff_and_by_role_for_a_submitter(self):
		self.assertEqual(dict(self._form(self.officer)["footer"])["Exported By"], "Dawit Officer")
		self.assertEqual(dict(self._form(self.submitter)["footer"])["Exported By"], "Complainant")


class TestRendering(TicketExportTestCase):
	def _csv_rows(self, form):
		content = ticket_export.render_csv(form)
		self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
		return list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))

	def test_csv_is_one_fact_per_line_in_form_order(self):
		rows = self._csv_rows(self._form())
		self.assertEqual(rows[0], ["Section", "Entry", "Field", "Value"])
		self.assertTrue(all(len(row) == 4 for row in rows))
		headings = [row[0] for row in rows if row[0].split(".")[0].isdigit()]
		seen = list(dict.fromkeys(headings))
		self.assertEqual([h.split(".")[0] for h in seen], [str(n) for n in range(1, 13)])
		self.assertIn(["9. Decision / Resolution", "Decisions 2", "Review Round", "2"], rows)
		self.assertIn(["1. Complainant Information", "", "Full Name", "Almaz Bekele"], rows)

	def test_csv_defuses_cells_a_spreadsheet_would_run_as_formulas(self):
		frappe.db.set_value(
			"Grievance",
			self.case.name,
			"desired_outcome",
			'=HYPERLINK("http://evil.example","click")',
			update_modified=False,
		)
		rows = self._csv_rows(self._form())
		values = {row[2]: row[3] for row in rows if row[0].startswith("6.")}
		self.assertEqual(values["Requested Action"], '\'=HYPERLINK("http://evil.example","click")')
		# A phone number begins with a plus and is not a formula; it must survive untouched.
		self.assertIn(["1. Complainant Information", "", "Telephone", "+251911234567"], rows)

	def test_pdf_is_a_pdf_holding_the_case(self):
		pdf = ticket_export.render_pdf(self._form())
		self.assertTrue(pdf.startswith(b"%PDF"))
		text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
		self.assertIn(tn.display(self.case.ticket_number), text)
		self.assertIn("Complainant Information".upper(), text.upper())
		self.assertIn("Almaz Bekele", text)
		# Laid out as the paper form: its letterhead, and ticked and empty boxes in the checklists.
		self.assertIn("FEDERAL DEMOCRATIC REPUBLIC OF ETHIOPIA", text.upper())
		self.assertIn(ticket_render.BOX_TICKED, text)
		self.assertIn(ticket_render.BOX_EMPTY, text)

	def test_pdf_prints_the_forms_own_wording_and_ruled_blanks(self):
		fresh = a_grievance(description="Nothing has happened to this grievance yet, apart from filing.")
		self.addCleanup(discard_grievance, fresh.name)
		page = ticket_render._pdf_html(self._form(case=fresh))
		for wording in (
			"Following review and verification, the responsible office determined that:",
			"The complainant was informed of the decision and actions taken by the responsible office.",
			"If the complainant is not satisfied with the decision",
		):
			self.assertIn(wording, page)
		# Nothing was found or decided, so those spaces are ruled lines to write on.
		self.assertGreaterEqual(page.count('<div class="rule"></div>'), 9)
		self.assertNotIn(ticket_render.BOX_TICKED, page)
		# Nothing is shown for a field that is only in the CSV.
		self.assertNotIn("Documents Attached", page)
		self.assertNotIn("Decisions Recorded", page)

	def test_csv_has_the_boxes_ticked_as_one_line(self):
		rows = self._csv_rows(self._form())
		self.assertIn(["12. Closure", "", "Final Status", "Closed, Escalated"], rows)
		self.assertIn(["10. Communication to the Complainant", "", "Communication Method", "SMS"], rows)
		# Pre-printed wording is the form's, not the case's.
		self.assertFalse([r for r in rows if "Following review and verification" in r[3]])

	def test_pdf_html_escapes_complaint_text(self):
		frappe.db.set_value(
			"Grievance",
			self.case.name,
			"description",
			'<script>alert(1)</script><img src="http://evil.example/x.png">',
			update_modified=False,
		)
		page = ticket_render._pdf_html(self._form())
		self.assertNotIn("<script>", page)
		self.assertNotIn("<img", page)
		self.assertIn("&lt;script&gt;", page)

	def test_pdf_refuses_to_fetch_anything(self):
		with mock.patch("weasyprint.HTML") as html_class:
			html_class.return_value.write_pdf.return_value = b"%PDF-"
			ticket_export.render_pdf(self._form())
		fetcher = html_class.call_args.kwargs["url_fetcher"]
		with self.assertRaises(ValueError):
			fetcher("http://169.254.169.254/latest/meta-data")


class TestExportEndpoint(TicketExportTestCase):
	def setUp(self):
		super().setUp()
		# Audit rows are committed so a refusal survives the API's rollback; keep the test's
		# own transaction.
		patcher = mock.patch.object(frappe.db, "commit")
		self.commit = patcher.start()
		self.addCleanup(patcher.stop)

	def test_staff_download_a_pdf_by_default_and_the_export_is_audited(self):
		frappe.set_user(self.officer)
		response = self._export()
		self.assertEqual(response.mimetype, "application/pdf")
		self.assertTrue(response.data.startswith(b"%PDF"))
		self.assertIn("attachment", response.headers["Content-Disposition"])
		self.assertEqual(frappe.response["type"], "download")

		event = self._events(grievance=self.case.name, action="export")[0]
		self.assertEqual(
			(event.user, event.decision, event.role), (self.officer, "Allowed", "Grievance Officer")
		)
		self.assertEqual(
			event.scope_evaluated,
			f"format=pdf; lang=en; outcome=success; bytes={len(response.data)}; "
			f"sha256={hashlib.sha256(response.data).hexdigest()}",
		)
		self.commit.assert_called()

	def test_csv_format_and_ticket_as_typed(self):
		frappe.set_user("Administrator")
		typed = tn.display(self.case.ticket_number).lower()
		response = self._export(ticket=typed, format="CSV")
		self.assertEqual(response.mimetype, "text/csv")
		self.assertIn(f"grievance-{self.case.ticket_number}.csv", response.headers["Content-Disposition"])
		self.assertIn("Almaz Bekele", response.data.decode("utf-8-sig"))

	def test_a_submitter_exports_their_own_case(self):
		frappe.set_user(self.submitter)
		response = self._export(format="csv")
		text = response.data.decode("utf-8-sig")
		self.assertIn("Almaz Bekele", text)
		self.assertNotIn("Site visit planned", text)

	def test_an_amharic_pdf_is_named_for_its_language_and_audited_with_it(self):
		frappe.set_user(self.officer)
		response = self._export(lang="AM")
		self.assertTrue(response.data.startswith(b"%PDF"))
		self.assertIn(f"grievance-{self.case.ticket_number}-am.pdf", response.headers["Content-Disposition"])
		event = self._events(grievance=self.case.name, action="export")[0]
		self.assertTrue(event.scope_evaluated.startswith("format=pdf; lang=am; outcome=success; bytes="))
		self.assertTrue(event.scope_evaluated.endswith(hashlib.sha256(response.data).hexdigest()))

	def test_an_unsupported_language_is_a_validation_error_and_nothing_is_exported(self):
		before = len(self._events(grievance=self.case.name))
		result = self._export(lang="fr")
		self.assertEqual(result["code"], "VALIDATION_ERROR")
		self.assertIn("lang", result["details"])
		self.assertEqual(len(self._events(grievance=self.case.name)), before)

	def test_the_request_language_is_back_after_an_export(self):
		frappe.set_user("Administrator")
		frappe.local.lang = "en"
		self._export(format="csv", lang="am")
		self.assertEqual(frappe.local.lang, "en")
		with (
			_keep_transaction(),
			mock.patch.object(ticket_export, "render_csv", side_effect=RuntimeError("boom")),
		):
			self._export(format="csv", lang="am")
		self.assertEqual(frappe.local.lang, "en")

	def test_an_unknown_format_is_a_validation_error_and_nothing_is_exported(self):
		before = len(self._events(grievance=self.case.name))
		result = self._export(format="docx")
		self.assertEqual(result["code"], "VALIDATION_ERROR")
		self.assertEqual(len(self._events(grievance=self.case.name)), before)

	def test_a_role_without_export_is_refused_and_the_attempt_is_audited(self):
		reviewer = _user(
			f"export-review-{frappe.generate_hash(length=6)}@example.com",
			"Rahel Review",
			role=C.ROLE_REVIEW_OFFICER,
		)
		self.addCleanup(frappe.delete_doc, "User", reviewer, force=True, ignore_permissions=True)
		frappe.set_user(reviewer)
		with _keep_transaction():
			result = self._export(format="csv")
		self.assertEqual(result["code"], "PERMISSION_DENIED")
		event = self._events(grievance=self.case.name, action="export")[0]
		self.assertEqual((event.user, event.decision), (reviewer, "Denied"))
		self.assertEqual(event.scope_evaluated, "format=csv; lang=en; reason=role")
		self.commit.assert_called()

	def test_a_role_without_export_learns_nothing_about_whether_the_case_exists(self):
		reviewer = _user(
			f"export-review-{frappe.generate_hash(length=6)}@example.com",
			"Rahel Review",
			role=C.ROLE_REVIEW_OFFICER,
		)
		self.addCleanup(frappe.delete_doc, "User", reviewer, force=True, ignore_permissions=True)
		frappe.set_user(reviewer)
		with _keep_transaction():
			result = self._export(ticket="no-such-ticket")
		self.assertEqual(result["code"], "PERMISSION_DENIED")

	def test_an_officer_with_no_access_to_the_case_is_refused_and_audited(self):
		stranger = _user(f"export-stranger-{frappe.generate_hash(length=6)}@example.com", "Sol Stranger")
		self.addCleanup(frappe.delete_doc, "User", stranger, force=True, ignore_permissions=True)
		frappe.set_user(stranger)
		with _keep_transaction():
			result = self._export()
		self.assertEqual(result["code"], "NOT_FOUND")
		event = self._events(grievance=self.case.name, action="export")[0]
		self.assertEqual((event.user, event.decision), (stranger, "Denied"))
		self.assertEqual(event.scope_evaluated, "format=pdf; lang=en; reason=case_access")

	def test_another_submitters_case_is_refused(self):
		other = _user(
			f"export-other-sub-{frappe.generate_hash(length=6)}@example.com",
			"Other Person",
			role=C.ROLE_SUBMITTER,
		)
		self.addCleanup(frappe.delete_doc, "User", other, force=True, ignore_permissions=True)
		frappe.set_user(other)
		with _keep_transaction():
			result = self._export()
		self.assertEqual(result["code"], "NOT_FOUND")
		self.assertEqual(self._events(grievance=self.case.name, action="export")[0].decision, "Denied")

	def test_a_ticket_the_caller_cannot_read_looks_exactly_like_one_that_does_not_exist(self):
		# Every submitter holds the export permission, so the answer must not tell them which
		# tickets exist: same code, same message, same status, whichever it is.
		other = _user(
			f"export-other-sub-{frappe.generate_hash(length=6)}@example.com",
			"Other Person",
			role=C.ROLE_SUBMITTER,
		)
		self.addCleanup(frappe.delete_doc, "User", other, force=True, ignore_permissions=True)
		frappe.set_user(other)
		with _keep_transaction():
			unreadable = self._export()
			missing = self._export(ticket="3-999-999Z-9")
		for key in ("code", "status"):
			self.assertEqual(unreadable[key], missing[key])
		# Compared by their last sentence only: this test environment adds unrelated mail-setup notices.
		self.assertTrue(unreadable["message"].endswith("No grievance found with that ticket number."))
		self.assertTrue(missing["message"].endswith("No grievance found with that ticket number."))
		# The audit trail, which the caller cannot read, still tells the two apart.
		reasons = {
			e.scope_evaluated.rsplit("reason=", 1)[1] for e in self._events(user=other, action="export")
		}
		self.assertEqual(reasons, {"case_access", "not_found"})

	def test_a_guest_is_refused_and_audited(self):
		frappe.set_user("Guest")
		with _keep_transaction():
			result = self._export()
		self.assertEqual(result["code"], "PERMISSION_DENIED")
		event = self._events(grievance=self.case.name, action="export")[0]
		self.assertEqual((event.user, event.decision), ("Guest", "Denied"))

	def test_an_unknown_ticket_is_not_found_and_the_attempt_is_audited(self):
		frappe.set_user(self.officer)
		with _keep_transaction():
			result = self._export(ticket="3-999-999Z-9")
		self.assertEqual(result["code"], "NOT_FOUND")
		event = self._events(user=self.officer, action="export")[0]
		self.assertEqual(event.decision, "Denied")
		self.assertIsNone(event.grievance)
		self.assertEqual(event.scope_evaluated, "format=pdf; lang=en; ticket=3-999-999Z-9; reason=not_found")

	def test_a_long_unknown_ticket_is_cut_in_the_audit_row(self):
		frappe.set_user(self.officer)
		with _keep_transaction():
			self._export(ticket="X" * 500)
		event = self._events(user=self.officer, action="export")[0]
		self.assertIn("ticket=" + "X" * ticket_export.AUDIT_TICKET_CHARS + ";", event.scope_evaluated)
		self.assertNotIn("X" * (ticket_export.AUDIT_TICKET_CHARS + 1), event.scope_evaluated)

	def test_a_failed_render_is_audited_as_a_failure_and_returns_no_file(self):
		frappe.set_user(self.officer)
		with (
			_keep_transaction(),
			mock.patch.object(ticket_export, "render_pdf", side_effect=RuntimeError("renderer exploded")),
		):
			result = self._export(format="pdf")
		self.assertEqual(result["code"], "INTERNAL_ERROR")
		self.assertNotIn("exploded", repr(result))
		events = self._events(grievance=self.case.name, user=self.officer)
		self.assertEqual(events[0].action, audit.ACTION_EXPORT_FAILED)
		self.assertEqual(events[0].decision, "Allowed")
		self.assertEqual(
			events[0].scope_evaluated, "format=pdf; lang=en; reason=generation_error (RuntimeError)"
		)
		self.assertFalse([e for e in events if e.action == "export"])
		self.commit.assert_called()

	def test_the_audit_row_never_carries_the_complaint(self):
		frappe.set_user(self.officer)
		self._export(format="csv")
		event = self._events(grievance=self.case.name, action="export")[0]
		for secret in ("Almaz", "251911234567", "plot size", "land"):
			self.assertNotIn(secret, event.scope_evaluated)

	def test_every_attempt_writes_exactly_one_row(self):
		frappe.set_user(self.officer)
		before = len(self._events(user=self.officer))
		self._export(format="csv")
		self.assertEqual(len(self._events(user=self.officer)), before + 1)


class TestExportGuards(TicketExportTestCase):
	"""What keeps the export from leaking, flooding or leaving without a trace."""

	def setUp(self):
		super().setUp()
		patcher = mock.patch.object(frappe.db, "commit")
		self.commit = patcher.start()
		self.addCleanup(patcher.stop)

	def _reviewer(self):
		reviewer = _user(
			f"export-review-{frappe.generate_hash(length=6)}@example.com",
			"Rahel Review",
			role=C.ROLE_REVIEW_OFFICER,
		)
		self.addCleanup(frappe.delete_doc, "User", reviewer, force=True, ignore_permissions=True)
		return reviewer

	def test_a_file_is_not_returned_when_its_audit_row_cannot_be_written(self):
		frappe.set_user(self.officer)
		with (
			_keep_transaction(),
			mock.patch.object(frappe.db, "commit", side_effect=RuntimeError("database went away")),
		):
			result = self._export(format="csv")
		self.assertEqual(result["code"], "INTERNAL_ERROR")
		self.assertNotIn("went away", repr(result))
		self.assertNotEqual(frappe.response.get("type"), "download")

	def test_a_strict_audit_write_raises_and_an_ordinary_one_does_not(self):
		with mock.patch.object(frappe.db, "commit", side_effect=RuntimeError("boom")):
			audit.record_access(audit.ACTION_EXPORT, durable=True)
			with self.assertRaises(RuntimeError):
				audit.record_access(audit.ACTION_EXPORT, durable=True, strict=True)

	def test_the_digest_in_the_audit_row_matches_the_file_that_left(self):
		frappe.set_user(self.officer)
		response = self._export(format="csv")
		event = self._events(grievance=self.case.name, action="export")[0]
		self.assertIn(f"bytes={len(response.data)};", event.scope_evaluated)
		self.assertTrue(event.scope_evaluated.endswith(f"sha256={hashlib.sha256(response.data).hexdigest()}"))

	def test_the_role_is_checked_before_the_query_is_validated(self):
		frappe.set_user(self._reviewer())
		with _keep_transaction():
			result = self._export(format="docx", lang="fr")
		self.assertEqual(result["code"], "PERMISSION_DENIED")
		self.assertFalse(result.get("details"))
		event = self._events(grievance=self.case.name, action="export")[0]
		self.assertEqual(event.scope_evaluated, "format=invalid; lang=invalid; reason=role")

	def test_a_case_with_too_many_entries_is_refused_not_cut_short(self):
		frappe.set_user(self.officer)
		with _keep_transaction(), mock.patch.object(ticket_form, "MAX_ROWS_PER_SOURCE", 1):
			result = self._export(format="csv")
		self.assertEqual(result["code"], "VALIDATION_ERROR")
		self.assertIn("too many entries", result["message"])
		events = self._events(grievance=self.case.name, user=self.officer)
		self.assertEqual(events[0].action, audit.ACTION_EXPORT_FAILED)
		self.assertIn("ExportTooLarge", events[0].scope_evaluated)
		self.assertFalse([e for e in events if e.action == "export"])

	def test_a_file_past_the_size_cap_is_not_returned(self):
		frappe.set_user(self.officer)
		with _keep_transaction(), mock.patch.object(ticket_export, "MAX_FILE_BYTES", 10):
			result = self._export(format="csv")
		self.assertEqual(result["code"], "VALIDATION_ERROR")
		self.assertIn("too large", result["message"])
		self.assertFalse(self._events(grievance=self.case.name, action="export"))

	def test_refusals_stop_writing_audit_rows_past_the_minutes_allowance_but_still_refuse(self):
		frappe.set_user("Guest")
		before = len(self._events(user="Guest", action="export", decision="Denied"))
		with _keep_transaction(), mock.patch.object(ticket_export, "MAX_DENIAL_ROWS_PER_MINUTE", 2):
			results = [self._export() for _ in range(5)]
		self.assertEqual([r["code"] for r in results], ["PERMISSION_DENIED"] * 5)
		self.assertEqual(len(self._events(user="Guest", action="export", decision="Denied")), before + 2)

	def test_the_allowance_belongs_to_one_account(self):
		reviewer = self._reviewer()
		before = len(self._events(user="Guest", action="export", decision="Denied"))
		with _keep_transaction(), mock.patch.object(ticket_export, "MAX_DENIAL_ROWS_PER_MINUTE", 1):
			frappe.set_user("Guest")
			self._export()
			self._export()
			frappe.set_user(reviewer)
			self._export()
		self.assertEqual(len(self._events(user="Guest", action="export", decision="Denied")), before + 1)
		self.assertEqual(len(self._events(user=reviewer, action="export", decision="Denied")), 1)

	def test_each_author_is_looked_up_for_staff_once(self):
		frappe.set_user(self.officer)
		doc = frappe.get_doc("Grievance", self.case.name)
		real = ticket_export.permissions.is_staff
		with mock.patch.object(ticket_export.permissions, "is_staff", side_effect=real) as is_staff:
			ticket_export.build_form(doc)
		users = [call.args[0] for call in is_staff.call_args_list if call.args]
		self.assertEqual(len(users), len(set(users)), msg=f"looked up more than once: {users}")

	def test_every_date_is_in_a_named_zone(self):
		form = self._form()
		zone = dict(form["footer"])["Time Zone"]
		self.assertTrue(zone.startswith(frappe.utils.get_system_timezone()), msg=zone)
		self.assertRegex(zone, r"\(UTC[+-]\d\d:\d\d\)$")
		text = ticket_export.render_csv(form).decode("utf-8-sig")
		self.assertIn(zone, text)


class TestAuditHelper(FrappeTestCase):
	def test_an_ordinary_audit_write_does_not_commit(self):
		with mock.patch.object(frappe.db, "commit") as commit:
			audit.record_access(audit.ACTION_VIEW_LIST)
			audit.log_denied(audit.ACTION_VIEW_LIST)
		commit.assert_not_called()

	def test_a_durable_audit_write_commits(self):
		with mock.patch.object(frappe.db, "commit") as commit:
			audit.record_access(audit.ACTION_EXPORT, durable=True)
		commit.assert_called_once()


class TestExistingApisUnchanged(TicketExportTestCase):
	def test_the_timeline_endpoint_still_answers_and_still_audits_a_view(self):
		frappe.set_user(self.officer)
		result = grievance.timeline(self.case.ticket_number)
		self.assertEqual(result["status"], "success", msg=result)
		self.assertTrue(self._events(grievance=self.case.name, action=audit.ACTION_VIEW_DETAIL))


class TestExportThroughTheRouter(TicketExportTestCase):
	"""The route as a client reaches it: URL, query string, the guard, and the file that comes back."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_routes_registered()

	def setUp(self):
		super().setUp()
		saved = getattr(frappe.local, "request", None)
		self.addCleanup(setattr, frappe.local, "request", saved)
		patcher = mock.patch.object(frappe.db, "commit")
		patcher.start()
		self.addCleanup(patcher.stop)

	def _get(self, query=""):
		request = make_test_request(f"/api/v1/grievances/{self.case.ticket_number}/export{query}")
		return frappe.api.handle(request)

	def test_a_file_comes_back_as_a_download(self):
		frappe.set_user("Administrator")
		pdf = self._get()
		self.assertEqual(pdf.status_code, 200)
		self.assertEqual(pdf.mimetype, "application/pdf")
		self.assertTrue(pdf.get_data().startswith(b"%PDF"))

		csv_response = self._get("?format=csv")
		self.assertEqual(csv_response.status_code, 200)
		self.assertEqual(csv_response.mimetype, "text/csv")
		self.assertIn("attachment", csv_response.headers["Content-Disposition"])
		self.assertEqual(csv_response.headers["Cache-Control"], "private, no-store")
		self.assertIn(tn.display(self.case.ticket_number), csv_response.get_data(as_text=True))

	def test_lang_is_read_from_the_query_string(self):
		frappe.set_user("Administrator")
		response = self._get("?format=csv&lang=am")
		self.assertEqual(response.status_code, 200)
		self.assertIn("-am.csv", response.headers["Content-Disposition"])
		self.assertIn("የቅሬታ አቅራቢ መረጃ", response.get_data(as_text=True))
		self.assertEqual(self._get("?lang=fr").status_code, 400)

	def test_errors_still_use_the_envelope_and_the_status_code(self):
		frappe.set_user("Administrator")
		self.assertEqual(self._get("?format=docx").status_code, 400)

		other = a_grievance(description="Another submitter's grievance, which this person cannot read.")
		self.addCleanup(discard_grievance, other.name)
		frappe.set_user(self.submitter)
		with _keep_transaction():
			request = make_test_request(f"/api/v1/grievances/{other.ticket_number}/export?format=csv")
			response = frappe.api.handle(request)
		self.assertEqual(response.status_code, 404)
		self.assertEqual(response.mimetype, "application/json")

	def test_an_unauthenticated_request_is_stopped_by_the_guard(self):
		request = make_test_request(f"/api/v1/grievances/{self.case.ticket_number}/export")
		with self.assertRaises(frappe.AuthenticationError):
			validate_jwt_request(request)


class TestAmharic(TicketExportTestCase):
	"""The form's own wording in Amharic, from translations/am.csv, with nothing else touched."""

	def _amharic(self, user="Administrator"):
		frappe.set_user(user)
		doc = frappe.get_doc("Grievance", self.case.name)
		with print_language("am"):
			return ticket_export.build_form(doc)

	def test_the_reference_block_and_every_section_title_are_in_amharic(self):
		form = self._amharic()
		english = self._form()
		self.assertEqual(form["lang"], "am")
		headings = [label for label, _value in form["header"]] + [s["title"] for s in form["sections"]]
		untranslated = [h for h in headings if not _has_ethiopic(h)]
		# Every label in the reference block, and every section title, has its Amharic in the Annexure.
		self.assertEqual(untranslated, [], msg=f"no Amharic for: {untranslated}")
		self.assertEqual(
			[s["title"] for s in english["sections"]][:2],
			["Complainant Information", "Subject of the Grievance"],
		)

	def test_the_letterhead_and_the_forms_printed_wording_are_in_amharic(self):
		form = self._amharic()
		self.assertEqual(form["letterhead"][0], "የኢትዮጵያ ፌዴራል ዴሞክራሲያዊ ሪፐብሊክ")
		self.assertTrue(all(line.endswith("አስተዳደር") for line in form["letterhead"][1:]))
		page = ticket_render._pdf_html(form)
		self.assertIn("ከተደረገው ምርመራና ማረጋገጫ በኋላ የተወሰነው፦", page)
		self.assertIn("ይግባኝ ማቅረብ ይችላል።", page)
		self.assertIn('<html lang="am">', page)

	def test_the_checklists_are_in_amharic_and_keep_what_was_ticked(self):
		sections = sections_of(self._amharic())
		self.assertEqual(dict(sections[12]["checks"])["የመጨረሻ ሁኔታ"], ["ተዘግቷል", "ወደ ከፍተኛ አካል ተላልፏል"])
		self.assertEqual(dict(sections[5]["checks"])["የቀረቡ የማስረጃ ሰነዶች"], ["ሌሎች ተያያዥ ማስረጃዎች"])
		# Same English word, two Amharic wordings: the phone number, and "by telephone".
		self.assertEqual(rows_of(sections[1])["ስልክ ቁጥር"], "+251911234567")
		options = next(i for i in sections[10]["items"] if i["kind"] == "checks")["options"]
		self.assertEqual([o["text"] for o in options], ["በጽሁፍ", "በስልክ", "በSMS", "በአካል", "ሌላ"])

	def test_what_people_wrote_is_not_translated_and_the_data_is_the_same(self):
		english, amharic = self._form(), self._amharic()
		self.assertEqual(rows_of(sections_of(amharic)[3])["Description"], self.case.description)
		self.assertEqual(
			[len(s["tables"]) for s in english["sections"]], [len(s["tables"]) for s in amharic["sections"]]
		)
		self.assertEqual(
			[row[1:] for row in english["appendix"]["rows"]], [row[1:] for row in amharic["appendix"]["rows"]]
		)

	def test_wording_with_no_amharic_yet_stays_english_instead_of_failing(self):
		sections = sections_of(self._amharic())
		self.assertIn("Submitter Type", rows_of(sections[1]))
		self.assertEqual(sections[9]["tables"][0]["title"], "Decisions")

	def test_the_amharic_pdf_and_csv_render(self):
		form = self._amharic()
		self.assertTrue(ticket_export.render_pdf(form).startswith(b"%PDF"))
		content = ticket_export.render_csv(form).decode("utf-8-sig")
		self.assertIn("የቅሬታ አቅራቢ መረጃ", content)

	@unittest.skipUnless(_has_ethiopic_font(), "no Ethiopic font on this machine")
	def test_amharic_text_survives_into_the_pdf_when_a_font_is_installed(self):
		pdf = ticket_export.render_pdf(self._amharic())
		text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
		self.assertIn("የቅሬታ", text)

	def test_the_language_choices_match_the_endpoints_schema(self):
		self.assertEqual(
			set(ticket_export.LANGUAGES),
			set(grievance.ExportGrievanceRequest.model_fields["lang"].annotation.__args__),
		)
