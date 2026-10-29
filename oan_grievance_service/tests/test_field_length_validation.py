# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""STG-478: the free-text limits from the Form Validation Summary ("Backend gaps").

Every limit is checked at the request boundary: the value exactly at the limit is
accepted, one character over is refused with a 400 that names the field, and the
refused request writes nothing.

Covers:
L1. Officer response: action_taken 500, resolution_summary 2000, internal_notes 2000,
    and the same limits when a two-part response is sent as one `reason` text.
L2. Internal note on /message: 2000 for a note, no limit for a public message.
L3. Rejecting a deferral, reassignment or change request needs 20 to 1000 characters;
    an approval note is not constrained.
L4. Reassignment justification: 1000.
L5. Response template: title 140, action_taken 500, resolution_summary 2000, on create
    and on update.
L6. Grievance submission: description 20 to 5000, desired outcome 1000.
L7. SLA deferral reason 20 to 1000; officer full name 140 on create and update.
"""

from types import MappingProxyType

import frappe
from frappe.tests.utils import FrappeTestCase
from oan_auth_service.api.field_errors import friendly_code, friendly_message
from pydantic import ValidationError

from oan_grievance_service.api.v1 import grievance, officer, response_config
from oan_grievance_service.api.v1.change_request import DecideChangeRequest, decide
from oan_grievance_service.api.v1.grievance import (
	DecideDeferralRequest,
	DecideReassignmentRequest,
	DeferSLARequest,
	GrievanceActionRequest,
	PostMessageRequest,
	ReassignGrievanceRequest,
	SubmitGrievanceRequest,
)
from oan_grievance_service.services import response_body


def _errors(model, **payload):
	"""{field: message} for a payload the schema refuses; empty when it accepts."""
	try:
		model(**payload)
	except ValidationError as error:
		return {".".join(str(part) for part in e["loc"]): friendly_message(e) for e in error.errors()}
	return {}


def _two_parts(action_taken, resolution_summary):
	return response_body.compose(action_taken, resolution_summary)


class TestResponseLimits(FrappeTestCase):
	"""L1 and L2: officer response fields and internal notes."""

	def test_action_taken_boundary(self):
		base = {"action": "Resolve", "resolution_summary": "Done."}
		self.assertEqual(_errors(GrievanceActionRequest, action_taken="a" * 500, **base), {})
		errors = _errors(GrievanceActionRequest, action_taken="a" * 501, **base)
		self.assertEqual(list(errors), ["action_taken"])
		self.assertIn("500", errors["action_taken"])

	def test_resolution_summary_boundary(self):
		base = {"action": "Resolve", "action_taken": "Fixed."}
		self.assertEqual(_errors(GrievanceActionRequest, resolution_summary="b" * 2000, **base), {})
		errors = _errors(GrievanceActionRequest, resolution_summary="b" * 2001, **base)
		self.assertEqual(list(errors), ["resolution_summary"])
		self.assertIn("2000", errors["resolution_summary"])

	def test_internal_notes_boundary(self):
		self.assertEqual(_errors(GrievanceActionRequest, action="Resolve", internal_notes="n" * 2000), {})
		errors = _errors(GrievanceActionRequest, action="Resolve", internal_notes="n" * 2001)
		self.assertEqual(list(errors), ["internal_notes"])

	def test_surrounding_whitespace_is_not_counted_and_is_dropped(self):
		padded = "  " + "a" * 500 + "  "
		request = GrievanceActionRequest(action="Resolve", action_taken=padded, resolution_summary="x")
		self.assertEqual(request.action_taken, "a" * 500)
		over = "  " + "a" * 501 + "  "
		self.assertEqual(
			_errors(GrievanceActionRequest, action="Resolve", action_taken=over, resolution_summary="x"),
			{"action_taken": "Action taken must not exceed 500 characters."},
		)

	def test_blank_parts_are_still_treated_as_missing(self):
		request = GrievanceActionRequest(action="Resolve", action_taken="   ", resolution_summary="  ")
		self.assertIsNone(request.action_taken)
		self.assertIsNone(request.resolution_summary)

	def test_parts_sent_as_one_reason_keep_their_limits(self):
		ok = _two_parts("a" * 500, "b" * 2000)
		self.assertEqual(_errors(GrievanceActionRequest, action="Resolve", reason=ok), {})
		long_action = _errors(GrievanceActionRequest, action="Resolve", reason=_two_parts("a" * 501, "b"))
		self.assertEqual(list(long_action), ["reason"])
		self.assertIn("Action taken", long_action["reason"])
		long_summary = _errors(GrievanceActionRequest, action="Resolve", reason=_two_parts("a", "b" * 2001))
		self.assertIn("Resolution summary", long_summary["reason"])

	def test_plain_reason_has_no_limit(self):
		self.assertEqual(_errors(GrievanceActionRequest, action="Reject", reason="r" * 5000), {})

	def test_internal_note_message_boundary(self):
		self.assertEqual(_errors(PostMessageRequest, body="n" * 2000, is_internal=True), {})
		errors = _errors(PostMessageRequest, body="n" * 2001, is_internal=True)
		self.assertEqual(list(errors), ["body"])
		self.assertEqual(errors["body"], "An internal note must not exceed 2000 characters.")

	def test_internal_flag_forms(self):
		for flag in ("true", "1", "yes", True):
			self.assertEqual(list(_errors(PostMessageRequest, body="n" * 2001, is_internal=flag)), ["body"])
		for flag in ("false", "0", False):
			self.assertEqual(_errors(PostMessageRequest, body="n" * 2001, is_internal=flag), {})

	def test_legacy_type_alias_makes_it_an_internal_note(self):
		self.assertEqual(list(_errors(PostMessageRequest, body="n" * 2001, type="note")), ["body"])
		self.assertEqual(_errors(PostMessageRequest, body="n" * 2001, type="message"), {})

	def test_public_message_has_no_limit(self):
		self.assertEqual(_errors(PostMessageRequest, body="m" * 5000), {})

	def test_endpoint_refuses_with_400_and_names_the_field(self):
		before = frappe.db.count("Grievance Timeline")
		result = grievance.message(ticket_number="NOPE", body="n" * 2001, is_internal=True)
		self.assertEqual(result["code"], "VALIDATION_ERROR")
		self.assertIn("body", result["details"])
		self.assertEqual(frappe.response["http_status_code"], 400)
		self.assertEqual(frappe.db.count("Grievance Timeline"), before)

	def test_action_endpoint_refuses_with_400_and_writes_nothing(self):
		before = frappe.db.count("Grievance Timeline")
		result = grievance.action(
			ticket_number="NOPE", action="Resolve", action_taken="a" * 501, resolution_summary="ok"
		)
		self.assertEqual(result["code"], "VALIDATION_ERROR")
		self.assertEqual(list(result["details"]), ["action_taken"])
		self.assertEqual(frappe.response["http_status_code"], 400)
		self.assertEqual(frappe.db.count("Grievance Timeline"), before)

	def test_several_bad_fields_are_reported_together(self):
		result = grievance.action(
			ticket_number="NOPE",
			action="Resolve",
			action_taken="a" * 501,
			resolution_summary="b" * 2001,
			internal_notes="n" * 2001,
		)
		self.assertEqual(sorted(result["details"]), ["action_taken", "internal_notes", "resolution_summary"])


class TestRejectionComment(FrappeTestCase):
	"""L3: a rejection carries a 20 to 1000 character comment."""

	models = (DecideReassignmentRequest, DecideDeferralRequest, DecideChangeRequest)

	def test_rejection_comment_boundaries(self):
		for model in self.models:
			with self.subTest(model=model.__name__):
				self.assertEqual(_errors(model, decision="Rejected", note="x" * 20), {})
				self.assertEqual(_errors(model, decision="Rejected", note="x" * 1000), {})
				for note in ("x" * 19, "x" * 1001):
					errors = _errors(model, decision="Rejected", note=note)
					self.assertEqual(list(errors), ["note"], msg=len(note))

	def test_rejection_wording_names_the_rule_and_the_limits(self):
		self.assertEqual(
			_errors(DecideDeferralRequest, decision="Rejected", note="too short"),
			{"note": "A rejection needs a comment of at least 20 characters."},
		)
		self.assertEqual(
			_errors(DecideDeferralRequest, decision="Rejected", note="x" * 1001),
			{"note": "A rejection comment must not exceed 1000 characters."},
		)

	def test_rejection_errors_carry_their_own_codes(self):
		def codes(**payload):
			try:
				DecideDeferralRequest(**payload)
			except ValidationError as error:
				return {e["loc"][0]: friendly_code(e) for e in error.errors()}

		self.assertEqual(
			codes(decision="Rejected", note="short"),
			{"note": {"code": "rejection_comment_too_short", "min_length": 20}},
		)
		self.assertEqual(
			codes(decision="Rejected", note="x" * 1001),
			{"note": {"code": "rejection_comment_too_long", "max_length": 1000}},
		)

	def test_a_bad_decision_is_not_also_reported_on_the_note(self):
		self.assertEqual(list(_errors(DecideDeferralRequest, decision="Maybe", note="short")), ["decision"])

	def test_rejection_needs_a_comment(self):
		for model in self.models:
			for note in (None, "", "   ", " " * 40):
				with self.subTest(model=model.__name__, note=note):
					kwargs = {} if note is None else {"note": note}
					self.assertEqual(list(_errors(model, decision="Rejected", **kwargs)), ["note"])

	def test_whitespace_padding_does_not_count_towards_the_minimum(self):
		for model in self.models:
			self.assertEqual(list(_errors(model, decision="Rejected", note="  short  " + " " * 30)), ["note"])

	def test_decision_is_case_insensitive_for_the_rule(self):
		self.assertEqual(list(_errors(DecideDeferralRequest, decision=" rejected ")), ["note"])

	def test_approval_note_is_not_constrained(self):
		for model in self.models:
			with self.subTest(model=model.__name__):
				self.assertEqual(_errors(model, decision="Approved"), {})
				self.assertEqual(_errors(model, decision="Approved", note="ok"), {})
				self.assertEqual(_errors(model, decision="Approved", note="y" * 5000), {})

	def test_bad_decision_is_reported_on_decision_only(self):
		self.assertEqual(list(_errors(DecideChangeRequest, decision="Maybe")), ["decision"])

	def test_endpoints_refuse_with_400_and_change_nothing(self):
		before = frappe.db.count("Grievance Change Request Approval")
		for endpoint, kwargs in (
			(grievance.decide_reassignment, {"ticket_number": "NOPE"}),
			(grievance.decide_deferral, {"ticket_number": "NOPE"}),
			(decide, {"name": "NOPE"}),
		):
			with self.subTest(endpoint=endpoint.__name__):
				result = endpoint(decision="Rejected", note="too short", **kwargs)
				self.assertEqual(result["code"], "VALIDATION_ERROR")
				self.assertEqual(list(result["details"]), ["note"])
				self.assertEqual(frappe.response["http_status_code"], 400)
		self.assertEqual(frappe.db.count("Grievance Change Request Approval"), before)


class TestJustification(FrappeTestCase):
	"""L4: the reassignment justification."""

	def test_boundary(self):
		self.assertEqual(_errors(ReassignGrievanceRequest, target_department="D", reason="j" * 1000), {})
		errors = _errors(ReassignGrievanceRequest, target_department="D", reason="j" * 1001)
		self.assertEqual(list(errors), ["reason"])
		self.assertIn("1000", errors["reason"])

	def test_justification_stays_optional(self):
		self.assertEqual(_errors(ReassignGrievanceRequest, target_department="D"), {})
		self.assertEqual(_errors(ReassignGrievanceRequest, target_department="D", reason=""), {})

	def test_endpoint_refuses_with_400(self):
		before = frappe.db.count("Grievance Change Request")
		result = grievance.reassign(ticket_number="NOPE", target_department="D", reason="j" * 1001)
		self.assertEqual(result["code"], "VALIDATION_ERROR")
		self.assertEqual(list(result["details"]), ["reason"])
		self.assertEqual(frappe.db.count("Grievance Change Request"), before)


class TestResponseTemplateLimits(FrappeTestCase):
	"""L5: response template title and the two response parts."""

	Create = response_config.CreateResponseTemplateRequest
	Update = response_config.UpdateResponseTemplateRequest
	base = MappingProxyType({"workflow_action": "Resolve"})

	def test_title_boundary(self):
		self.assertEqual(
			_errors(self.Create, title="t" * 140, action_taken="a", resolution_summary="s", **self.base), {}
		)
		errors = _errors(self.Create, title="t" * 141, action_taken="a", resolution_summary="s", **self.base)
		self.assertEqual(list(errors), ["title"])
		self.assertEqual(list(_errors(self.Update, template="T", title="t" * 141)), ["title"])
		self.assertEqual(_errors(self.Update, template="T", title="t" * 140), {})

	def test_title_blank_is_still_refused(self):
		self.assertEqual(list(_errors(self.Create, title="   ", body="b", **self.base)), ["title"])

	def test_part_boundaries_on_create(self):
		ok = {"title": "T", "action_taken": "a" * 500, "resolution_summary": "s" * 2000, **self.base}
		self.assertEqual(_errors(self.Create, **ok), {})
		self.assertEqual(list(_errors(self.Create, **{**ok, "action_taken": "a" * 501})), ["action_taken"])
		self.assertEqual(
			list(_errors(self.Create, **{**ok, "resolution_summary": "s" * 2001})), ["resolution_summary"]
		)

	def test_part_boundaries_on_update(self):
		ok = {"template": "T", "action_taken": "a" * 500, "resolution_summary": "s" * 2000}
		self.assertEqual(_errors(self.Update, **ok), {})
		self.assertEqual(list(_errors(self.Update, **{**ok, "action_taken": "a" * 501})), ["action_taken"])
		self.assertEqual(
			list(_errors(self.Update, **{**ok, "resolution_summary": "s" * 2001})), ["resolution_summary"]
		)

	def test_parts_hidden_in_body_or_reason_keep_their_limits(self):
		too_long = _two_parts("a" * 501, "s")
		for field in ("body", "reason"):
			with self.subTest(field=field):
				self.assertEqual(
					list(_errors(self.Create, title="T", **{field: too_long}, **self.base)), [field]
				)
				self.assertEqual(list(_errors(self.Update, template="T", **{field: too_long})), [field])

	def test_plain_body_has_no_limit(self):
		self.assertEqual(_errors(self.Create, title="T", body="b" * 5000, **self.base), {})

	def test_update_without_the_fields_is_untouched(self):
		self.assertEqual(_errors(self.Update, template="T", is_active=False), {})

	def test_endpoint_accepts_values_at_the_limits(self):
		frappe.set_user("Administrator")
		result = response_config.create_response_template(
			title="t" * 140,
			workflow_action="Resolve",
			action_taken="a" * 500,
			resolution_summary="s" * 2000,
		)
		self.assertEqual(result["status"], "success", msg=result)
		record = result["data"]["response_template"]
		self.assertEqual(len(record["title"]), 140)
		self.assertEqual(len(record["reason_parts"]["action_taken"]), 500)
		self.assertEqual(len(record["reason_parts"]["resolution_summary"]), 2000)

	def test_endpoint_refuses_with_400_and_writes_nothing(self):
		frappe.set_user("Administrator")
		before = frappe.db.count("Grievance Response Template")
		result = response_config.create_response_template(
			title="t" * 141, workflow_action="Resolve", body="body"
		)
		self.assertEqual(result["code"], "VALIDATION_ERROR")
		self.assertEqual(list(result["details"]), ["title"])
		self.assertEqual(frappe.response["http_status_code"], 400)
		self.assertEqual(frappe.db.count("Grievance Response Template"), before)


class TestSubmissionLimits(FrappeTestCase):
	"""L6: description and desired outcome on the submit endpoint."""

	def test_description_boundaries(self):
		self.assertEqual(_errors(SubmitGrievanceRequest, description="d" * 20), {})
		self.assertEqual(_errors(SubmitGrievanceRequest, description="d" * 5000), {})
		for text in ("d" * 19, "d" * 5001):
			errors = _errors(SubmitGrievanceRequest, description=text)
			self.assertEqual(list(errors), ["description"], msg=len(text))

	def test_description_stays_optional_at_the_schema(self):
		self.assertEqual(_errors(SubmitGrievanceRequest), {})

	def test_desired_outcome_boundary(self):
		self.assertEqual(_errors(SubmitGrievanceRequest, desired_outcome="o" * 1000), {})
		self.assertEqual(_errors(SubmitGrievanceRequest, desired_outcome=""), {})
		errors = _errors(SubmitGrievanceRequest, desired_outcome="o" * 1001)
		self.assertEqual(list(errors), ["desired_outcome"])
		self.assertIn("1000", errors["desired_outcome"])

	def test_endpoint_refuses_with_400_and_creates_no_grievance(self):
		before = frappe.db.count("Grievance")
		result = grievance.submit(description="d" * 5001, desired_outcome="o" * 1001, consent_given=1)
		self.assertEqual(result["code"], "VALIDATION_ERROR")
		self.assertEqual(sorted(result["details"]), ["description", "desired_outcome"])
		self.assertEqual(frappe.response["http_status_code"], 400)
		self.assertEqual(frappe.db.count("Grievance"), before)


class TestDeferralAndOfficerLimits(FrappeTestCase):
	"""L7: SLA deferral reason and officer full name."""

	def test_deferral_reason_boundaries(self):
		self.assertEqual(_errors(DeferSLARequest, additional_days=1, reason="r" * 20), {})
		self.assertEqual(_errors(DeferSLARequest, additional_days=1, reason="r" * 1000), {})
		for text in ("r" * 19, "r" * 1001):
			errors = _errors(DeferSLARequest, additional_days=1, reason=text)
			self.assertEqual(list(errors), ["reason"], msg=len(text))

	def test_deferral_reason_is_measured_without_surrounding_spaces(self):
		self.assertEqual(
			_errors(DeferSLARequest, additional_days=1, reason="   short   " + " " * 20),
			{"reason": "Reason must contain at least 20 characters."},
		)
		request = DeferSLARequest(additional_days=1, reason="  " + "r" * 20 + "  ")
		self.assertEqual(request.reason, "r" * 20)

	def test_deferral_endpoint_refuses_with_400_and_creates_no_request(self):
		before = frappe.db.count("Grievance Change Request")
		result = grievance.defer_sla(ticket_number="NOPE", additional_days=2, reason="r" * 1001)
		self.assertEqual(result["code"], "VALIDATION_ERROR")
		self.assertEqual(list(result["details"]), ["reason"])
		self.assertEqual(frappe.db.count("Grievance Change Request"), before)

	def test_officer_full_name_boundaries(self):
		# Creating an officer has other required fields; only `full_name` is under test.
		create = {"email": "a@example.com", "temporary_password": "Passw0rd!x"}
		self.assertNotIn("full_name", _errors(officer.CreateOfficer, full_name="n" * 140, **create))
		self.assertIn("full_name", _errors(officer.CreateOfficer, full_name="n" * 141, **create))
		self.assertIn("full_name", _errors(officer.CreateOfficer, full_name="  ", **create))
		self.assertEqual(_errors(officer.UpdateOfficer, officer="o@example.com", full_name="n" * 140), {})
		self.assertEqual(
			list(_errors(officer.UpdateOfficer, officer="o@example.com", full_name="n" * 141)),
			["full_name"],
		)
