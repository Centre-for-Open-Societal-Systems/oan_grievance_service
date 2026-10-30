# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""STG-478: validation errors name their field and state the rule that was broken.

The rules and limits come from RefferDocs/Form-Validation-Summary.xlsx. Each test pins
the wording a client sees as `details[<field>]` in the 400 `VALIDATION_ERROR` response.

Covers:
M1. Organisation registration: required, 1-140, registration number, postal code,
    website, member count, consent.
M2. SLA settings and category names: minimums, maximums, required.
M3. Grievance submission: description, desired outcome, submitter name.
M4. Deferral reason, officer name, submitter registration name.
M5. The message rewriter itself: required, minimum, maximum, pattern, number, literal.
M6. Endpoints answer 400 with the same text and write nothing.
M7. A field's label is its `title`; no request schema can surface a raw pydantic message.
"""

import importlib
import pkgutil

import frappe
from frappe.tests.utils import FrappeTestCase
from oan_auth_service.api.field_errors import field_label, friendly_message, has_wording
from oan_auth_service.tests.utils import raw_message_leaks, request_schemas
from pydantic import ValidationError

import oan_grievance_service.api.v1 as api_v1
from oan_grievance_service.api.v1 import grievance, officer, organization, sla_settings, taxonomy
from oan_grievance_service.api.v1.grievance import DeferSLARequest, SubmitGrievanceRequest
from oan_grievance_service.api.v1.submitter import RegisterSubmitterRequest
from oan_grievance_service.tests.fixtures import a_user_with_role


def _errors(model, **payload):
	"""{field: message} for a payload the schema refuses; empty when it accepts."""
	try:
		model(**payload)
	except ValidationError as error:
		return {".".join(str(part) for part in e["loc"]): friendly_message(e, model) for e in error.errors()}
	return {}


ORGANISATION = {
	"legal_name": "Oromia Farmers Union",
	"org_type": "FPO",
	"entity_type": "Cooperative",
	"registration_number": "Oromia/Coop/Agri-045/2018",
	"contact_email": "office@example.org",
	"contact_phone": "+251911000000",
	"street_address": "Bole Road",
	"zone": "Zone 1",
	"region": "Oromia",
	"country": "Ethiopia",
	"postal_code": "1000",
	"consent_given": True,
}


class TestOrganisationMessages(FrappeTestCase):
	"""M1: organisation registration (Form Validation Summary, 'Organisation registration')."""

	Create = organization.CreateOrganization

	def test_valid_registration_passes(self):
		self.assertEqual(_errors(self.Create, **ORGANISATION), {})

	def test_required_fields_say_so(self):
		for field in ("legal_name", "street_address", "contact_phone", "registration_number"):
			with self.subTest(field=field):
				payload = {k: v for k, v in ORGANISATION.items() if k != field}
				self.assertEqual(
					_errors(self.Create, **payload), {field: f"{field_label((field,))} is required."}
				)

	def test_blank_and_whitespace_only_count_as_missing(self):
		for value in ("", "   "):
			errors = _errors(self.Create, **{**ORGANISATION, "legal_name": value})
			self.assertEqual(errors, {"legal_name": "Legal name is required."})

	def test_text_fields_stop_at_140(self):
		for field in ("legal_name", "representative_name", "street_address", "contact_phone"):
			with self.subTest(field=field):
				self.assertEqual(_errors(self.Create, **{**ORGANISATION, field: "x" * 140}), {})
				errors = _errors(self.Create, **{**ORGANISATION, field: "x" * 141})
				self.assertEqual(errors, {field: f"{field_label((field,))} must not exceed 140 characters."})

	def test_registration_number(self):
		for good in ("2330", "MT/AA/14/0001234/2018", "0045678912", "A" * 60, "ABC"):
			self.assertEqual(_errors(self.Create, **{**ORGANISATION, "registration_number": good}), {}, good)
		for bad in ("AB", "A" * 61, "bad number", "-2330", "23//30"):
			errors = _errors(self.Create, **{**ORGANISATION, "registration_number": bad})
			self.assertEqual(
				errors, {"registration_number": "Registration number is not in a valid format."}, bad
			)

	def test_postal_code(self):
		for good in ("1000", "ABC", "A" * 10, "SW1A 1AA"):
			self.assertEqual(_errors(self.Create, **{**ORGANISATION, "postal_code": good}), {}, good)
		for bad in ("12", "A" * 11, "12#4", ""):
			errors = _errors(self.Create, **{**ORGANISATION, "postal_code": bad})
			self.assertEqual(errors, {"postal_code": "Postal code must match the required format."}, bad)

	def test_website(self):
		self.assertEqual(_errors(self.Create, **{**ORGANISATION, "website": "https://example.org"}), {})
		errors = _errors(self.Create, **{**ORGANISATION, "website": "ftp://example.org"})
		self.assertEqual(errors, {"website": "Website must be a valid http or https address."})

	def test_member_count(self):
		for good in (0, 100_000_000):
			self.assertEqual(_errors(self.Create, **{**ORGANISATION, "members_affected": good}), {})
		self.assertEqual(
			_errors(self.Create, **{**ORGANISATION, "members_affected": -1}),
			{"members_affected": "Members affected must be at least 0."},
		)
		self.assertEqual(
			_errors(self.Create, **{**ORGANISATION, "members_affected": 100_000_001}),
			{"members_affected": "Members affected must not exceed 100000000."},
		)
		self.assertEqual(
			_errors(self.Create, **{**ORGANISATION, "members_affected": "many"}),
			{"members_affected": "Members affected must be a whole number."},
		)

	def test_consent_must_be_true(self):
		self.assertEqual(
			_errors(self.Create, **{**ORGANISATION, "consent_given": False}),
			{"consent_given": "Consent given must be accepted."},
		)
		payload = {k: v for k, v in ORGANISATION.items() if k != "consent_given"}
		self.assertEqual(_errors(self.Create, **payload), {"consent_given": "Consent given is required."})

	def test_several_errors_come_back_together(self):
		errors = _errors(
			self.Create,
			**{**ORGANISATION, "legal_name": "x" * 141, "postal_code": "1", "consent_given": False},
		)
		self.assertEqual(sorted(errors), ["consent_given", "legal_name", "postal_code"])

	def test_update_applies_the_same_rules(self):
		errors = _errors(organization.UpdateOrganization, legal_name="x" * 141, postal_code="1")
		self.assertEqual(
			errors,
			{
				"legal_name": "Legal name must not exceed 140 characters.",
				"postal_code": "Postal code must match the required format.",
			},
		)


class TestSlaMessages(FrappeTestCase):
	"""M2: SLA settings and category names."""

	def test_global_policy(self):
		Update = sla_settings.UpdateGlobalSlaPolicy
		self.assertEqual(_errors(Update, max_deferral_days=1, auto_escalation_threshold=100), {})
		self.assertEqual(
			_errors(Update, max_deferral_days=0),
			{"max_deferral_days": "Max deferral days must be at least 1."},
		)
		self.assertEqual(
			_errors(Update, auto_escalation_threshold=0),
			{"auto_escalation_threshold": "Auto escalation threshold must be at least 1."},
		)
		self.assertEqual(
			_errors(Update, auto_escalation_threshold=101),
			{"auto_escalation_threshold": "Auto escalation threshold must not exceed 100."},
		)

	def test_sla_days(self):
		Update = sla_settings.UpdateSlaConfiguration
		self.assertEqual(_errors(Update, config="C", sla_days=1), {})
		self.assertEqual(
			_errors(Update, config="C", sla_days=0), {"sla_days": "SLA days must be at least 1."}
		)
		self.assertEqual(
			_errors(Update, config="C", sla_days="soon"), {"sla_days": "SLA days must be a whole number."}
		)

	def test_category_and_type_names(self):
		for model, field, extra in (
			(taxonomy.CreateServiceCategory, "category_name", {"code": "ABC"}),
			(taxonomy.CreateGrievanceType, "type_name", {"service_category": "C"}),
		):
			label = field_label((field,))
			with self.subTest(field=field):
				self.assertEqual(_errors(model, **{field: "n" * 140}, **extra), {})
				self.assertEqual(
					_errors(model, **{field: "n" * 141}, **extra),
					{field: f"{label} must not exceed 140 characters."},
				)
				self.assertEqual(_errors(model, **{field: "  "}, **extra), {field: f"{label} is required."})


class TestSubmissionMessages(FrappeTestCase):
	"""M3 and M4: grievance submission, deferral, officer and registration names."""

	def test_description(self):
		self.assertEqual(_errors(SubmitGrievanceRequest, description="d" * 20), {})
		self.assertEqual(_errors(SubmitGrievanceRequest, description="d" * 5000), {})
		self.assertEqual(
			_errors(SubmitGrievanceRequest, description="d" * 19),
			{"description": "Description must contain at least 20 characters."},
		)
		self.assertEqual(
			_errors(SubmitGrievanceRequest, description="d" * 5001),
			{"description": "Description must not exceed 5000 characters."},
		)

	def test_desired_outcome(self):
		self.assertEqual(
			_errors(SubmitGrievanceRequest, desired_outcome="o" * 1001),
			{"desired_outcome": "Desired outcome must not exceed 1000 characters."},
		)

	def test_submitter_name(self):
		self.assertEqual(_errors(SubmitGrievanceRequest, submitter_name="n" * 140), {})
		self.assertEqual(
			_errors(SubmitGrievanceRequest, submitter_name="n" * 141),
			{"submitter_name": "Submitter name must not exceed 140 characters."},
		)
		self.assertEqual(_errors(RegisterSubmitterRequest, submitter_name="n" * 140), {})
		self.assertEqual(
			_errors(RegisterSubmitterRequest, submitter_name="n" * 141),
			{"submitter_name": "Submitter name must not exceed 140 characters."},
		)

	def test_deferral(self):
		self.assertEqual(
			_errors(DeferSLARequest, additional_days=1, reason="r" * 19),
			{"reason": "Reason must contain at least 20 characters."},
		)
		self.assertEqual(
			_errors(DeferSLARequest, additional_days=1, reason="r" * 1001),
			{"reason": "Reason must not exceed 1000 characters."},
		)
		self.assertEqual(
			_errors(DeferSLARequest, additional_days=0, reason="r" * 20),
			{"additional_days": "Additional days must be at least 1."},
		)
		self.assertEqual(
			_errors(DeferSLARequest, reason="r" * 20), {"additional_days": "Additional days is required."}
		)

	def test_officer_full_name(self):
		self.assertEqual(
			_errors(officer.UpdateOfficer, officer="o@example.com", full_name="n" * 141),
			{"full_name": "Full name must not exceed 140 characters."},
		)
		self.assertEqual(
			_errors(officer.UpdateOfficer, officer="o@example.com", full_name="   "),
			{"full_name": "Full name is required."},
		)


class TestLabels(FrappeTestCase):
	"""M7: the label comes from the field's `title`, so an acronym is set where it is used."""

	def test_acronym_fields_carry_their_title(self):
		for schema, field, label in (
			(sla_settings.UpdateSlaConfiguration, "sla_days", "SLA days"),
			(taxonomy.CreateServiceCategory, "sla_days", "SLA days"),
			(organization.OrganizationRef, "org_id", "Organization ID"),
			(SubmitGrievanceRequest, "fayda_id", "Fayda ID"),
		):
			with self.subTest(schema=schema.__name__, field=field):
				self.assertEqual(field_label((field,), schema), label)

	def test_a_title_does_not_leak_to_another_schema(self):
		self.assertEqual(field_label(("sla_days",), organization.OrganizationRef), "Sla days")

	def test_the_title_is_in_the_message(self):
		self.assertEqual(
			_errors(sla_settings.UpdateSlaConfiguration, config="C", sla_days=0),
			{"sla_days": "SLA days must be at least 1."},
		)


class TestCoverage(FrappeTestCase):
	"""M7: every built-in error a request schema can raise has wording."""

	def test_no_request_schema_can_surface_a_raw_pydantic_message(self):
		modules = [
			importlib.import_module(f"{api_v1.__name__}.{module.name}")
			for module in pkgutil.iter_modules(api_v1.__path__)
		]
		schemas = request_schemas(*modules)
		self.assertGreater(len(schemas), 50)
		self.assertEqual(raw_message_leaks(schemas, has_wording), [])


class TestRewriter(FrappeTestCase):
	"""M5: how this service's own validators meet the auth app's rewriter (the rules themselves
	are tested in oan_auth_service)."""

	def test_messages_written_by_validators_pass_through(self):
		error = {
			"type": "string_too_long",
			"loc": ("action_taken",),
			"msg": "Action taken must not exceed 500 characters.",
			"ctx": {"label": "Action taken", "max_length": 500},
		}
		self.assertEqual(friendly_message(error), "Action taken must not exceed 500 characters.")

	def test_unknown_fields_are_named(self):
		self.assertEqual(
			_errors(organization.OrganizationRef, org_id="O", surprise=1),
			{"surprise": "Surprise is not an accepted field."},
		)

	def test_no_internal_detail_in_messages(self):
		errors = _errors(organization.CreateOrganization, **{**ORGANISATION, "members_affected": object()})
		for message in errors.values():
			self.assertNotIn("pydantic", message.lower())
			self.assertNotIn("traceback", message.lower())


class TestEndpoints(FrappeTestCase):
	"""M6: the 400 an API client receives."""

	def test_organisation_register_returns_field_messages_and_creates_nothing(self):
		user = a_user_with_role("Grievance Submitter", "orgfiler")
		self.addCleanup(frappe.delete_doc, "User", user, force=True, ignore_permissions=True)
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user(user)
		before = frappe.db.count("Grievance Organization")
		result = organization.register_organization(
			**{**ORGANISATION, "legal_name": "x" * 141, "postal_code": "1"}
		)
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
		self.assertEqual(
			result["details"],
			{
				"legal_name": "Legal name must not exceed 140 characters.",
				"postal_code": "Postal code must match the required format.",
			},
		)
		self.assertEqual(frappe.response["http_status_code"], 400)
		self.assertEqual(frappe.db.count("Grievance Organization"), before)

	def test_submit_returns_field_messages_and_creates_nothing(self):
		before = frappe.db.count("Grievance")
		result = grievance.submit(description="too short", desired_outcome="o" * 1001, consent_given=1)
		self.assertEqual(result["code"], "VALIDATION_ERROR")
		self.assertEqual(
			result["details"],
			{
				"description": "Description must contain at least 20 characters.",
				"desired_outcome": "Desired outcome must not exceed 1000 characters.",
			},
		)
		self.assertEqual(frappe.response["http_status_code"], 400)
		self.assertEqual(frappe.db.count("Grievance"), before)
