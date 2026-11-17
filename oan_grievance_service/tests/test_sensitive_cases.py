"""Sensitive cases (STG-514): bypass the Kebele, stay hidden from it, and say nothing identifying.

Each rule is checked where it is enforced on the server: routing picks the officer, the permission
hooks decide who reads, and the notification service writes what the submitter is told.
"""

import re
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service import permissions
from oan_grievance_service.api.v1 import grievance as grievance_api
from oan_grievance_service.services import classification, notifications, reassignment, routing
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import ticket_number as tn
from oan_grievance_service.services.ticket_number import region_of
from oan_grievance_service.setup.install import seed_role_levels, seed_workflow
from oan_grievance_service.tests.fixtures import (
	a_desk,
	a_grievance,
	a_leaf_area,
	a_service_category,
	a_submitter_type,
	a_unique_department,
	a_user_with_role,
	an_administrative_area,
)
from oan_grievance_service.tests.test_category_assignment import _keep_transaction
from oan_grievance_service.tests.test_classification import a_type

AUTO_ROUTING = (
	"oan_grievance_service.grievance_management.doctype.grievance_settings.grievance_settings"
	".auto_routing_enabled"
)
OFFICER_NAME = "tadesse"


class SensitiveFixture(FrappeTestCase):
	"""A desk with a Kebele officer and a Woreda focal person, and a sensitive and an ordinary type."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_role_levels()
		seed_workflow()

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.savepoint = "sensitive_cases"
		frappe.db.savepoint(self.savepoint)

		region = region_of(a_leaf_area())["name"]
		self.woreda = an_administrative_area(f"Woreda {frappe.generate_hash(length=5)}", region, "Woreda")
		self.kebele = an_administrative_area(
			f"Kebele {frappe.generate_hash(length=5)}", self.woreda, "Kebele"
		)

		self.category = a_service_category()
		self.department = a_unique_department("sens")
		self.kebele_officer = a_user_with_role("Grievance Officer", "kebele")
		self.focal_officer = a_user_with_role("Grievance Officer", OFFICER_NAME)
		self.desk = a_desk(
			self.department, self.category, "nodal_officer", (self.kebele_officer, self.kebele)
		)
		desk = frappe.get_doc("Grievance RBAC Assignment", self.desk)
		desk.append(
			"officers",
			{
				"user": self.focal_officer,
				"role_level": "senior_nodal_officer",
				"administrative_area": self.woreda,
				"active": 1,
			},
		)
		desk.save(ignore_permissions=True)

		self.sensitive_type = a_type(self.category, sensitive=1)
		self.ordinary_type = a_type(self.category)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point=self.savepoint)
		super().tearDown()

	def a_case(self, sensitive=True, **values):
		values.setdefault("administrative_area", self.kebele)
		values.setdefault("service_category", self.category)
		return a_grievance(grievance_type=self.sensitive_type if sensitive else self.ordinary_type, **values)

	def route(self, case):
		with patch(AUTO_ROUTING, return_value=True):
			routing.apply_routing(case, commit_status=False)
		case.reload()
		return case.assigned_to

	def misassign(self, case, user):
		"""Put the case on `user` the way an old or hand-edited record could be, past every guard."""
		case.db_set("assigned_to", user, update_modified=False)
		case.reload()


class TestSensitiveRouting(SensitiveFixture):
	def test_an_ordinary_case_goes_to_the_kebele_officer(self):
		self.assertEqual(self.route(self.a_case(sensitive=False)), self.kebele_officer)

	def test_a_sensitive_case_skips_the_kebele_for_the_focal_person(self):
		case = self.a_case()
		self.assertEqual(self.route(case), self.focal_officer)
		self.assertEqual(case.assigned_dept, self.department)

	def test_a_desk_with_only_kebele_officers_takes_no_sensitive_case(self):
		desk = frappe.get_doc("Grievance RBAC Assignment", self.desk)
		desk.officers = [row for row in desk.officers if row.user == self.kebele_officer]
		self.assertIsNone(routing.pick_officer_by_strategy(desk, self.a_case(), sensitive=True))
		# The same desk still serves an ordinary case.
		self.assertEqual(
			routing.pick_officer_by_strategy(desk, self.a_case(sensitive=False)), self.kebele_officer
		)

	def test_every_rung_of_a_desk_seated_below_woreda_is_skipped(self):
		for level in ("Village", "Ward"):
			seat = an_administrative_area(f"{level} {frappe.generate_hash(length=5)}", self.woreda, level)
			desk = frappe.get_doc("Grievance RBAC Assignment", self.desk)
			desk.officers = []
			desk.append(
				"officers",
				{
					"user": self.kebele_officer,
					"role_level": "nodal_officer",
					"administrative_area": seat,
					"active": 1,
				},
			)
			self.assertIsNone(routing.pick_officer_by_strategy(desk, sensitive=True), level)

	def test_a_sensitive_case_cannot_be_assigned_to_a_kebele_officer(self):
		with self.assertRaises(frappe.ValidationError):
			self.a_case(assigned_to=self.kebele_officer)

	def test_a_sensitive_case_can_be_assigned_to_the_focal_person(self):
		self.assertEqual(self.a_case(assigned_to=self.focal_officer).assigned_to, self.focal_officer)

	def test_escalation_never_lands_a_sensitive_case_on_a_kebele_officer(self):
		from oan_grievance_service.services import sla

		case = self.a_case(assigned_to=self.focal_officer)
		with patch.object(sla, "higher_authority_of", return_value=(self.kebele_officer, object())):
			sla.escalate(case, reason="test")
		case.reload()
		self.assertEqual(case.assigned_to, self.focal_officer)
		self.assertEqual(case.escalated, 1)


class TestKebeleOfficerAccess(SensitiveFixture):
	def test_a_kebele_officer_is_one_whose_every_seat_is_kebele_level(self):
		self.assertTrue(permissions.is_kebele_level_officer(self.kebele_officer))
		self.assertFalse(permissions.is_kebele_level_officer(self.focal_officer))
		self.assertFalse(permissions.is_kebele_level_officer("Administrator"))
		self.assertFalse(
			permissions.is_kebele_level_officer(a_user_with_role("Grievance Officer", "unseated"))
		)

	def test_an_officer_with_a_seat_above_the_kebele_is_not_a_kebele_officer(self):
		# A user sits once on a desk, so the seat above the Kebele is on another department's desk.
		a_desk(
			a_unique_department("upper"),
			self.category,
			"senior_nodal_officer",
			(self.kebele_officer, self.woreda),
		)
		self.assertFalse(permissions.is_kebele_level_officer(self.kebele_officer))

	def test_a_seat_with_no_area_is_not_kebele_level(self):
		other = a_user_with_role("Grievance Officer", "national")
		a_desk(a_unique_department("nat"), self.category, "nodal_officer", (other, None))
		self.assertFalse(permissions.is_kebele_level_officer(other))

	def test_a_kebele_officer_cannot_read_a_sensitive_case_assigned_to_them(self):
		case = self.a_case()
		self.misassign(case, self.kebele_officer)
		self.assertFalse(permissions.has_grievance_permission(case, "read", self.kebele_officer))
		self.assertFalse(permissions.has_grievance_permission(case, "write", self.kebele_officer))

	def test_a_kebele_officer_cannot_list_a_sensitive_case(self):
		case = self.a_case()
		self.misassign(case, self.kebele_officer)
		listed = frappe.get_list(
			"Grievance", filters={"name": case.name}, user=self.kebele_officer, pluck="name"
		)
		self.assertEqual(listed, [])

	def test_a_kebele_officer_cannot_open_a_sensitive_case_through_the_api(self):
		case = self.a_case()
		self.misassign(case, self.kebele_officer)
		frappe.set_user(self.kebele_officer)
		with self.assertRaises(frappe.PermissionError):
			grievance_api._load(case.ticket_number)
		with _keep_transaction():
			refused = grievance_api.timeline(case.ticket_number)
		self.assertEqual(refused["code"], "PERMISSION_DENIED")
		self.assertNotIn("data", refused)

	def test_a_kebele_officer_cannot_get_a_sensitive_case_from_the_list_endpoint(self):
		case = self.a_case()
		self.misassign(case, self.kebele_officer)
		frappe.set_user(self.kebele_officer)
		result = grievance_api.list_grievances(limit=100)
		self.assertNotIn(case.name, {row["name"] for row in result["data"]["items"]})

	def test_a_kebele_officer_still_reads_an_ordinary_case_assigned_to_them(self):
		case = self.a_case(sensitive=False)
		self.misassign(case, self.kebele_officer)
		self.assertTrue(permissions.has_grievance_permission(case, "read", self.kebele_officer))
		listed = frappe.get_list(
			"Grievance", filters={"name": case.name}, user=self.kebele_officer, pluck="name"
		)
		self.assertEqual(listed, [case.name])

	def test_the_focal_person_reads_and_lists_the_sensitive_case(self):
		case = self.a_case(assigned_to=self.focal_officer)
		self.assertTrue(permissions.has_grievance_permission(case, "read", self.focal_officer))
		listed = frappe.get_list(
			"Grievance", filters={"name": case.name}, user=self.focal_officer, pluck="name"
		)
		self.assertEqual(listed, [case.name])

	def test_a_kebele_officer_gets_no_sensitive_case_by_a_pending_change_request(self):
		case = self.a_case(assigned_to=self.focal_officer)
		frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": case.name,
				"status": "Pending",
				"pending_with": self.kebele_officer,
				"requested_by": self.focal_officer,
			}
		).db_insert()
		self.assertFalse(permissions.has_grievance_permission(case, "read", self.kebele_officer))
		listed = frappe.get_list(
			"Grievance", filters={"name": case.name}, user=self.kebele_officer, pluck="name"
		)
		self.assertEqual(listed, [])

	def test_an_admin_still_reads_every_sensitive_case(self):
		case = self.a_case()
		self.assertTrue(permissions.has_grievance_permission(case, "read", "Administrator"))


class TestSensitiveSubmitterAccess(SensitiveFixture):
	def file_for(self, user, assisted=None, sensitive=True):
		case = self.a_case(sensitive=sensitive)
		frappe.db.set_value(
			"Grievance", case.name, {"owner": user, "assisted_by_officer": assisted}, update_modified=False
		)
		case.reload()
		return case

	def test_a_submitter_reads_their_own_sensitive_case(self):
		farmer = a_user_with_role("Grievance Submitter", "farmer")
		case = self.file_for(farmer)
		self.assertTrue(permissions.has_grievance_permission(case, "read", farmer))
		listed = frappe.get_list("Grievance", filters={"name": case.name}, user=farmer, pluck="name")
		self.assertEqual(listed, [case.name])

	def test_an_agent_who_filed_it_for_someone_loses_a_sensitive_case(self):
		agent = a_user_with_role("Grievance Assisted Submitter", "agent")
		case = self.file_for(agent, assisted=agent)
		self.assertFalse(permissions.has_grievance_permission(case, "read", agent))
		self.assertFalse(permissions.has_grievance_permission(case, "write", agent))
		listed = frappe.get_list("Grievance", filters={"name": case.name}, user=agent, pluck="name")
		self.assertEqual(listed, [])

	def test_an_agent_keeps_an_ordinary_case_they_filed_for_someone(self):
		agent = a_user_with_role("Grievance Assisted Submitter", "agent")
		case = self.file_for(agent, assisted=agent, sensitive=False)
		self.assertTrue(permissions.has_grievance_permission(case, "read", agent))
		listed = frappe.get_list("Grievance", filters={"name": case.name}, user=agent, pluck="name")
		self.assertEqual(listed, [case.name])

	def test_another_submitter_never_reads_it(self):
		stranger = a_user_with_role("Grievance Submitter", "stranger")
		case = self.file_for(a_user_with_role("Grievance Submitter", "owner"))
		self.assertFalse(permissions.has_grievance_permission(case, "read", stranger))

	def test_the_list_shows_a_submitter_no_desk_of_a_sensitive_case(self):
		farmer = a_user_with_role("Grievance Submitter", "farmer")
		sensitive, ordinary = self.file_for(farmer), self.file_for(farmer, sensitive=False)
		for case in (sensitive, ordinary):
			frappe.db.set_value(
				"Grievance", case.name, "assigned_dept", self.department, update_modified=False
			)
		frappe.set_user(farmer)
		rows = {row["name"]: row for row in grievance_api.list_grievances(limit=100)["data"]["items"]}
		self.assertIsNone(rows[sensitive.name]["assigned_dept"])
		self.assertIsNone(rows[sensitive.name]["department"])
		self.assertNotIn(self.department, str(rows[sensitive.name]))
		# An ordinary case still names its department, as before.
		self.assertEqual(rows[ordinary.name]["department"], self.department)

	def test_a_submitter_is_shown_no_officer_and_no_classification(self):
		farmer = a_user_with_role("Grievance Submitter", "farmer")
		case = self.file_for(farmer)
		self.misassign(case, self.focal_officer)
		frappe.set_user(farmer)
		opened = grievance_api.timeline(case.ticket_number)["data"]
		self.assertIsNone(opened["assignment"]["assigned_to"])
		self.assertIsNone(opened["assignment"]["assigned_to_name"])
		self.assertIsNone(opened["assignment"]["department"])
		for key in ("is_sensitive", "handling_class", "priority"):
			self.assertNotIn(key, opened)
		self.assertNotIn(OFFICER_NAME, str(opened))
		self.assertNotIn(self.focal_officer, str(opened))


class TestSensitiveNotifications(SensitiveFixture):
	def a_notification(self, recipient="Submitter", channel="SMS"):
		event = f"test_sensitive_{frappe.generate_hash(length=6)}"
		name = f"Test {event}"
		frappe.get_doc(
			{
				"doctype": "Notification",
				"name": name,
				"subject": "{{ _('Update on {0}', context='test.sensitive.subject').format(doc.service_category) }}",
				"document_type": "Grievance",
				"event": "Method",
				"method": event,
				"channel": channel,
				"grievance_recipient_type": recipient,
				"message": (
					"{{ _('Case assigned to {0} in {1}', context='test.sensitive.message')"
					".format(frappe.db.get_value('User', doc.assigned_to, 'full_name'), doc.assigned_dept) }}"
				),
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
		return event

	def masked(self, case):
		return f"Grievance {tn.display(case.ticket_number)}. PIN: {notifications.PIN_MASK}"

	def revealed(self, case):
		return f"Grievance {tn.display(case.ticket_number)}. PIN: {classification.pin_of(case)}"

	def a_registered_farmer(self):
		farmer = a_user_with_role("Grievance Submitter", "farmer")
		profile = frappe.get_doc(
			{
				"doctype": "Grievance Submitter Profile",
				"submitter_name": "Registered Farmer",
				"submitter_type": a_submitter_type(),
				"contact_mobile": "+251911234567",
				"user": farmer,
			}
		).insert(ignore_permissions=True)
		return farmer, profile.name

	def test_a_sensitive_message_is_logged_with_the_reference_and_a_masked_pin(self):
		case = self.a_case(assigned_to=self.focal_officer)
		rows = notifications.queue(case, self.a_notification())
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].message, self.masked(case))
		stored = frappe.db.get_value("Grievance Notification Log", rows[0].name, ["message", "subject"])
		self.assertNotIn(classification.pin_of(case), " ".join(stored))
		for leaked in (OFFICER_NAME, self.focal_officer, self.department, self.category):
			self.assertNotIn(leaked, rows[0].message)
			self.assertNotIn(leaked, rows[0].subject or "")

	def test_the_text_that_leaves_carries_the_pin(self):
		case = self.a_case(assigned_to=self.focal_officer)
		row = notifications.queue(case, self.a_notification())[0]
		self.assertEqual(notifications._outbound_message(row, case), self.revealed(case))
		# The log row itself is untouched.
		self.assertEqual(
			frappe.db.get_value("Grievance Notification Log", row.name, "message"), self.masked(case)
		)

	def test_a_sensitive_sms_handed_to_oan_carries_the_pin(self):
		"""With OAN Messaging on the site, the SMS that leaves carries the PIN, as with Frappe's sender.

		The Grievance Notification Log row keeps the masked text. `_send_sms_row` is called
		directly: `dispatch_queued` commits after each row, which this fixture's savepoint cannot
		survive.
		"""
		keys = ("oan_messaging_enabled", "oan_messaging_base_url", "oan_messaging_token")
		conf = {k: frappe.conf.get(k) for k in keys}
		self.addCleanup(lambda: [frappe.conf.__setitem__(k, v) for k, v in conf.items()])
		frappe.conf.oan_messaging_enabled = 1
		frappe.conf.oan_messaging_base_url = "https://oan-messaging.example.invalid"
		frappe.conf.oan_messaging_token = "oanmsg_" + "t" * 43
		enqueue = patch.object(frappe, "enqueue")  # no dispatch job from the test
		enqueue.start()
		self.addCleanup(enqueue.stop)

		case = self.a_case(assigned_to=self.focal_officer, contact_mobile="+251900000002")
		row = notifications.queue(case, self.a_notification())[0]
		result = notifications._send_sms_row(row, case)

		self.assertEqual(result["status"], notifications.STATUS_QUEUED_WITH_OAN)
		self.assertEqual(
			frappe.db.get_value("OAN Message", result["oan_message"], "text"), self.revealed(case)
		)
		self.assertEqual(
			frappe.db.get_value("Grievance Notification Log", row.name, "message"), self.masked(case)
		)

	def test_no_other_message_ever_gets_the_pin(self):
		case = self.a_case(assigned_to=self.focal_officer)
		officer_row = notifications.queue(case, self.a_notification(recipient="Assigned Officer"))[0]
		self.assertEqual(notifications._outbound_message(officer_row, case), officer_row.message)
		self.assertNotIn(classification.pin_of(case), notifications._outbound_message(officer_row, case))

	def test_a_sensitive_email_carries_the_pin_but_the_case_record_does_not(self):
		case = self.a_case(assigned_to=self.focal_officer, contact_email="farmer@example.com")
		row = notifications.queue(case, self.a_notification(channel="Email"))[0]
		self.assertEqual(row.message, self.masked(case))
		sent = []
		with patch.object(notifications.frappe, "sendmail", side_effect=lambda **kw: sent.append(kw)):
			result = notifications._send_email_row(row, case)
		self.assertEqual(sent[0]["message"], self.revealed(case))
		# The Communication shows on the case, to the officers who work it: masked.
		content = frappe.db.get_value("Communication", result["communication"], "content")
		self.assertNotIn(classification.pin_of(case), content)
		self.assertNotIn(OFFICER_NAME, f"{row.message} {row.subject}")

	def test_a_sensitive_in_app_message_reaches_the_submitter_with_the_pin(self):
		farmer, profile = self.a_registered_farmer()
		case = self.a_case(assigned_to=self.focal_officer, submitter=profile)
		row = notifications.queue(case, self.a_notification(channel="System Notification"))[0]
		self.assertEqual(row.recipient, farmer)
		self.assertEqual(row.message, self.masked(case))
		sent = []
		target = "frappe.desk.doctype.notification_log.notification_log.enqueue_create_notification"
		with patch(target, side_effect=lambda users, doc, **kw: sent.append(doc)):
			notifications._send_system_row(row, case)
		self.assertEqual(sent[0]["description"], self.revealed(case))
		self.assertNotIn(OFFICER_NAME, f"{row.message} {row.subject}")

	def test_the_message_is_the_same_whatever_the_template_says(self):
		case = self.a_case(assigned_to=self.focal_officer)
		first = notifications.queue(case, self.a_notification())[0].message
		second = notifications.queue(case, self.a_notification())[0].message
		self.assertEqual(first, second)

	def test_an_ordinary_case_still_gets_its_template(self):
		case = self.a_case(sensitive=False, assigned_to=self.kebele_officer)
		rows = notifications.queue(case, self.a_notification())
		self.assertIn("Case assigned to", rows[0].message)
		self.assertIn("kebele", rows[0].message)

	def test_an_officer_notice_about_a_sensitive_case_skips_a_kebele_officer(self):
		case = self.a_case()
		self.misassign(case, self.kebele_officer)
		self.assertEqual(notifications.queue(case, self.a_notification(recipient="Assigned Officer")), [])

	def test_an_officer_notice_about_a_sensitive_case_reaches_the_focal_person(self):
		case = self.a_case(assigned_to=self.focal_officer)
		rows = notifications.queue(case, self.a_notification(recipient="Assigned Officer"))
		self.assertEqual([row.recipient for row in rows], [self.focal_officer])
		self.assertIn("Case assigned to", rows[0].message)

	def test_the_text_helper_names_the_reference_and_pin_only(self):
		case = self.a_case()
		reference = re.escape(tn.display(case.ticket_number))
		message, subject = notifications.sensitive_submitter_text(case)
		self.assertEqual(message, self.masked(case))
		revealed, _subject = notifications.sensitive_submitter_text(case, reveal_pin=True)
		self.assertRegex(revealed, rf"^Grievance {reference}\. PIN: \d{{6}}$")
		self.assertEqual(subject, f"Grievance {tn.display(case.ticket_number)}")


class TestSensitiveSubmission(SensitiveFixture):
	def a_registered_farmer(self):
		farmer = a_user_with_role("Grievance Submitter", "farmer")
		frappe.get_doc(
			{
				"doctype": "Grievance Submitter Profile",
				"submitter_name": "Registered Farmer",
				"submitter_type": a_submitter_type(),
				"contact_mobile": "+251911234567",
				"user": farmer,
			}
		).insert(ignore_permissions=True)
		return farmer

	def submit(self, user="Administrator", **payload):
		"""File through the submit handler as `user`; the handler is what classifies.

		An administrator, or an assisted submitter, files on the submitter's behalf; a Grievance
		Submitter files for themselves.
		"""
		body = {
			"service_category": self.category,
			"grievance_type": self.sensitive_type,
			"administrative_area": self.kebele,
			"description": "A sensitive grievance described in more than twenty characters.",
			"submitter_type": a_submitter_type(),
			"submitter_name": "Test Submitter",
			"contact_mobile": "+251911234567",
			**payload,
		}
		frappe.set_user(user)
		try:
			result = grievance_api.submit(**body)
		finally:
			frappe.set_user("Administrator")
		self.assertIn("data", result, msg=result)
		name = tn.normalize(result["data"]["ticket_number"])
		return result["data"], frappe.get_doc("Grievance", name)

	def test_a_submitter_filing_for_themselves_gets_the_pin(self):
		data, case = self.submit(self.a_registered_farmer(), is_anonymous=True)
		self.assertEqual((case.is_anonymous, case.is_sensitive), (1, 1))
		self.assertFalse(case.assisted_by_officer)
		self.assertEqual(data["status_pin"], classification.pin_of(case))
		self.assertRegex(data["status_pin"], r"^\d{6}$")
		self.assertIsNone(data["routing_rule"])

	def test_an_administrator_filing_on_behalf_gets_no_pin_and_no_desk(self):
		data, case = self.submit()
		self.assertEqual(case.assisted_by_officer, "Administrator")
		self.assertNotIn("status_pin", data)
		state = data["current_state"]
		for key in ("assigned_to", "assigned_to_name", "department", "possible_duplicate"):
			self.assertIsNone(state[key], key)
		for key in ("is_sensitive", "handling_class", "priority"):
			self.assertNotIn(key, state)

	def test_an_agent_filing_on_behalf_gets_no_pin(self):
		agent = a_user_with_role("Grievance Assisted Submitter", "agent")
		data, case = self.submit(agent)
		self.assertEqual(case.assisted_by_officer, agent)
		self.assertNotIn("status_pin", data)
		self.assertIsNone(data["current_state"]["department"])

	def test_the_submitter_still_gets_the_pin_by_message_when_someone_files_for_them(self):
		_data, case = self.submit(is_anonymous=True)
		rows = frappe.get_all(
			"Grievance Notification Log",
			filters={"grievance": case.name, "event": C.EVENT_SUBMISSION_RECEIVED},
			fields=["name", "message", "language"],
		)
		for row in rows:
			self.assertEqual(
				row.message, f"Grievance {tn.display(case.ticket_number)}. PIN: {notifications.PIN_MASK}"
			)
			self.assertIn(classification.pin_of(case), notifications._outbound_message(row, case))

	def test_what_a_submitter_is_shown_of_a_sensitive_case_has_no_desk_and_no_flags(self):
		farmer = a_user_with_role("Grievance Submitter", "farmer")
		_data, case = self.submit()
		frappe.set_user(farmer)
		state = grievance_api._current_state(case)
		self.assertIsNone(state["assigned_to"])
		self.assertIsNone(state["assigned_to_name"])
		self.assertIsNone(state["department"])
		for key in ("is_sensitive", "handling_class", "priority"):
			self.assertNotIn(key, state)

	def test_an_officer_is_shown_the_flags_in_the_current_state(self):
		_data, case = self.submit()
		state = grievance_api._current_state(case)
		self.assertEqual(state["is_sensitive"], True)

	def test_an_ordinary_submission_has_no_pin(self):
		data, case = self.submit(self.a_registered_farmer(), grievance_type=self.ordinary_type)
		self.assertNotIn("status_pin", data)
		self.assertEqual(case.is_sensitive, 0)
		self.assertIsNone(classification.pin_of(case))

	def test_an_ordinary_case_filed_on_behalf_keeps_the_staff_view(self):
		data, _case = self.submit(grievance_type=self.ordinary_type)
		self.assertIn("is_sensitive", data["current_state"])

	def test_an_anonymous_ordinary_submission_still_works(self):
		_data, case = self.submit(grievance_type=self.ordinary_type, is_anonymous=True)
		self.assertEqual(case.is_anonymous, 1)

	def test_the_submission_is_classified_by_the_backend_not_the_client(self):
		_data, plain = self.submit(grievance_type=self.ordinary_type, is_sensitive=True)
		self.assertEqual(plain.is_sensitive, 0)


class TestSensitiveReassignment(SensitiveFixture):
	"""Moving a case to a sensitive type takes it off a Kebele-level officer."""

	def an_ordinary_case_with_the_kebele_officer(self):
		return self.a_case(sensitive=False, assigned_to=self.kebele_officer, assigned_dept=self.department)

	def test_a_move_to_a_sensitive_type_goes_to_the_focal_person(self):
		case = self.an_ordinary_case_with_the_kebele_officer()
		changes = reassignment.resolve(case, department=self.department, grievance_type=self.sensitive_type)
		self.assertEqual(changes["grievance_type"], self.sensitive_type)
		self.assertEqual(changes["assigned_to"], self.focal_officer)

	def test_naming_a_kebele_officer_for_a_sensitive_type_is_refused(self):
		case = self.an_ordinary_case_with_the_kebele_officer()
		with self.assertRaises(frappe.ValidationError):
			reassignment.resolve(
				case,
				department=self.department,
				officer=self.kebele_officer,
				grievance_type=self.sensitive_type,
			)

	def test_a_move_to_another_ordinary_type_keeps_the_kebele_officer(self):
		case = self.an_ordinary_case_with_the_kebele_officer()
		changes = reassignment.resolve(case, department=self.department, grievance_type=a_type(self.category))
		self.assertNotIn("assigned_to", changes)

	def test_the_resolved_move_saves_with_the_focal_person(self):
		case = self.an_ordinary_case_with_the_kebele_officer()
		changes = reassignment.resolve(case, department=self.department, grievance_type=self.sensitive_type)
		case.update(changes)
		case.flags.change_request = "test-change-request"
		case.save(ignore_permissions=True)
		self.assertEqual((case.is_sensitive, case.assigned_to), (1, self.focal_officer))

	def test_a_type_change_that_leaves_a_kebele_officer_on_a_sensitive_case_is_refused(self):
		case = self.an_ordinary_case_with_the_kebele_officer()
		case.grievance_type = self.sensitive_type
		case.flags.change_request = "test-change-request"
		with self.assertRaises(frappe.ValidationError):
			case.save(ignore_permissions=True)


class TestOfficerView(SensitiveFixture):
	def test_an_officer_sees_the_flags_the_backend_decided(self):
		urgent_sensitive = a_type(self.category, sensitive=1)
		# The category's base rule, and an all-year urgent rule for the type.
		classification.set_base_days(self.category, 10)
		frappe.get_doc(
			{
				"doctype": "Grievance SLA Rule",
				"service_category": self.category,
				"grievance_type": urgent_sensitive,
				"sla_days": 10,
				"is_urgent": 1,
			}
		).insert(ignore_permissions=True)
		case = a_grievance(
			grievance_type=urgent_sensitive,
			administrative_area=self.kebele,
			service_category=self.category,
			assigned_to=self.focal_officer,
		)
		frappe.set_user(self.focal_officer)
		opened = grievance_api.timeline(case.ticket_number)["data"]
		self.assertEqual(
			(opened["is_sensitive"], opened["handling_class"], opened["priority"]),
			(True, C.HANDLING_URGENT, C.PRIORITY_URGENT),
		)
		row = next(
			r for r in grievance_api.list_grievances(limit=100)["data"]["items"] if r["name"] == case.name
		)
		self.assertEqual(
			(row["is_sensitive"], row["handling_class"], row["priority"]),
			(True, C.HANDLING_URGENT, C.PRIORITY_URGENT),
		)

	def test_an_ordinary_case_reads_standard_and_normal(self):
		case = self.a_case(sensitive=False, assigned_to=self.kebele_officer)
		frappe.set_user(self.kebele_officer)
		opened = grievance_api.timeline(case.ticket_number)["data"]
		self.assertEqual(
			(opened["is_sensitive"], opened["handling_class"], opened["priority"]),
			(False, C.HANDLING_STANDARD, None),
		)
