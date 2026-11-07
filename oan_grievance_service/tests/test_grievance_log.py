"""Grievance operational logging: the SLA clock, reminder, state-timer and escalation events,
the framing of the scheduled sweeps, and the fields that identify a case.

The generic parts (masking, the correlation id, the request log, `log_error`) are tested in
oan_auth_service, where they live."""

import io
import json
import logging
import uuid
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from oan_auth_service.api import operational_log as oplog

from oan_grievance_service import tasks
from oan_grievance_service.services import grievance_log as glog
from oan_grievance_service.services import sla

JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJqYW5lIn0.c2lnbmF0dXJlLXBhcnQ"
REFRESH = "refresh-abcdef-1234567890"
PASSWORD = "hunter2-Pa55word"
EMAIL = "jane.doe@example.com"
PHONE = "0911223344"
FAYDA = "1234567890123456"
NAME = "Abebe Kebede"
SENSITIVE = (JWT, REFRESH, PASSWORD, EMAIL, PHONE, FAYDA, NAME, "c2lnbmF0dXJlLXBhcnQ")


class CapturedLog:
	"""Stands in for the operational logger and keeps what it was asked to write."""

	def __init__(self, level=logging.DEBUG):
		self.stream = io.StringIO()
		handler = logging.StreamHandler(self.stream)
		handler.setFormatter(oplog.JsonFormatter())
		self.logger = logging.getLogger(f"oan-test-{uuid.uuid4().hex}")
		self.logger.setLevel(level)
		self.logger.propagate = False
		self.logger.addHandler(handler)
		self._patch = mock.patch.object(oplog, "get_logger", return_value=self.logger)

	def __enter__(self):
		self._patch.start()
		return self

	def __exit__(self, *exc):
		self._patch.stop()

	@property
	def text(self) -> str:
		return self.stream.getvalue()

	@property
	def events(self) -> list[dict]:
		return [json.loads(line) for line in self.text.splitlines() if line]

	def named(self, event: str) -> list[dict]:
		return [e for e in self.events if e["event"] == event]


class LogCase(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self._reset_request_state()
		self.addCleanup(self._reset_request_state)

	@staticmethod
	def _reset_request_state():
		frappe.local.request_id = None
		frappe.local.oan_request_started = None

	def assertNoLeak(self, text: str):
		for secret in SENSITIVE:
			self.assertNotIn(secret, text, msg=f"{secret!r} reached the log")


class _Case(frappe._dict):
	"""A grievance stand-in that records `db_set`."""

	def db_set(self, updates, value=None, update_modified=True):
		self.update(updates if isinstance(updates, dict) else {updates: value})


def _case(**overrides):
	values = {
		"name": "GRV-0001",
		"ticket_number": "3001002A0",
		"service_category": "No such category",
		"assigned_dept": "Dept A",
		"assigned_to": "officer.one@example.com",
		"status": "Assigned",
		"sla_due_date": frappe.utils.now_datetime(),
		"submitter_name": NAME,
		"contact_mobile": PHONE,
		"contact_email": EMAIL,
		"escalated_at": None,
	}
	values.update(overrides)
	return _Case(values)


class TestSlaAndEscalationEvents(LogCase):
	def test_an_escalation_is_logged_with_who_it_moved_between_and_when(self):
		case = _case()
		rung = frappe._dict(name="level_2", level_name="Desk Head", escalation_hours=24)
		frappe.local.request_id = "job-sla_escalation-123"
		with (
			CapturedLog() as captured,
			mock.patch.object(sla, "higher_authority_of", return_value=("boss@example.com", rung)),
			mock.patch.object(sla.GrievanceTimeline, "record"),
			mock.patch("oan_grievance_service.services.notifications.queue"),
		):
			target = sla.escalate(case, reason="SLA Breached")

		self.assertEqual(target, "boss@example.com")
		(line,) = captured.named("escalation.escalated")
		self.assertEqual(line["level"], "INFO")
		self.assertEqual(line["correlation_id"], "job-sla_escalation-123")
		self.assertEqual(line["grievance"], "GRV-0001")
		self.assertEqual(line["ticket_number"], "3001002A0")
		self.assertEqual(line["rung"], "Desk Head")
		self.assertEqual(line["from_user"], "o***@example.com")
		self.assertEqual(line["to_user"], "b***@example.com")
		self.assertEqual(line["reason"], "SLA Breached")
		self.assertTrue(line["escalated_at"])
		self.assertTrue(line["next_escalation_at"])
		self.assertTrue(line["sla_due_at"])
		self.assertNoLeak(captured.text)
		self.assertNotIn("officer.one", captured.text)

	def test_an_escalation_with_nobody_above_is_a_warning(self):
		case = _case()
		with (
			CapturedLog() as captured,
			mock.patch.object(sla, "higher_authority_of", return_value=(None, None)),
			mock.patch.object(sla.GrievanceTimeline, "record"),
			mock.patch("oan_grievance_service.services.notifications.queue"),
		):
			sla.escalate(case)
		(line,) = captured.named("escalation.no_higher_authority")
		self.assertEqual(line["level"], "WARNING")
		self.assertEqual(line["grievance"], "GRV-0001")

	def test_a_reminder_is_logged_with_its_threshold(self):
		row = frappe._dict(name="GRV-0001", on_hold_since=None, reminder_80_sent=0, reminder_50_sent=0)
		with (
			CapturedLog() as captured,
			mock.patch.object(tasks, "open_grievances_with_sla", return_value=[row]),
			mock.patch.object(sla, "consumed_percent", return_value=85),
			mock.patch.object(frappe, "get_doc", return_value=_case()),
			mock.patch.object(tasks.notifications, "queue"),
		):
			sent = tasks.send_sla_reminders()
		self.assertEqual(sent, 1)
		(line,) = captured.named("sla.reminder_sent")
		self.assertEqual(line["threshold_percent"], 80)
		self.assertEqual(line["consumed_percent"], 85)
		self.assertEqual(line["grievance"], "GRV-0001")
		self.assertNoLeak(captured.text)

	def test_a_pause_resume_and_stop_are_logged(self):
		case = _case(on_hold_since=None, sla_due_date=frappe.utils.add_days(frappe.utils.now_datetime(), -1))
		with CapturedLog() as captured, mock.patch.object(sla, "sla_category_of") as category:
			category.return_value = sla.PAUSED
			sla.on_status_change(case, "Awaiting Citizen")
			self.assertTrue(case.on_hold_since)

			category.return_value = sla.RUNNING
			sla.on_status_change(case, "In Progress")
			self.assertIsNone(case.on_hold_since)

			category.return_value = sla.STOPPED
			sla.on_status_change(case, "Resolved")

		self.assertEqual(len(captured.named("sla.clock_paused")), 1)
		self.assertEqual(len(captured.named("sla.clock_resumed")), 1)
		(stopped,) = captured.named("sla.clock_stopped")
		self.assertTrue(stopped["breached"])
		self.assertNoLeak(captured.text)

	def test_the_clock_is_judged_once_when_a_case_is_resolved_and_not_again_when_it_closes(self):
		"""Resolved on time, then auto-closed late: the second move is not an outcome."""
		now = frappe.utils.now_datetime()
		case = _case(on_hold_since=None, sla_due_date=frappe.utils.add_days(now, 2))
		with CapturedLog() as captured, mock.patch.object(sla, "sla_category_of") as category:
			# Day 5 of 7: resolved in time.
			category.side_effect = lambda state: {"In Progress": sla.RUNNING}.get(state, sla.STOPPED)
			sla.on_status_change(case, "Resolved", from_state="In Progress")
			# Day 9: the auto-close moves it between Stopped states, after the deadline.
			case.sla_due_date = frappe.utils.add_days(now, -2)
			sla.on_status_change(case, "Closed", from_state="Resolved")
		(stopped,) = captured.named("sla.clock_stopped")
		self.assertEqual(stopped["state"], "Resolved")
		self.assertFalse(stopped["breached"])

	def test_a_reopened_case_is_judged_again_on_its_new_window(self):
		now = frappe.utils.now_datetime()
		case = _case(on_hold_since=None, sla_due_date=frappe.utils.add_days(now, 2))
		with CapturedLog() as captured, mock.patch.object(sla, "sla_category_of") as category:
			category.side_effect = (
				lambda state: sla.RUNNING if state in ("In Progress", "Reopened") else sla.STOPPED
			)
			sla.on_status_change(case, "Resolved", from_state="In Progress")
			with mock.patch.object(sla, "reset_clock"):
				sla.on_status_change(case, "Reopened", from_state="Resolved")
			sla.on_status_change(case, "Resolved", from_state="Reopened")
		self.assertEqual(len(captured.named("sla.clock_stopped")), 2)

	def test_a_failed_escalation_is_an_error_event_with_the_sweep_id(self):
		with (
			CapturedLog() as captured,
			mock.patch.object(frappe, "get_all", return_value=["GRV-0001"]),
			mock.patch.object(frappe, "get_doc", return_value=_case()),
			mock.patch.object(sla, "escalate", side_effect=RuntimeError(f"no desk for {EMAIL}")),
			mock.patch.object(frappe, "log_error"),
			mock.patch.dict(frappe.local.conf, {"grievance_auto_escalation_enabled": True}),
		):
			tasks.escalate_breached()

		(failure,) = captured.named("error")
		self.assertEqual(failure["title"], "Grievance escalation failed")
		self.assertTrue(failure["correlation_id"].startswith("job-sla_escalation-"))
		self.assertEqual(failure["exception"], "RuntimeError")
		self.assertNoLeak(captured.text)

	def test_a_sweep_is_framed_by_start_and_finish_with_a_count(self):
		with (
			CapturedLog() as captured,
			mock.patch.object(frappe, "get_all", return_value=[]),
			mock.patch.dict(frappe.local.conf, {"grievance_auto_escalation_enabled": True}),
		):
			result = tasks.escalate_breached()

		self.assertEqual(result, 0)
		(started,) = captured.named("job.started")
		(finished,) = captured.named("job.finished")
		self.assertEqual(started["job"], "sla_escalation")
		self.assertEqual(finished["processed"], 0)
		self.assertIn("duration_ms", finished)
		self.assertEqual(started["correlation_id"], finished["correlation_id"])
		# A job's id does not outlive it.
		self.assertIsNone(frappe.local.request_id)

	def test_a_job_that_raises_is_logged_and_still_raises(self):
		@glog.logged_job("explodes")
		def explode():
			raise RuntimeError("boom")

		with CapturedLog() as captured:
			with self.assertRaises(RuntimeError):
				explode()
		self.assertEqual(captured.named("job.failed")[0]["exception"], "RuntimeError")
		self.assertIsNone(frappe.local.request_id)

	def test_an_unreadable_case_costs_the_fields_not_the_work(self):
		class Broken:
			def get(self, *_):
				raise RuntimeError("half-built document")

		self.assertEqual(glog.grievance_fields(Broken()), {})
		# and a line built from it is still written, with what it has
		with CapturedLog() as captured:
			oplog.emit("sla.clock_started", **glog.grievance_fields(Broken()), sla_days=5)
		(line,) = captured.events
		self.assertEqual(line["sla_days"], 5)

	def test_a_case_row_and_a_document_give_the_same_fields(self):
		row = frappe._dict(name="GRV-1", ticket_number="3001002A0", service_category="C", status="Open")
		fields = glog.grievance_fields(row)
		self.assertEqual(fields["grievance"], "GRV-1")
		self.assertEqual(fields["ticket_number"], "3001002A0")
		self.assertEqual(fields["category"], "C")
		self.assertEqual(set(fields), set(glog.grievance_fields(_case())))

	def test_personal_fields_on_a_case_never_reach_an_sla_line(self):
		with CapturedLog() as captured:
			oplog.emit("sla.clock_started", **glog.grievance_fields(_case()))
		self.assertNoLeak(captured.text)
		(line,) = captured.events
		for field in ("submitter_name", "contact_mobile", "contact_email"):
			self.assertNotIn(field, line)
