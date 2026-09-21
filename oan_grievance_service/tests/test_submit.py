"""STG-328: Submit Grievance API — validation, ticket ID, draft carry-over."""

import frappe
from frappe.tests.utils import FrappeTestCase
from pydantic import ValidationError as PydanticValidationError

from oan_grievance_service.api.v1 import draft
from oan_grievance_service.api.v1.grievance import submit
from oan_grievance_service.services import constants as C
from oan_grievance_service.services.identity import validate_required_submission_fields
from oan_grievance_service.tests.fixtures import a_leaf_area, discard_grievance


def _ensure_submitter_user(email="stg328.submitter@example.com"):
	if not frappe.db.exists("Role", "Grievance Submitter"):
		frappe.get_doc({"doctype": "Role", "role_name": "Grievance Submitter"}).insert(
			ignore_permissions=True
		)
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "STG328",
				"last_name": "Submitter",
				"send_welcome_email": 0,
				"new_password": "Test@12345",
			}
		).insert(ignore_permissions=True)
		user.add_roles("Grievance Submitter")
	else:
		user = frappe.get_doc("User", email)
		user.add_roles("Grievance Submitter")

	if not frappe.db.exists("Grievance Submitter Type", "Individual Farmer"):
		frappe.get_doc(
			{"doctype": "Grievance Submitter Type", "type_name": "Individual Farmer", "is_active": 1}
		).insert(ignore_permissions=True)

	profile_name = frappe.db.get_value("Grievance Submitter Profile", {"user": email}, "name")
	if not profile_name:
		profile = frappe.get_doc(
			{
				"doctype": "Grievance Submitter Profile",
				"user": email,
				"submitter_type": "Individual Farmer",
				"submitter_name": "STG328 Farmer",
				"contact_mobile": "+251911328001",
				"contact_email": email,
				"active": 1,
			}
		).insert(ignore_permissions=True)
		profile_name = profile.name
	return user, profile_name


def _ensure_masters():
	if not frappe.db.exists("Grievance Service Category", "Inputs"):
		frappe.get_doc(
			{
				"doctype": "Grievance Service Category",
				"category_name": "Inputs",
				"code": "001",
				"is_active": 1,
			}
		).insert(ignore_permissions=True)
	if not frappe.db.get_value("Grievance Type", {"type_name": "Fertilizer Shortage"}, "name"):
		frappe.get_doc(
			{
				"doctype": "Grievance Type",
				"type_name": "Fertilizer Shortage",
				"service_category": "Inputs",
				"is_active": 1,
			}
		).insert(ignore_permissions=True)
	if not frappe.db.exists("Grievance Submission Type", "Mobile App"):
		frappe.get_doc(
			{
				"doctype": "Grievance Submission Type",
				"submission_type_name": "Mobile App",
				"is_active": 1,
			}
		).insert(ignore_permissions=True)
	return a_leaf_area()


class TestSubmitGrievanceAPI(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.area = _ensure_masters()
		self.user, self.profile = _ensure_submitter_user()
		self.tickets = []

	def tearDown(self):
		frappe.set_user("Administrator")
		for ticket in self.tickets:
			discard_grievance(ticket)
		frappe.db.rollback()
		super().tearDown()

	def _track(self, result):
		ticket = result["data"]["ticket_number"]
		self.tickets.append(ticket)
		return ticket

	def test_required_fields_raise_per_field_errors(self):
		"""STG-321 / STG-328: missing fields map to clear per-field details."""
		with self.assertRaises(PydanticValidationError) as ctx:
			validate_required_submission_fields(
				{
					"submitter_type": "Individual Farmer",
					"submitter_name": "Abebe",
					"contact_mobile": "+251911234567",
					# administrative_area / service_category / etc. omitted
				}
			)
		locs = {".".join(str(p) for p in err["loc"]) for err in ctx.exception.errors()}
		self.assertIn("administrative_area", locs)
		self.assertIn("service_category", locs)
		self.assertIn("grievance_type", locs)
		self.assertIn("description", locs)
		self.assertIn("submission_channel", locs)
		self.assertIn("consent_given", locs)

	def test_submit_returns_ticket_and_leaves_draft(self):
		frappe.set_user(self.user.name)
		result = submit(
			submission_channel="Mobile App",
			administrative_area=self.area,
			service_category="Inputs",
			grievance_type="Fertilizer Shortage",
			description="Fertilizer voucher delayed past planting window in Kebele 01.",
			consent_given=1,
		)
		self.assertEqual(result["status"], "success")
		ticket = self._track(result)
		self.assertTrue(ticket)
		self.assertNotEqual(result["data"]["status"], C.DRAFT)
		self.assertEqual(result["data"]["status"], C.SUBMITTED)

		doc = frappe.get_doc("Grievance", ticket)
		self.assertEqual(doc.status, C.SUBMITTED)
		self.assertEqual(doc.workflow_state, C.SUBMITTED)
		self.assertEqual(doc.ticket_number, ticket)
		self.assertEqual(doc.description, "Fertilizer voucher delayed past planting window in Kebele 01.")

	def test_draft_payload_carries_into_submitted_record(self):
		"""STG-325 resume → STG-328 submit: draft fields become the case."""
		frappe.set_user(self.user.name)
		client_uuid = frappe.generate_hash(length=20)
		draft_description = "Drafted complaint about delayed fertilizer distribution vouchers."
		draft.save(
			client_uuid=client_uuid,
			payload={
				"submission_channel": "Mobile App",
				"administrative_area": self.area,
				"service_category": "Inputs",
				"grievance_type": "Fertilizer Shortage",
				"description": draft_description,
				"desired_outcome": "Vouchers issued before rains.",
				"consent_given": 1,
			},
			step_reached=4,
		)

		# Client sends client_uuid plus only an overlay correction.
		result = submit(
			client_uuid=client_uuid,
			desired_outcome="Immediate voucher disbursement.",
		)
		self.assertEqual(result["status"], "success")
		ticket = self._track(result)
		self.assertEqual(result["data"]["status"], C.SUBMITTED)

		doc = frappe.get_doc("Grievance", ticket)
		self.assertEqual(doc.description, draft_description)
		self.assertEqual(doc.desired_outcome, "Immediate voucher disbursement.")
		self.assertEqual(doc.administrative_area, self.area)
		self.assertEqual(doc.service_category, "Inputs")

		draft_row = frappe.db.get_value(
			"Grievance Draft",
			{"client_uuid": client_uuid},
			["submitted_as", "owner_user"],
			as_dict=True,
		)
		self.assertEqual(draft_row.submitted_as, doc.name)
		self.assertEqual(draft_row.owner_user, self.user.name)

	def test_resubmitting_claimed_draft_returns_original_ticket(self):
		frappe.set_user(self.user.name)
		client_uuid = frappe.generate_hash(length=20)
		draft.save(
			client_uuid=client_uuid,
			payload={
				"submission_channel": "Mobile App",
				"administrative_area": self.area,
				"service_category": "Inputs",
				"grievance_type": "Fertilizer Shortage",
				"description": "First submission from a saved offline wizard draft.",
				"consent_given": 1,
			},
			step_reached=4,
		)
		first = submit(client_uuid=client_uuid)
		ticket = self._track(first)

		second = submit(client_uuid=client_uuid)
		self.assertEqual(second["status"], "success")
		self.assertTrue(second["data"]["duplicate_submission"])
		self.assertEqual(second["data"]["ticket_number"], ticket)

	def test_submit_rejects_missing_consent_with_field_error(self):
		frappe.set_user(self.user.name)
		# @handle_api_errors turns PydanticValidationError into the standard envelope.
		result = submit(
			submission_channel="Mobile App",
			administrative_area=self.area,
			service_category="Inputs",
			grievance_type="Fertilizer Shortage",
			description="Enough characters for the description minimum length.",
			consent_given=0,
		)
		self.assertEqual(result["status"], "error")
		self.assertEqual(result["code"], "VALIDATION_ERROR")
		self.assertIn("consent_given", result.get("details") or {})

	def test_guest_cannot_submit(self):
		frappe.set_user("Guest")
		result = submit(
			submission_channel="Mobile App",
			administrative_area=self.area,
			service_category="Inputs",
			grievance_type="Fertilizer Shortage",
			description="Guest attempt should be blocked by role guard before insert.",
			consent_given=1,
		)
		self.assertEqual(result["status"], "error")
		self.assertEqual(result["code"], "PERMISSION_DENIED")
