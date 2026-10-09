"""Tests for the Werkzeug REST Router in OAN Grievance Service."""

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from oan_auth_service.api.utils import _resolve_version_meta
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request, Response

from oan_grievance_service.api.router import ensure_routes_registered
from oan_grievance_service.api.v1 import administrative_area, draft, grievance, profile, submitter
from oan_grievance_service.services import ticket_number as tn
from oan_grievance_service.tests.fixtures import a_leaf_area


def make_test_request(
	path: str,
	method: str = "GET",
	data: dict | None = None,
	headers: dict | None = None,
	scheme: str = "http",
	environ_base: dict | None = None,
) -> Request:
	"""Helper to construct a Werkzeug Request and set up frappe.local state."""
	query_string = None
	if "?" in path:
		path, query_string = path.split("?", 1)

	builder_kwargs = {
		"path": path,
		"method": method.upper(),
		"base_url": f"{scheme}://testsite.localhost",
		"headers": headers or {},
	}
	if query_string:
		builder_kwargs["query_string"] = query_string
	if data is not None:
		builder_kwargs["json"] = data

	builder = EnvironBuilder(**builder_kwargs)
	env = builder.get_environ()
	if environ_base:
		env.update(environ_base)
	req = Request(env)

	frappe.local.request = req
	frappe.local.request_ip = "127.0.0.1"
	form_data = dict(req.args)
	if data:
		form_data.update(data)
	frappe.local.form_dict = frappe._dict(form_data)
	frappe.local.response = frappe._dict({})

	return req


class TestGrievanceRESTRouter(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_routes_registered()

	def _ensure_fixtures(self):
		frappe.set_user("Administrator")

		# Ensure required roles exist
		for role in ("Grievance Submitter", "Grievance Officer", "Grievance Admin"):
			if not frappe.db.exists("Role", role):
				frappe.get_doc({"doctype": "Role", "role_name": role}).insert(ignore_permissions=True)

		if not frappe.db.exists("Grievance Submitter Type", "Individual Farmer"):
			frappe.get_doc(
				{"doctype": "Grievance Submitter Type", "type_name": "Individual Farmer", "code": "IND"}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("Grievance Service Category", "Inputs"):
			frappe.get_doc(
				{
					"doctype": "Grievance Service Category",
					"category_name": "Inputs",
					"code": "001",
					"is_active": 1,
				}
			).insert(ignore_permissions=True)
		else:
			frappe.db.set_value("Grievance Service Category", "Inputs", "code", "001")

		if not frappe.db.exists("Grievance Type", {"type_name": "Fertilizer Shortage"}):
			self.gtype = frappe.get_doc(
				{
					"doctype": "Grievance Type",
					"type_name": "Fertilizer Shortage",
					"service_category": "Inputs",
					"is_active": 1,
				}
			).insert(ignore_permissions=True)
		else:
			gtype_name = frappe.db.get_value("Grievance Type", {"type_name": "Fertilizer Shortage"}, "name")
			self.gtype = frappe.get_doc("Grievance Type", gtype_name)

		if not frappe.db.exists("Grievance SLA Configuration", {"service_category": "Inputs", "active": 1}):
			frappe.get_doc(
				{
					"doctype": "Grievance SLA Configuration",
					"service_category": "Inputs",
					"sla_days": 15,
					"active": 1,
				}
			).insert(ignore_permissions=True)

		self.area = a_leaf_area()

		# Create a test farmer user and profile
		self.farmer_user = frappe.db.get_value("User", {"email": "rest_farmer@test.org"}, "*")
		if not self.farmer_user:
			self.farmer_user = frappe.get_doc(
				{
					"doctype": "User",
					"email": "rest_farmer@test.org",
					"first_name": "REST Farmer",
					"roles": [{"role": "Grievance Submitter"}],
				}
			).insert(ignore_permissions=True)
		else:
			self.farmer_user = frappe.get_doc("User", self.farmer_user.name)

		profile_name = frappe.db.get_value(
			"Grievance Submitter Profile",
			{"user": self.farmer_user.name},
			"name",
		) or frappe.db.get_value(
			"Grievance Submitter Profile",
			{"dedupe_key": "phone:+251911998877"},
			"name",
		)
		if not profile_name:
			self.farmer_profile = frappe.get_doc(
				{
					"doctype": "Grievance Submitter Profile",
					"submitter_type": "Individual Farmer",
					"submitter_name": "REST Test Submitter",
					"contact_mobile": "+251911998877",
					"user": self.farmer_user.name,
				}
			).insert(ignore_permissions=True)
		else:
			self.farmer_profile = frappe.get_doc("Grievance Submitter Profile", profile_name)
			if self.farmer_profile.user != self.farmer_user.name:
				self.farmer_profile.user = self.farmer_user.name
				self.farmer_profile.save(ignore_permissions=True)

	def setUp(self):
		super().setUp()
		self._ensure_fixtures()
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def test_version_meta_isolation(self):
		"""Verify that version_meta resolves from oan_grievance_service and not oan_auth_service."""
		meta_grv = _resolve_version_meta(draft.submit_draft)
		self.assertEqual(meta_grv["api_version"], "v1")
		self.assertEqual(meta_grv["status"], "current")

		meta_sub = _resolve_version_meta(submitter.options)
		self.assertEqual(meta_sub["api_version"], "v1")

		meta_area = _resolve_version_meta(administrative_area.get_areas)
		self.assertEqual(meta_area["api_version"], "v1")

	def test_public_health_and_ping_endpoints(self):
		"""Test GET /api/v1/grievances/health and /ping."""
		import frappe.api

		req = make_test_request("/api/v1/grievances/health", method="GET")
		res = frappe.api.handle(req)
		self.assertEqual(res.status_code, 200)
		data = json.loads(res.get_data(as_text=True))
		self.assertEqual(data["status"], "success")
		self.assertEqual(data["data"]["service"], "oan_grievance_service")
		self.assertEqual(data["data"]["status"], "healthy")

		req_ping = make_test_request("/api/v1/grievances/ping", method="GET")
		res_ping = frappe.api.handle(req_ping)
		self.assertEqual(res_ping.status_code, 200)
		data_ping = json.loads(res_ping.get_data(as_text=True))
		self.assertEqual(data_ping["data"]["ping"], "pong")

	def test_public_submitter_options_endpoint(self):
		"""Test GET /api/v1/submitters/options is accessible as guest."""
		import frappe.api

		req = make_test_request("/api/v1/submitters/options", method="GET")
		frappe.set_user("Guest")
		res = frappe.api.handle(req)
		self.assertEqual(res.status_code, 200)
		data = json.loads(res.get_data(as_text=True))
		self.assertEqual(data["status"], "success")
		self.assertIn("submitter_types", data["data"])
		self.assertIn("identity_schemes", data["data"])
		self.assertIn("submission_types", data["data"])

	def test_public_administrative_area_endpoints(self):
		"""Test GET /api/v1/administrative-areas."""
		import frappe.api

		req = make_test_request("/api/v1/administrative-areas", method="GET")
		frappe.set_user("Guest")
		res = frappe.api.handle(req)
		self.assertEqual(res.status_code, 200)
		data = json.loads(res.get_data(as_text=True))
		self.assertEqual(data["status"], "success")
		self.assertIn("areas", data["data"])

	def test_grievance_options_endpoint(self):
		"""Test GET /api/v1/grievances/options and cascading department officers."""
		import frappe.api

		from oan_grievance_service.tests.fixtures import a_department

		frappe.set_user("Administrator")
		# 1. Base options without department
		req = make_test_request("/api/v1/grievances/options", method="GET")
		res = frappe.api.handle(req)
		self.assertEqual(res.status_code, 200)
		data = json.loads(res.get_data(as_text=True))
		self.assertEqual(data["status"], "success")
		self.assertIn("departments", data["data"])
		self.assertIn("statuses", data["data"])
		self.assertIn("service_categories", data["data"])
		self.assertNotIn("officers", data["data"])

		# 2. Cascading options with department
		dept = a_department()
		if not frappe.db.exists("Grievance Role Level", "nodal_officer"):
			frappe.get_doc(
				{
					"doctype": "Grievance Role Level",
					"level_code": "nodal_officer",
					"level_name": "Nodal Officer",
					"level_order": 10,
					"is_active": 1,
				}
			).insert(ignore_permissions=True)

		# Create an RBAC assignment for this department
		desk = frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": dept,
				"active": 1,
				"effective_from": frappe.utils.today(),
				"officers": [
					{
						"user": "Administrator",
						"role_level": "nodal_officer",
						"is_primary": 1,
						"active": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		try:
			req_dept = make_test_request(f"/api/v1/grievances/options?department={dept}", method="GET")
			res_dept = frappe.api.handle(req_dept)
			self.assertEqual(res_dept.status_code, 200)
			data_dept = json.loads(res_dept.get_data(as_text=True))
			self.assertIn("officers", data_dept["data"])
			officers = data_dept["data"]["officers"]
			# The caller is never offered to themselves
			self.assertFalse(any(o["user_id"] == "Administrator" for o in officers))

			# 3. Cascading options with department and service_category
			req_cat = make_test_request(
				f"/api/v1/grievances/options?department={dept}&service_category=Inputs", method="GET"
			)
			res_cat = frappe.api.handle(req_cat)
			self.assertEqual(res_cat.status_code, 200)
			data_cat = json.loads(res_cat.get_data(as_text=True))
			self.assertIn("officers", data_cat["data"])
		finally:
			frappe.delete_doc("Grievance RBAC Assignment", desk.name, force=True, ignore_permissions=True)

	def test_auth_me_returns_namespaced_grievance_profile(self):
		"""Test GET /api/v1/auth/me enriches data.profiles.grievance via on_user_profile hook."""
		import frappe.api

		# 1. Test Submitter profile retrieval
		frappe.set_user(self.farmer_user.name)
		req = make_test_request("/api/v1/auth/me", method="GET")
		res = frappe.api.handle(req)
		self.assertEqual(res.status_code, 200)
		data = json.loads(res.get_data(as_text=True))
		self.assertEqual(data["status"], "success")
		self.assertIn("profiles", data["data"])
		self.assertIn("grievance", data["data"]["profiles"])
		grv_profile = data["data"]["profiles"]["grievance"]
		self.assertEqual(grv_profile["profile_id"], self.farmer_profile.name)
		self.assertEqual(grv_profile["type"], "Individual Farmer")
		self.assertIsInstance(grv_profile["identities"], list)

		# 2. Test Admin without officer scope or submitter profile returns no grievance profile
		frappe.set_user("Administrator")
		req_admin = make_test_request("/api/v1/auth/me", method="GET")
		res_admin = frappe.api.handle(req_admin)
		self.assertEqual(res_admin.status_code, 200)
		data_admin = json.loads(res_admin.get_data(as_text=True))
		self.assertEqual(data_admin["status"], "success")
		self.assertNotIn("grievance", data_admin["data"].get("profiles", {}))

	def test_timeline_pagination(self):
		"""Test timeline cursor pagination works for multiple pages without breaking."""
		import json

		import frappe
		import frappe.api

		from oan_grievance_service.api.v1.grievance import message
		from oan_grievance_service.tests.fixtures import a_grievance

		# 1. Create a grievance
		doc = a_grievance()
		frappe.set_user("Administrator")
		ticket_number = doc.ticket_number or doc.name

		# 2. Add multiple messages to create timeline entries
		for i in range(5):
			message(ticket_number=ticket_number, body=f"Message {i}")

		# 3. Fetch first page with limit=2
		req1 = make_test_request(f"/api/v1/grievances/{ticket_number}/timeline?limit=2", method="GET")
		res1 = frappe.api.handle(req1)
		self.assertEqual(res1.status_code, 200)
		data1 = json.loads(res1.get_data(as_text=True))["data"]
		self.assertTrue(data1["has_more"])
		self.assertIsNotNone(data1["next_cursor"])
		self.assertEqual(len(data1["timeline"]), 2)

		# 4. Fetch second page with cursor
		import urllib.parse

		cursor = data1["next_cursor"]
		req2 = make_test_request(
			f"/api/v1/grievances/{ticket_number}/timeline?limit=2&cursor={urllib.parse.quote(cursor)}",
			method="GET",
		)
		res2 = frappe.api.handle(req2)
		self.assertEqual(res2.status_code, 200)
		data2 = json.loads(res2.get_data(as_text=True))["data"]
		self.assertEqual(len(data2["timeline"]), 2)

		# 5. Verify timeline entries are in ASC (chronological) order
		self.assertLessEqual(data1["timeline"][0]["created_on"], data1["timeline"][1]["created_on"])
		page1_ids = {e["name"] for e in data1["timeline"]}
		page2_ids = {e["name"] for e in data2["timeline"]}
		self.assertTrue(page1_ids.isdisjoint(page2_ids))
		self.assertLessEqual(data1["timeline"][-1]["created_on"], data2["timeline"][0]["created_on"])

	def test_message_idempotency(self):
		"""Test that sending client_message_id prevents duplicate message insertion."""
		import json
		import uuid

		import frappe
		import frappe.api

		from oan_grievance_service.tests.fixtures import a_grievance

		doc = a_grievance()
		frappe.set_user("Administrator")
		ticket_number = doc.ticket_number or doc.name

		client_msg_id = f"client-msg-{uuid.uuid4()}"
		payload = {
			"body": "First message attempt",
			"client_message_id": client_msg_id,
		}

		# Send message 1st time
		req1 = make_test_request(f"/api/v1/grievances/{ticket_number}/message", method="POST", data=payload)
		res1 = frappe.api.handle(req1)
		self.assertEqual(res1.status_code, 200)
		data1 = json.loads(res1.get_data(as_text=True))["data"]
		entry1_name = data1["name"]

		# Send message 2nd time with exact same client_message_id
		req2 = make_test_request(f"/api/v1/grievances/{ticket_number}/message", method="POST", data=payload)
		res2 = frappe.api.handle(req2)
		self.assertEqual(res2.status_code, 200)
		data2 = json.loads(res2.get_data(as_text=True))["data"]
		entry2_name = data2["name"]

		# Must return the same existing record, not create a duplicate
		self.assertEqual(entry1_name, entry2_name)
		total_matching = frappe.db.count(
			"Grievance Timeline",
			filters={"grievance": doc.name, "client_message_id": client_msg_id},
		)
		self.assertEqual(total_matching, 1)

		# Reusing the same client_message_id on a DIFFERENT case creates a new entry on that case
		doc2 = a_grievance()
		ticket2 = doc2.ticket_number or doc2.name
		payload_other = {
			"body": "Message on second case with same client_message_id",
			"client_message_id": client_msg_id,
		}
		req_other = make_test_request(
			f"/api/v1/grievances/{ticket2}/message", method="POST", data=payload_other
		)
		res_other = frappe.api.handle(req_other)
		self.assertEqual(res_other.status_code, 200)
		data_other = json.loads(res_other.get_data(as_text=True))["data"]
		self.assertNotEqual(data_other["name"], entry1_name)
		self.assertEqual(
			frappe.db.get_value("Grievance Timeline", data_other["name"], "grievance"), doc2.name
		)

		# Sending client_message_id longer than 64 characters is refused (400)
		payload_too_long = {
			"body": "Message with overlong key",
			"client_message_id": "a" * 65,
		}
		req_long = make_test_request(
			f"/api/v1/grievances/{ticket_number}/message", method="POST", data=payload_too_long
		)
		res_long = frappe.api.handle(req_long)
		self.assertEqual(res_long.status_code, 400)
		body_long = json.loads(res_long.get_data(as_text=True))
		self.assertEqual(body_long["code"], "VALIDATION_ERROR")

	def test_grievance_submission_tracking_and_timeline_rest_flow(self):
		"""Test complete REST workflow: submit, track, add note, and timeline."""
		import uuid

		import frappe.api

		# 1. Save and submit a grievance draft via REST
		frappe.set_user(self.farmer_user.name)
		draft_uuid = str(uuid.uuid4())
		save_payload = {
			"client_submission_uuid": draft_uuid,
			"submission_channel": "Mobile App",
			"administrative_area": self.area,
			"service_category": "Inputs",
			"grievance_type": self.gtype.name,
			"description": "REST API submission test: severe shortage in sector 4.",
		}
		req_save = make_test_request("/api/v1/drafts", method="POST", data=save_payload)
		res_save = frappe.api.handle(req_save)
		self.assertEqual(res_save.status_code, 200)

		submit_payload = {
			"client_submission_uuid": draft_uuid,
			"consent_given": 1,
		}
		req_submit = make_test_request("/api/v1/drafts/submit", method="POST", data=submit_payload)
		res_submit = frappe.api.handle(req_submit)
		self.assertEqual(res_submit.status_code, 200)
		submit_data = json.loads(res_submit.get_data(as_text=True))
		self.assertEqual(submit_data["status"], "success")
		ticket_number = submit_data["data"]["ticket_number"]
		self.assertTrue(bool(ticket_number))

		# 2. Track status and attachments via GET /api/v1/grievances/<ticket_number>/timeline
		req_tl = make_test_request(f"/api/v1/grievances/{ticket_number}/timeline", method="GET")
		res_tl = frappe.api.handle(req_tl)
		self.assertEqual(res_tl.status_code, 200)
		tl_data = json.loads(res_tl.get_data(as_text=True))
		self.assertEqual(tl_data["data"]["ticket_number"], tn.display(ticket_number))
		self.assertIn("attachments", tl_data["data"])

		# 3. Add message as Officer via POST /api/v1/grievances/<ticket_number>/message
		frappe.set_user("Administrator")
		req_note = make_test_request(
			f"/api/v1/grievances/{ticket_number}/message",
			method="POST",
			data={"body": "Officer reviewing case via REST", "is_internal": False},
		)
		res_note = frappe.api.handle(req_note)
		self.assertEqual(res_note.status_code, 200)
		note_data = json.loads(res_note.get_data(as_text=True))
		self.assertEqual(note_data["status"], "success")

		# 4. View Timeline as Submitter via GET /api/v1/grievances/<ticket_number>/timeline
		frappe.set_user(self.farmer_user.name)
		req_tl = make_test_request(f"/api/v1/grievances/{ticket_number}/timeline", method="GET")
		res_tl = frappe.api.handle(req_tl)
		self.assertEqual(res_tl.status_code, 200)
		tl_data = json.loads(res_tl.get_data(as_text=True))
		self.assertEqual(tl_data["status"], "success")
		timeline_entries = tl_data["data"]["timeline"]
		self.assertTrue(len(timeline_entries) > 0)

		# Verify chronological ASC order
		timestamps = [e["created_on"] for e in timeline_entries]
		self.assertEqual(timestamps, sorted(timestamps))

		for entry in timeline_entries:
			self.assertNotIn("tags", entry)
			self.assertNotIn("author_user", entry)
			self.assertNotIn("ref_docname", entry)
			self.assertNotIn("ref_doctype", entry)
			if entry.get("author_type") == "officer":
				# Submitter cannot see officer name: key must be completely omitted
				self.assertNotIn("author_name", entry)
				self.assertTrue(bool(entry.get("author_role")))
			elif entry.get("author_type") == "submitter":
				# Submitter can see their own name
				self.assertIn("author_name", entry)
		self.assertIsNone(tl_data["data"]["assignment"]["assigned_to"])

		# 5. View Timeline as Staff: officer author_name is visible, author_user is excluded
		frappe.set_user("Administrator")
		req_tl_admin = make_test_request(f"/api/v1/grievances/{ticket_number}/timeline", method="GET")
		res_tl_admin = frappe.api.handle(req_tl_admin)
		tl_admin_data = json.loads(res_tl_admin.get_data(as_text=True))
		admin_entries = tl_admin_data["data"]["timeline"]
		officer_entry = next((e for e in admin_entries if e.get("author_type") == "officer"), None)
		self.assertIsNotNone(officer_entry)
		self.assertNotIn("author_user", officer_entry)
		self.assertIn("author_name", officer_entry)
		self.assertEqual(officer_entry["author_name"], "Administrator")
		self.assertEqual(officer_entry["entry_type"], "dept_response")
		self.assertEqual(officer_entry["response_number"], 1)
		self.assertNotIn("ref_docname", officer_entry)

	def test_unified_action_rest_endpoint(self):
		"""Test POST /api/v1/grievances/<ticket_number>/action for workflow moves."""
		import uuid

		import frappe.api

		from oan_grievance_service.services import lifecycle

		# 1. Save and submit a grievance draft via REST
		frappe.set_user(self.farmer_user.name)
		draft_uuid = str(uuid.uuid4())
		save_payload = {
			"client_submission_uuid": draft_uuid,
			"submission_channel": "Mobile App",
			"administrative_area": self.area,
			"service_category": "Inputs",
			"grievance_type": self.gtype.name,
			"description": "Unified action endpoint test with minimum length.",
		}
		req_save = make_test_request("/api/v1/drafts", method="POST", data=save_payload)
		res_save = frappe.api.handle(req_save)
		self.assertEqual(res_save.status_code, 200)

		submit_payload = {
			"client_submission_uuid": draft_uuid,
			"consent_given": 1,
		}
		req_submit = make_test_request("/api/v1/drafts/submit", method="POST", data=submit_payload)
		res_submit = frappe.api.handle(req_submit)
		ticket_number = json.loads(res_submit.get_data(as_text=True))["data"]["ticket_number"]

		# 2. As submitter, check timeline returns available_actions
		req_tl = make_test_request(f"/api/v1/grievances/{ticket_number}/timeline", method="GET")
		res_tl = frappe.api.handle(req_tl)
		tl_data = json.loads(res_tl.get_data(as_text=True))["data"]
		self.assertIn("available_actions", tl_data)

		# 3. Attempting direct 'Assign' action via generic action endpoint is refused
		frappe.set_user("Administrator")
		req_assign_bad = make_test_request(
			f"/api/v1/grievances/{ticket_number}/action",
			method="POST",
			data={"action": "Assign"},
		)
		res_assign_bad = frappe.api.handle(req_assign_bad)
		body_assign_bad = json.loads(res_assign_bad.get_data(as_text=True))
		self.assertEqual(body_assign_bad["status"], "error")
		self.assertIn("not permitted", body_assign_bad["message"].lower())

		from oan_grievance_service.tests.fixtures import a_department, a_grievance

		case_bad = a_grievance(assigned_dept=a_department())
		lifecycle.transition(case_bad, "Assign", automated=True)

		# Execute In Progress without reason is refused (400)
		req_action_bad = make_test_request(
			f"/api/v1/grievances/{case_bad.ticket_number or case_bad.name}/action",
			method="POST",
			data={"action": "In Progress"},
		)
		res_action_bad = frappe.api.handle(req_action_bad)
		body_action_bad = json.loads(res_action_bad.get_data(as_text=True))
		self.assertEqual(body_action_bad["status"], "error")
		self.assertIn("reason is required", body_action_bad["message"].lower())

		# Execute In Progress via unified action endpoint with reason
		case = a_grievance(assigned_dept=a_department())
		lifecycle.transition(case, "Assign", automated=True)
		req_action = make_test_request(
			f"/api/v1/grievances/{case.ticket_number or case.name}/action",
			method="POST",
			data={"action": "In Progress", "reason": "Officer commenced case review."},
		)
		res_action = frappe.api.handle(req_action)
		self.assertEqual(res_action.status_code, 200, res_action.get_data(as_text=True))
		action_data = json.loads(res_action.get_data(as_text=True))
		self.assertEqual(action_data["status"], "success")
		self.assertEqual(action_data["data"]["status"], "In Progress")

		# Verify timeline formatting: entry is status_change and has no response_number
		req_tl_check = make_test_request(
			f"/api/v1/grievances/{case.ticket_number or case.name}/timeline", method="GET"
		)
		res_tl_check = frappe.api.handle(req_tl_check)
		tl_events = json.loads(res_tl_check.get_data(as_text=True))["data"]["timeline"]
		in_prog_entry = next((e for e in tl_events if e.get("action") == "In Progress"), None)
		self.assertIsNotNone(in_prog_entry)
		self.assertEqual(in_prog_entry["entry_type"], "status_change")
		self.assertNotIn("response_number", in_prog_entry)

		# Attempting an unavailable action is refused
		req_resp_bad = make_test_request(
			f"/api/v1/grievances/{case.ticket_number or case.name}/action",
			method="POST",
			data={"action": "Submit"},
		)
		res_resp_bad = frappe.api.handle(req_resp_bad)
		body_resp_bad = json.loads(res_resp_bad.get_data(as_text=True))
		self.assertEqual(body_resp_bad["status"], "error")
		self.assertIn("not available", body_resp_bad["message"].lower())

		# 4. Reject without reason is refused (400)
		rej_case = a_grievance()
		req_rej_bad = make_test_request(
			f"/api/v1/grievances/{rej_case.ticket_number or rej_case.name}/action",
			method="POST",
			data={"action": "Reject", "reason": ""},
		)
		res_rej_bad = frappe.api.handle(req_rej_bad)
		body_rej_bad = json.loads(res_rej_bad.get_data(as_text=True))
		self.assertEqual(body_rej_bad["status"], "error")
		self.assertIn("reason is required", body_rej_bad["message"].lower())

		# 5. Reject with reason succeeds
		rej_case_2 = a_grievance()
		req_rej_ok = make_test_request(
			f"/api/v1/grievances/{rej_case_2.ticket_number or rej_case_2.name}/action",
			method="POST",
			data={"action": "Reject", "reason": "Not an agricultural grievance."},
		)
		res_rej_ok = frappe.api.handle(req_rej_ok)
		self.assertEqual(res_rej_ok.status_code, 200)
		action_rej = json.loads(res_rej_ok.get_data(as_text=True))
		self.assertEqual(action_rej["data"]["status"], "Rejected")

		# 6. Action with note and internal_notes populates status history notes and internal timeline
		rej_case_3 = a_grievance()
		req_rej_notes = make_test_request(
			f"/api/v1/grievances/{rej_case_3.ticket_number or rej_case_3.name}/action",
			method="POST",
			data={
				"action": "Reject",
				"reason": "Out of scope",
				"note": "Audit note on status change",
				"internal_notes": "Private officer timeline note",
			},
		)
		res_rej_notes = frappe.api.handle(req_rej_notes)
		self.assertEqual(res_rej_notes.status_code, 200)
		hist_row = frappe.get_all(
			"Grievance Status History",
			filters={"grievance": rej_case_3.name},
			fields=["reason", "notes"],
			order_by="creation desc",
			limit=1,
		)[0]
		self.assertEqual(hist_row["reason"], "Out of scope")
		self.assertEqual(hist_row["notes"], "Audit note on status change")
		internal_tl = frappe.get_all(
			"Grievance Timeline",
			filters={"grievance": rej_case_3.name, "is_internal": 1},
			fields=["body", "entry_type"],
		)
		self.assertEqual(len(internal_tl), 1)
		self.assertEqual(internal_tl[0]["body"], "Private officer timeline note")

		# 7. Resolve without reason is refused (400)
		res_case = a_grievance(assigned_dept=a_department())
		lifecycle.transition(res_case, "Assign", automated=True)
		lifecycle.transition(res_case, "In Progress", reason="Starting investigation.")
		req_res_bad = make_test_request(
			f"/api/v1/grievances/{res_case.ticket_number or res_case.name}/action",
			method="POST",
			data={"action": "Resolve"},
		)
		res_res_bad = frappe.api.handle(req_res_bad)
		body_res_bad = json.loads(res_res_bad.get_data(as_text=True))
		self.assertEqual(body_res_bad["status"], "error")
		self.assertIn("reason is required", body_res_bad["message"].lower())

		# 8. Resolve with incomplete two-part resolution is refused (400)
		res_case_2 = a_grievance(assigned_dept=a_department())
		lifecycle.transition(res_case_2, "Assign", automated=True)
		lifecycle.transition(res_case_2, "In Progress", reason="Starting investigation.")
		req_res_incomplete = make_test_request(
			f"/api/v1/grievances/{res_case_2.ticket_number or res_case_2.name}/action",
			method="POST",
			data={"action": "Resolve", "action_taken": "Inspected crop damage"},
		)
		res_res_incomplete = frappe.api.handle(req_res_incomplete)
		body_res_incomplete = json.loads(res_res_incomplete.get_data(as_text=True))
		self.assertEqual(body_res_incomplete["status"], "error")
		self.assertEqual(body_res_incomplete["code"], "VALIDATION_ERROR")
		self.assertIn("action_taken and resolution_summary", str(body_res_incomplete["details"]))

		# 9. Resolve with complete two-part resolution succeeds
		req_res_twopart = make_test_request(
			f"/api/v1/grievances/{res_case_2.ticket_number or res_case_2.name}/action",
			method="POST",
			data={
				"action": "Resolve",
				"action_taken": "Inspected crop damage and processed subsidy payout.",
				"resolution_summary": "Subsidy credited to farmer account.",
			},
		)
		res_res_twopart = frappe.api.handle(req_res_twopart)
		self.assertEqual(res_res_twopart.status_code, 200)
		body_res_twopart = json.loads(res_res_twopart.get_data(as_text=True))
		self.assertEqual(body_res_twopart["data"]["status"], "Resolved")

	def test_available_actions_gated_by_write_permission(self):
		"""Verify available_actions is empty for users lacking write permission on the case."""
		from oan_grievance_service.api.v1.grievance import _get_available_actions_for_user
		from oan_grievance_service.tests.fixtures import a_grievance

		doc = a_grievance()

		# Administrator has write permission -> available_actions has entries
		frappe.set_user("Administrator")
		req_admin = make_test_request(f"/api/v1/grievances/{doc.ticket_number}/timeline", method="GET")
		res_admin = frappe.api.handle(req_admin)
		self.assertEqual(res_admin.status_code, 200)
		data_admin = json.loads(res_admin.get_data(as_text=True))["data"]
		self.assertTrue(len(data_admin["available_actions"]) > 0)
		self.assertTrue(len(_get_available_actions_for_user(doc)) > 0)

		# User without write permission -> available_actions must be empty []
		other_user = "unauthorized_viewer@test.org"
		if not frappe.db.exists("User", other_user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": other_user,
					"first_name": "Other User",
					"roles": [{"role": "Grievance Submitter"}],
				}
			).insert(ignore_permissions=True)
		frappe.set_user(other_user)
		self.assertEqual(_get_available_actions_for_user(doc), [])

	def test_reassign_and_defer_endpoints(self):
		"""Test direct REST APIs for reassignment and deferral, and verify submitted anonymous case."""
		import uuid

		from oan_grievance_service.services import lifecycle
		from oan_grievance_service.tests.fixtures import a_department

		# 1. Submit a grievance
		frappe.set_user(self.farmer_user.name)
		draft_uuid = str(uuid.uuid4())
		save_payload = {
			"client_submission_uuid": draft_uuid,
			"submission_channel": "Mobile App",
			"administrative_area": self.area,
			"service_category": "Inputs",
			"grievance_type": self.gtype.name,
			"description": "Testing reassign and deferral direct endpoints.",
		}
		req_save = make_test_request("/api/v1/drafts", method="POST", data=save_payload)
		frappe.api.handle(req_save)

		submit_payload = {
			"client_submission_uuid": draft_uuid,
			"consent_given": 1,
			"is_anonymous": 1,
			"anonymity_justification": "Need anonymity",
		}
		req_submit = make_test_request("/api/v1/drafts/submit", method="POST", data=submit_payload)
		res_submit = frappe.api.handle(req_submit)
		self.assertEqual(res_submit.status_code, 200, res_submit.get_data(as_text=True))
		ticket_number = json.loads(res_submit.get_data(as_text=True))["data"]["ticket_number"]

		# 2. Reassign endpoint
		frappe.set_user("Administrator")
		officer_email = "router_officer@test.org"
		if not frappe.db.exists("User", officer_email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": officer_email,
					"first_name": "Router Officer",
					"roles": [{"role": "Grievance Officer"}],
				}
			).insert(ignore_permissions=True)

		reassign_dept_name = f"Router Reassign Dept {uuid.uuid4().hex[:6]}"
		reassign_dept = frappe.get_doc(
			{
				"doctype": "Grievance Department",
				"dept_name": reassign_dept_name,
				"email_account": "router_reassign@example.com",
				"active": 1,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc,
			"Grievance Department",
			reassign_dept.name,
			force=True,
			ignore_permissions=True,
		)

		rbac_doc = frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": reassign_dept.name,
				"category_scope": "Inputs",
				"active": 1,
				"effective_from": frappe.utils.today(),
				"officers": [
					{
						"user": officer_email,
						"role_level": "nodal_officer",
						"is_primary": 1,
						"active": 1,
					}
				],
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc,
			"Grievance RBAC Assignment",
			rbac_doc.name,
			force=True,
			ignore_permissions=True,
		)

		req_reassign = make_test_request(
			f"/api/v1/grievances/{ticket_number}/reassign",
			method="POST",
			data={
				"target_department": reassign_dept.name,
				"reason": "Routing to regional dept",
			},
		)
		res_reassign = frappe.api.handle(req_reassign)
		self.assertEqual(res_reassign.status_code, 200, res_reassign.get_data(as_text=True))
		reassign_data = json.loads(res_reassign.get_data(as_text=True))
		self.assertEqual(reassign_data["status"], "success")
		# 3. Assign case to start SLA clock, then Defer SLA endpoint
		doc_case = frappe.get_doc("Grievance", tn.normalize(ticket_number))
		lifecycle.transition(doc_case, "Assign")

		# Deferral with reason < 20 characters is refused
		req_defer_short = make_test_request(
			f"/api/v1/grievances/{ticket_number}/defer-sla",
			method="POST",
			data={"additional_days": 5, "reason": "Too short"},
		)
		res_defer_short = frappe.api.handle(req_defer_short)
		self.assertEqual(res_defer_short.status_code, 400)
		body_defer_short = json.loads(res_defer_short.get_data(as_text=True))
		self.assertEqual(body_defer_short["code"], "VALIDATION_ERROR")

		req_defer = make_test_request(
			f"/api/v1/grievances/{ticket_number}/defer-sla",
			method="POST",
			data={"additional_days": 5, "reason": "Awaiting soil lab sample results"},
		)
		res_defer = frappe.api.handle(req_defer)
		self.assertEqual(res_defer.status_code, 200, res_defer.get_data(as_text=True))
		defer_data = json.loads(res_defer.get_data(as_text=True))
		self.assertEqual(defer_data["status"], "success")
		self.assertIn("sla_due_date", defer_data["data"])

		# 4. Anonymity check: anonymous case was submitted as anonymous directly
		self.assertTrue(doc_case.is_anonymous)

		# 5. Verify serialize_public does not leak subject, reason, or unapproved dates to citizens
		frappe.set_user(self.farmer_user.name)
		req_tl_citizen = make_test_request(f"/api/v1/grievances/{ticket_number}/timeline", method="GET")
		res_tl_citizen = frappe.api.handle(req_tl_citizen)
		self.assertEqual(res_tl_citizen.status_code, 200)
		tl_citizen_data = json.loads(res_tl_citizen.get_data(as_text=True))["data"]
		pub_deferral = tl_citizen_data["sla"]["active_deferral_request"]
		if pub_deferral:
			self.assertEqual(pub_deferral["status"], "Pending")
			self.assertIsNone(pub_deferral.get("approved_due_date"))
			self.assertNotIn("subject", pub_deferral)
			self.assertNotIn("reason", pub_deferral)
			self.assertNotIn("name", pub_deferral)
			self.assertNotIn("requested_at", pub_deferral)
			self.assertNotIn("decided_at", pub_deferral)
			self.assertNotIn("changes", pub_deferral)

		# 6. Verify _current_state masks assigned_to for non-staff and reveals it for staff
		from oan_grievance_service.api.v1.grievance import _current_state

		frappe.set_user(self.farmer_user.name)
		state_citizen = _current_state(doc_case)
		self.assertIsNone(state_citizen["assigned_to"])

		frappe.set_user("Administrator")
		state_staff = _current_state(doc_case)
		self.assertEqual(state_staff["assigned_to"], doc_case.assigned_to)

	def test_unified_message_and_department_response_endpoint(self):
		"""Test unified POST /api/v1/grievances/<ticket>/message for notes, messages, info requests, and department responses."""
		import uuid

		import frappe.api

		from oan_grievance_service.services import lifecycle
		from oan_grievance_service.tests.fixtures import a_department

		# 1. Citizen submits grievance
		frappe.set_user(self.farmer_user.name)
		draft_uuid = str(uuid.uuid4())
		save_payload = {
			"client_submission_uuid": draft_uuid,
			"submission_channel": "Mobile App",
			"administrative_area": self.area,
			"service_category": "Inputs",
			"grievance_type": self.gtype.name,
			"description": "Testing unified communication and department response endpoint.",
		}
		req_save = make_test_request("/api/v1/drafts", method="POST", data=save_payload)
		frappe.api.handle(req_save)

		submit_payload = {"client_submission_uuid": draft_uuid, "consent_given": 1}
		req_submit = make_test_request("/api/v1/drafts/submit", method="POST", data=submit_payload)
		res_submit = frappe.api.handle(req_submit)
		ticket_number = json.loads(res_submit.get_data(as_text=True))["data"]["ticket_number"]

		# Assign and In Progress
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Grievance", tn.normalize(ticket_number))
		doc.assigned_dept = a_department()
		doc.save(ignore_permissions=True)
		lifecycle.transition(doc, "Assign")
		lifecycle.transition(doc, "In Progress", reason="Starting investigation.")

		# 2. Staff posts an internal note
		req_note = make_test_request(
			f"/api/v1/grievances/{ticket_number}/message",
			method="POST",
			data={"body": "Internal investigation note", "is_internal": True},
		)
		res_note = frappe.api.handle(req_note)
		self.assertEqual(res_note.status_code, 200)
		note_data = json.loads(res_note.get_data(as_text=True))
		self.assertEqual(note_data["data"]["entry_type"], "note")
		self.assertTrue(note_data["data"]["is_internal"])

		# 3. Staff posts an information request -> moves state to More Info Needed
		req_req_info = make_test_request(
			f"/api/v1/grievances/{ticket_number}/action",
			method="POST",
			data={"action": "Request More Info", "reason": "Please provide proof of purchase."},
		)
		res_req_info = frappe.api.handle(req_req_info)
		self.assertEqual(res_req_info.status_code, 200, res_req_info.get_data(as_text=True))
		info_req_data = json.loads(res_req_info.get_data(as_text=True))
		self.assertEqual(info_req_data["data"]["status"], "More Info Needed")

		# 4. Citizen replies -> automatically moves state back to In Progress
		frappe.set_user(self.farmer_user.name)
		req_reply = make_test_request(
			f"/api/v1/grievances/{ticket_number}/action",
			method="POST",
			data={"action": "Submitter Reply", "reason": "Receipt number is RCP-998811."},
		)
		res_reply = frappe.api.handle(req_reply)
		self.assertEqual(res_reply.status_code, 200)
		reply_data = json.loads(res_reply.get_data(as_text=True))
		self.assertEqual(reply_data["data"]["status"], "In Progress")

		# 5. Staff posts a public message
		frappe.set_user("Administrator")
		req_msg = make_test_request(
			f"/api/v1/grievances/{ticket_number}/message",
			method="POST",
			data={"body": "We have verified your receipt and are dispatching replacement seeds."},
		)
		res_msg = frappe.api.handle(req_msg)
		self.assertEqual(res_msg.status_code, 200)
		msg_data = json.loads(res_msg.get_data(as_text=True))
		self.assertEqual(msg_data["data"]["entry_type"], "message")
		self.assertFalse(msg_data["data"]["is_internal"])

		# 6. Staff resolves grievance via action endpoint
		req_resp = make_test_request(
			f"/api/v1/grievances/{ticket_number}/action",
			method="POST",
			data={
				"action": "Resolve",
				"reason": "Replacement seeds delivered to the primary warehouse.",
			},
		)
		res_resp = frappe.api.handle(req_resp)
		self.assertEqual(res_resp.status_code, 200)
		resp_data = json.loads(res_resp.get_data(as_text=True))
		self.assertEqual(resp_data["data"]["status"], "Resolved")

	def test_list_grievances_prioritizes_escalated(self):
		"""Test that GET /api/v1/grievances returns escalated cases first."""
		import uuid

		import frappe.api

		frappe.set_user("Administrator")
		test_tag = f"tag_{uuid.uuid4().hex[:8]}"

		# Create non-escalated grievance
		g_normal = frappe.get_doc(
			{
				"doctype": "Grievance",
				"submission_channel": "Mobile App",
				"submitter_type": "Individual Farmer",
				"submitter_name": f"Citizen Normal {test_tag}",
				"contact_mobile": "+251911998877",
				"administrative_area": self.area,
				"service_category": "Inputs",
				"grievance_type": self.gtype.name,
				"description": f"Normal non-escalated case {test_tag}",
				"workflow_state": "Submitted",
				"status": "Submitted",
				"docstatus": 1,
				"escalated": 0,
			}
		).insert(ignore_permissions=True)

		# Create escalated grievance
		g_escalated = frappe.get_doc(
			{
				"doctype": "Grievance",
				"submission_channel": "Mobile App",
				"submitter_type": "Individual Farmer",
				"submitter_name": f"Citizen Escalated {test_tag}",
				"contact_mobile": "+251911998877",
				"administrative_area": self.area,
				"service_category": "Inputs",
				"grievance_type": self.gtype.name,
				"description": f"Critical escalated case {test_tag}",
				"workflow_state": "Submitted",
				"status": "Submitted",
				"docstatus": 1,
				"escalated": 1,
			}
		).insert(ignore_permissions=True)

		req = make_test_request(f"/api/v1/grievances?search={test_tag}", method="GET")
		res = frappe.api.handle(req)
		self.assertEqual(res.status_code, 200)
		data = json.loads(res.get_data(as_text=True))
		items = data["data"]["items"]

		# Find index of each
		esc_idx = next(i for i, item in enumerate(items) if item["name"] == g_escalated.name)
		normal_idx = next(i for i, item in enumerate(items) if item["name"] == g_normal.name)

		self.assertLess(esc_idx, normal_idx, "Escalated grievance must appear before non-escalated grievance")

	def test_kong_cors_methods_and_jwt_secret(self):
		"""Kong CORS must allow DELETE and not expose dummy JWT secrets."""
		from pathlib import Path

		import yaml

		kong_path = Path(__file__).resolve().parent.parent.parent / "kong" / "kong.yml"
		self.assertTrue(kong_path.exists())
		with open(kong_path) as f:
			conf = yaml.safe_load(f)

		cors_plugin = next(p for p in conf["services"][0]["plugins"] if p["name"] == "cors")
		self.assertIn("DELETE", cors_plugin["config"]["methods"])
		if "*" in cors_plugin["config"]["origins"]:
			self.assertFalse(cors_plugin["config"].get("credentials", False))

		for c in conf.get("consumers", []):
			for sec in c.get("jwt_secrets", []):
				self.assertNotEqual(sec.get("secret"), "REPLACE_WITH_OAN_AUTH_JWT_SECRET")

	def test_attachment_routes_present_in_spec_and_kong(self):
		"""Attachment endpoints must be present in OpenAPI and Kong."""
		from pathlib import Path

		import yaml

		spec_path = Path(__file__).resolve().parent.parent.parent / "openapi" / "openapi_v1.public.yaml"
		self.assertTrue(spec_path.exists())
		with open(spec_path) as f:
			spec = yaml.safe_load(f)

		paths = spec["paths"]
		self.assertIn("/api/v1/grievances/{ticket_number}/attachments", paths)
		self.assertIn("/api/v1/attachments/{attachment_id}/download", paths)
		self.assertIn("/api/v1/attachments/{attachment_id}/view", paths)
		self.assertIn("/api/v1/attachments/{attachment_id}", paths)

		view = paths["/api/v1/attachments/{attachment_id}/view"]["get"]
		self.assertEqual(view["responses"]["200"]["content"]["*/*"]["schema"]["format"], "binary")

	def test_channels_are_data_driven(self):
		"""Intake channels come from master data, not hardcoded constants."""
		self.assertIn("Web Portal", grievance.active_channels())
		frappe.db.set_value("Grievance Submission Type", "Web Portal", "is_active", 0)
		self.assertNotIn("Web Portal", grievance.active_channels())
		frappe.db.set_value("Grievance Submission Type", "Web Portal", "is_active", 1)

	def test_status_summary_endpoint(self):
		"""Status summary queue exposes valid card counts and public statuses."""
		res = grievance.summary()
		self.assertEqual(res["status"], "success")
		card_statuses = [card["status"] for card in res["data"]["cards"]]
		self.assertIn("All", card_statuses)
		self.assertIn("In Progress", card_statuses)

	def test_response_template_rest_crud_and_autonaming(self):
		"""Creating a template via REST auto-generates RT-### without client code."""
		frappe.set_user("Administrator")
		req_create = make_test_request(
			"/api/v1/response-templates",
			method="POST",
			data={
				"title": "Auto Named Template",
				"action": "Resolve",
				"reason": "Dear {{ submitter_name }}, resolved via REST.",
				"note": "Internal note for RT test.",
				"is_active": True,
			},
		)
		res_create = frappe.api.handle(req_create)
		self.assertEqual(res_create.status_code, 200)
		data_create = json.loads(res_create.get_data(as_text=True))
		self.assertEqual(data_create["status"], "success")
		tmpl = data_create["data"]["response_template"]
		tmpl_id = tmpl["template"]
		self.assertTrue(tmpl_id.startswith("RT-"))
		self.assertEqual(tmpl["title"], "Auto Named Template")
		self.assertEqual(tmpl["workflow_action"], "Resolve")

		# Update via PATCH
		req_update = make_test_request(
			f"/api/v1/response-templates/{tmpl_id}",
			method="PATCH",
			data={"note": "Updated internal note."},
		)
		res_update = frappe.api.handle(req_update)
		self.assertEqual(res_update.status_code, 200)
		data_update = json.loads(res_update.get_data(as_text=True))
		self.assertEqual(data_update["data"]["response_template"]["note"], "Updated internal note.")

		# Clean up
		frappe.db.delete("Grievance Response Template", {"name": tmpl_id})
