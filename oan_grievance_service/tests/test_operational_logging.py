"""Operational logging: correlation ids, request/response lines, errors, SLA and escalation
events, authentication failures, and the guarantee that no credential or personal data
reaches a log line."""

import io
import json
import logging
import uuid
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from oan_auth_service.api.middleware import validate_jwt_request
from oan_auth_service.api.router import ensure_routes_registered
from oan_auth_service.tests.test_router import make_test_request
from oan_auth_service.tests.utils import configured_keys
from werkzeug.wrappers import Response

from oan_grievance_service import tasks
from oan_grievance_service.api import request_logging as rl
from oan_grievance_service.api.router import get_health
from oan_grievance_service.services import operational_log as oplog
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


class TestMasking(FrappeTestCase):
	def test_contact_details_are_masked(self):
		self.assertEqual(oplog.mask_email(EMAIL), "j***@example.com")
		self.assertEqual(oplog.mask_phone(PHONE), "***44")
		self.assertEqual(oplog.mask_phone("+251911234567"), "***67")
		self.assertEqual(oplog.mask_name(NAME), "A*** K***")
		self.assertEqual(oplog.mask_identifier(FAYDA), "***56")
		self.assertEqual(oplog.mask_ip("196.188.12.34"), "196.188.12.0")
		self.assertEqual(oplog.mask_ip("2001:db8:85a3:8d3:1319:8a2e:370:7348"), "2001:db8:85a3::")

	def test_a_user_id_is_masked_whatever_it_looks_like(self):
		self.assertEqual(oplog.mask_user(EMAIL), "j***@example.com")
		self.assertEqual(oplog.mask_user("+251911234567"), "***67")
		self.assertEqual(oplog.mask_user("abebe"), "a***")
		self.assertEqual(oplog.mask_user("Administrator"), "Administrator")
		self.assertIsNone(oplog.mask_user(None))

	def test_short_values_are_hidden_rather_than_partly_shown(self):
		self.assertEqual(oplog.mask_phone("123"), oplog.MASKED)
		self.assertEqual(oplog.mask_identifier("1234"), oplog.MASKED)
		self.assertEqual(oplog.mask_email("not-an-email"), oplog.MASKED)

	def test_user_ref_is_stable_case_blind_and_not_the_address(self):
		ref = oplog.user_ref(EMAIL)
		self.assertEqual(ref, oplog.user_ref(EMAIL.upper()))
		self.assertNotEqual(ref, oplog.user_ref("someone.else@example.com"))
		self.assertEqual(len(ref), 12)
		self.assertNotIn("jane", ref)
		self.assertIsNone(oplog.user_ref(None))

	def test_free_text_loses_credentials_and_personal_data(self):
		text = (
			f"login failed for {EMAIL} password={PASSWORD} Authorization: Bearer {JWT} "
			f'{{"refresh_token": "{REFRESH}"}} phone {PHONE} fayda {FAYDA}'
		)
		clean = oplog.scrub_text(text)
		self.assertNoLeakIn(clean)
		self.assertIn("login failed for j***@example.com", clean)

	def assertNoLeakIn(self, text: str):
		for secret in (JWT, REFRESH, PASSWORD, EMAIL, PHONE, FAYDA):
			self.assertNotIn(secret, text)

	def test_a_ticket_number_is_not_mistaken_for_a_phone_number(self):
		self.assertEqual(oplog.scrub_text("ticket 300100200 and 3001002A0"), "ticket 300100200 and 3001002A0")

	def test_timestamps_and_short_numbers_survive(self):
		text = "2026-11-06 10:15:00 line 1234 took 250 ms"
		self.assertEqual(oplog.scrub_text(text), text)

	def test_long_text_is_cut(self):
		clean = oplog.scrub_text("x" * 5000)
		self.assertLess(len(clean), 2100)
		self.assertTrue(clean.endswith("[truncated]"))

	def test_redact_by_key_covers_credentials_and_personal_fields(self):
		out = oplog.redact(
			{
				"password": PASSWORD,
				"new_password": PASSWORD,
				"access_token": JWT,
				"refresh_token": REFRESH,
				"Authorization": f"Bearer {JWT}",
				"cookie": f"sid={REFRESH}",
				"api_secret": "s3cr3t",
				"otp": "123456",
				"submitter_name": NAME,
				"contact_mobile": PHONE,
				"contact_email": EMAIL,
				"fayda_id": FAYDA,
				"description": "my neighbour took my land",
				"address": "Bole, Addis Ababa",
				"assigned_to": EMAIL,
				"grievance": "3001002A0",
				"status": "Assigned",
				"count": 3,
			}
		)
		self.assertEqual(out["password"], oplog.REDACTED)
		self.assertEqual(out["new_password"], oplog.REDACTED)
		self.assertEqual(out["access_token"], oplog.REDACTED)
		self.assertEqual(out["refresh_token"], oplog.REDACTED)
		self.assertEqual(out["Authorization"], oplog.REDACTED)
		self.assertEqual(out["cookie"], oplog.REDACTED)
		self.assertEqual(out["api_secret"], oplog.REDACTED)
		self.assertEqual(out["otp"], oplog.REDACTED)
		self.assertEqual(out["submitter_name"], "A*** K***")
		self.assertEqual(out["contact_mobile"], "***44")
		self.assertEqual(out["contact_email"], "j***@example.com")
		self.assertEqual(out["fayda_id"], "***56")
		self.assertEqual(out["description"], oplog.MASKED)
		self.assertEqual(out["address"], oplog.MASKED)
		self.assertEqual(out["assigned_to"], "j***@example.com")
		# What an operator needs is left alone.
		self.assertEqual(out["grievance"], "3001002A0")
		self.assertEqual(out["status"], "Assigned")
		self.assertEqual(out["count"], 3)

	def test_redact_walks_nested_values_and_scrubs_strings(self):
		out = oplog.redact({"detail": {"items": [{"password": PASSWORD}, f"mail {EMAIL}"]}})
		self.assertEqual(out["detail"]["items"][0]["password"], oplog.REDACTED)
		self.assertNotIn(EMAIL, json.dumps(out))

	def test_redact_does_not_loop_on_a_deep_structure(self):
		deep: dict = {}
		cursor = deep
		for _ in range(50):
			cursor["a"] = {}
			cursor = cursor["a"]
		self.assertIn(oplog.MASKED, json.dumps(oplog.redact(deep)))

	def test_bytes_are_never_logged(self):
		self.assertEqual(oplog.redact({"blob": b"secret bytes"})["blob"], "<12 bytes>")


class TestCorrelationId(LogCase):
	def test_a_plain_inbound_id_is_adopted(self):
		request = make_test_request("/api/v1/grievances/health", headers={"X-Request-Id": "trace-abc-12345"})
		rl.begin()
		self.assertEqual(frappe.local.request_id, "trace-abc-12345")
		self.assertIsNotNone(request)

	def test_a_missing_id_is_generated(self):
		make_test_request("/api/v1/grievances/health")
		rl.begin()
		self.assertTrue(uuid.UUID(frappe.local.request_id))

	def test_an_id_that_could_forge_a_log_line_is_replaced(self):
		for hostile in ("short", "a" * 200, "has space in it", "<script>12345678", 'quote"12345678'):
			with self.subTest(hostile=hostile):
				self._reset_request_state()
				make_test_request("/api/v1/grievances/health", headers={"X-Request-Id": hostile})
				rl.begin()
				self.assertNotEqual(frappe.local.request_id, hostile)
				self.assertTrue(uuid.UUID(frappe.local.request_id))

	def test_an_id_with_a_line_break_is_never_valid(self):
		# A WSGI server will hand one over even though the test client refuses to build it.
		for hostile in ('abc"}\n{"event":"fake"', "abcdefgh\r\nX: y", "abcdefgh\n"):
			with self.subTest(hostile=hostile):
				self.assertIsNone(oplog.valid_correlation_id(hostile))
		trace_id = "0af7651916cd43dd8448eb211c80319c"
		self.assertEqual(oplog.valid_correlation_id(trace_id), trace_id)
		self.assertEqual(oplog.valid_correlation_id("req_01HZX:7-a.b"), "req_01HZX:7-a.b")

	def test_an_id_already_set_is_kept(self):
		frappe.local.request_id = "already-set-12345"
		self.assertEqual(oplog.ensure_correlation_id("other-id-123456"), "already-set-12345")

	def test_other_paths_are_left_alone(self):
		make_test_request("/app/grievance")
		rl.begin()
		self.assertIsNone(frappe.local.request_id)

	def test_the_api_envelope_carries_the_same_id(self):
		make_test_request("/api/v1/grievances/health", headers={"X-Request-Id": "trace-abc-12345"})
		rl.begin()
		envelope = get_health()
		self.assertEqual(envelope["request_id"], "trace-abc-12345")

	def test_every_line_in_a_request_carries_the_id(self):
		make_test_request("/api/v1/grievances/health", headers={"X-Request-Id": "trace-abc-12345"})
		with CapturedLog() as captured:
			rl.begin()
			oplog.info("custom.event", detail="x")
			rl.end(Response("{}", status=200, content_type="application/json"), frappe.local.request)
		self.assertTrue(captured.events)
		self.assertEqual({e["correlation_id"] for e in captured.events}, {"trace-abc-12345"})


class TestRequestLogging(LogCase):
	def _run(self, path, status=200, body=None, headers=None, data=None, method="GET", content_type=None):
		request = make_test_request(path, method=method, headers=headers, data=data)
		# Frappe's form_dict holds the query string as well as the body.
		frappe.local.form_dict.update(request.args.to_dict())
		with CapturedLog() as captured:
			rl.begin()
			response = Response(
				json.dumps(body) if body is not None else "{}",
				status=status,
				content_type=content_type or "application/json",
			)
			rl.end(response, request)
		return captured, response

	def test_a_request_is_logged_with_status_duration_and_user(self):
		captured, response = self._run("/api/v1/grievances", status=200)
		self.assertEqual(response.headers["X-Request-Id"], frappe.local.request_id)

		(line,) = captured.named("http.request")
		self.assertEqual(line["level"], "INFO")
		self.assertEqual(line["method"], "GET")
		self.assertEqual(line["path"], "/api/v1/grievances")
		self.assertEqual(line["status"], 200)
		self.assertIsInstance(line["duration_ms"], int | float)
		self.assertGreaterEqual(line["duration_ms"], 0)
		self.assertEqual(line["correlation_id"], frappe.local.request_id)
		self.assertEqual(line["user"], "Administrator")
		self.assertEqual(len(line["user_ref"]), 12)
		self.assertEqual(line["client_ip"], "127.0.0.0")
		self.assertEqual(line["site"], frappe.local.site)
		self.assertIn("ts", line)

	def test_the_response_keeps_a_correlation_header_the_handler_already_set(self):
		request = make_test_request("/api/v1/grievances/health")
		with CapturedLog():
			rl.begin()
			response = Response("{}", status=200, content_type="application/json")
			response.headers["X-Request-Id"] = "from-handler-123"
			rl.end(response, request)
		self.assertEqual(response.headers["X-Request-Id"], "from-handler-123")

	def test_the_level_follows_the_status(self):
		for status, level in (
			(200, "INFO"),
			(302, "INFO"),
			(404, "WARNING"),
			(409, "WARNING"),
			(500, "ERROR"),
		):
			with self.subTest(status=status):
				self._reset_request_state()
				captured, _ = self._run("/api/v1/grievances", status=status)
				self.assertEqual(captured.named("http.request")[0]["level"], level)

	def test_an_error_response_logs_its_code_and_nothing_else_from_the_body(self):
		body = {
			"status": "error",
			"code": "NOT_FOUND",
			"message": f"No grievance for {EMAIL}",
			"details": {"phone": PHONE},
		}
		captured, _ = self._run("/api/v1/grievances/3001002A0/timeline", status=404, body=body)
		(line,) = captured.named("http.request")
		self.assertEqual(line["error_code"], "NOT_FOUND")
		self.assertNoLeak(captured.text)

	def test_a_frappe_error_page_reports_its_exception_type(self):
		captured, _ = self._run(
			"/api/v1/grievances", status=500, body={"exc_type": "OperationalError", "exc": "Traceback"}
		)
		self.assertEqual(captured.named("http.request")[0]["error_code"], "OperationalError")

	def test_a_download_is_not_read(self):
		request = make_test_request("/api/v1/attachments/x/download")
		response = Response(b"%PDF", status=404, content_type="application/pdf")
		with CapturedLog() as captured:
			rl.begin()
			rl.end(response, request)
		self.assertIsNone(captured.named("http.request")[0].get("error_code"))

	def test_a_slow_request_is_flagged_and_raised_to_a_warning(self):
		request = make_test_request("/api/v1/grievances")
		with CapturedLog() as captured, mock.patch.dict(frappe.local.conf, {"grievance_slow_request_ms": 1}):
			rl.begin()
			frappe.local.oan_request_started -= 0.5
			rl.end(Response("{}", status=200, content_type="application/json"), request)
		(line,) = captured.named("http.request")
		self.assertTrue(line["slow"])
		self.assertEqual(line["level"], "WARNING")
		self.assertGreaterEqual(line["duration_ms"], 500)

	def test_health_checks_are_debug_lines(self):
		request = make_test_request("/api/v1/grievances/ping")
		with CapturedLog(level=logging.INFO) as captured:
			rl.begin()
			rl.end(Response("{}", status=200, content_type="application/json"), request)
		self.assertEqual(captured.named("http.request"), [])

	def test_no_value_from_the_request_reaches_the_log(self):
		captured, _ = self._run(
			f"/api/v1/officers/{EMAIL}?phone={PHONE}&password={PASSWORD}",
			method="POST",
			headers={
				"Authorization": f"Bearer {JWT}",
				"Cookie": f"oan_refresh={REFRESH}",
				"User-Agent": f"curl/8 (contact {EMAIL})",
			},
			data={"submitter_name": NAME, "contact_mobile": PHONE, "fayda_id": FAYDA, "pwd": PASSWORD},
			body={"status": "error", "code": "VALIDATION_ERROR", "message": f"{NAME} {PHONE}"},
			status=400,
		)
		self.assertNoLeak(captured.text)
		(line,) = captured.named("http.request")
		# The path keeps its shape; the officer's address is masked in it.
		self.assertEqual(line["path"], "/api/v1/officers/j***@example.com")
		# Only the names of what was sent are recorded.
		self.assertEqual(
			line["param_names"], ["contact_mobile", "fayda_id", "password", "phone", "pwd", "submitter_name"]
		)

	def test_an_unrelated_request_is_not_logged(self):
		request = make_test_request("/assets/frappe/js/x.js")
		with CapturedLog() as captured:
			rl.begin()
			rl.end(Response("", status=200), request)
		self.assertEqual(captured.events, [])

	def test_a_logging_fault_does_not_break_the_request(self):
		request = make_test_request("/api/v1/grievances")
		with mock.patch.object(oplog, "get_logger", side_effect=RuntimeError("disk full")):
			rl.begin()
			response = Response("{}", status=200, content_type="application/json")
			rl.end(response, request)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.headers["X-Request-Id"], frappe.local.request_id)

	def test_a_request_below_the_configured_level_builds_no_log_fields(self):
		request = make_test_request("/api/v1/grievances")
		with CapturedLog(level=logging.ERROR) as captured, mock.patch.object(oplog, "scrub_path") as scrub:
			rl.begin()
			response = Response("{}", status=200, content_type="application/json")
			rl.end(response, request)
		scrub.assert_not_called()
		self.assertEqual(captured.events, [])
		# The caller still gets its id back.
		self.assertEqual(response.headers["X-Request-Id"], frappe.local.request_id)

	def test_a_disabled_level_costs_nothing(self):
		with CapturedLog(level=logging.ERROR), mock.patch.object(oplog, "redact") as redact:
			oplog.info("sla.something", grievance="G-1")
		redact.assert_not_called()


class TestAuthenticationFailures(LogCase):
	def setUp(self):
		super().setUp()
		ensure_routes_registered()

	def _refused(self, status, **request_kwargs):
		request = make_test_request("/api/v1/service-providers", **request_kwargs)
		with CapturedLog() as captured:
			rl.begin()
			rl.end(
				Response(
					json.dumps({"code": "AUTHENTICATION_ERROR"}),
					status=status,
					content_type="application/json",
				),
				request,
			)
		return captured

	def test_a_request_refused_by_the_auth_layer_is_logged_with_its_correlation_id(self):
		"""The auth hook raises after `before_request`, so the refusal has an id to be traced by."""
		frappe.set_user("Guest")
		frappe.local.session = frappe._dict({"user": "Guest"})
		headers = {"Authorization": f"Bearer {JWT}", "X-Request-Id": "trace-abc-12345"}
		request = make_test_request("/api/v1/service-providers", headers=headers)
		rl.begin()
		with configured_keys(), CapturedLog() as captured:
			with self.assertRaises(frappe.AuthenticationError):
				validate_jwt_request(request)
			rl.end(
				Response(
					json.dumps({"code": "AUTHENTICATION_ERROR"}), status=401, content_type="application/json"
				),
				request,
			)
		frappe.set_user("Administrator")

		(failure,) = captured.named("security.auth_failure")
		self.assertEqual(failure["correlation_id"], "trace-abc-12345")
		self.assertEqual(failure["status"], 401)
		self.assertEqual(failure["path"], "/api/v1/service-providers")
		self.assertEqual(failure["error_code"], "AUTHENTICATION_ERROR")
		self.assertEqual(failure["auth_scheme"], "bearer")
		self.assertEqual(failure["level"], "WARNING")
		self.assertNoLeak(captured.text)

	def test_a_failed_sign_in_names_the_account_only_masked(self):
		captured = self._refused(
			401, method="POST", data={"usr": EMAIL, "pwd": PASSWORD}, headers={"User-Agent": "okhttp/4"}
		)
		(failure,) = captured.named("security.auth_failure")
		self.assertEqual(failure["attempted_user"], "j***@example.com")
		self.assertEqual(failure["attempted_user_ref"], oplog.user_ref(EMAIL))
		self.assertEqual(failure["auth_scheme"], "none")
		self.assertEqual(failure["user_agent"], "okhttp/4")
		self.assertNoLeak(captured.text)

	def test_the_scheme_is_recorded_without_the_credential(self):
		cases = (
			({"Authorization": f"Bearer {JWT}"}, "bearer"),
			({"Authorization": "token key:secret-value"}, "token"),
			({"Authorization": "Basic dXNlcjpwYXNz"}, "basic"),
			({"Authorization": "Negotiate abcdef"}, "other"),
			({"Cookie": f"oan_access={JWT}"}, "cookie"),
			({}, "none"),
		)
		for headers, scheme in cases:
			with self.subTest(scheme=scheme):
				captured = self._refused(401, headers=headers)
				self.assertEqual(captured.named("security.auth_failure")[0]["auth_scheme"], scheme)
				self.assertNoLeak(captured.text)
				self.assertNotIn("secret-value", captured.text)
				self.assertNotIn("dXNlcjpwYXNz", captured.text)

	def test_permission_denied_and_rate_limiting_are_security_events(self):
		self.assertEqual(len(self._refused(403).named("security.permission_denied")), 1)
		self.assertEqual(len(self._refused(429).named("security.rate_limited")), 1)

	def test_other_statuses_raise_no_security_event(self):
		for status in (200, 400, 404, 500):
			with self.subTest(status=status):
				captured = self._refused(status)
				self.assertFalse([e for e in captured.events if e["event"].startswith("security.")])

	def test_a_signed_in_user_denied_is_identified_only_by_reference(self):
		with mock.patch.dict(frappe.local.session, {"user": EMAIL}):
			captured = self._refused(403)
		(denied,) = captured.named("security.permission_denied")
		self.assertEqual(denied["user_ref"], oplog.user_ref(EMAIL))
		self.assertNoLeak(captured.text)

	def test_the_password_reset_event_carries_no_password(self):
		with CapturedLog() as captured:
			oplog.security_event(
				"temporary_password_reissued",
				logging.INFO,
				user="admin@example.com",
				target_user=EMAIL,
				new_password=PASSWORD,
				temporary_password=PASSWORD,
			)
		(event,) = captured.named("security.temporary_password_reissued")
		self.assertEqual(event["target_user"], "j***@example.com")
		self.assertEqual(event["new_password"], oplog.REDACTED)
		self.assertEqual(event["temporary_password"], oplog.REDACTED)
		self.assertNoLeak(captured.text)


class TestErrorLogging(LogCase):
	def _fail(self, **kwargs):
		try:
			raise ValueError(f"bad input for {EMAIL}: password={PASSWORD} fayda {FAYDA} Bearer {JWT}")
		except ValueError:
			with mock.patch.object(frappe, "log_error") as stored, CapturedLog() as captured:
				oplog.log_error(title="Something failed", **kwargs)
		return stored, captured

	def test_an_error_is_logged_with_its_type_and_correlation_id(self):
		frappe.local.request_id = "trace-abc-12345"
		_, captured = self._fail()
		(line,) = captured.named("error")
		self.assertEqual(line["level"], "ERROR")
		self.assertEqual(line["correlation_id"], "trace-abc-12345")
		self.assertEqual(line["title"], "Something failed")
		self.assertEqual(line["exception"], "ValueError")
		self.assertIn("ValueError", line["detail"])

	def test_the_error_log_row_is_scrubbed_and_traceable(self):
		frappe.local.request_id = "trace-abc-12345"
		stored, captured = self._fail()
		stored.assert_called_once()
		self.assertEqual(stored.call_args.kwargs["title"], "Something failed")
		message = stored.call_args.kwargs["message"]
		self.assertTrue(message.startswith("correlation_id: trace-abc-12345\n"))
		self.assertIn("ValueError", message)
		self.assertNoLeak(message)
		self.assertNoLeak(captured.text)

	def test_an_explicit_message_is_scrubbed_too(self):
		with mock.patch.object(frappe, "log_error") as stored, CapturedLog() as captured:
			oplog.log_error(title="t", message=f"user {EMAIL} token={JWT}", defer_insert=True)
		self.assertNotIn(EMAIL, stored.call_args.kwargs["message"])
		self.assertTrue(stored.call_args.kwargs["defer_insert"])
		self.assertNoLeak(captured.text)

	def test_a_failing_error_log_still_writes_the_line(self):
		with (
			mock.patch.object(frappe, "log_error", side_effect=RuntimeError("db down")),
			CapturedLog() as captured,
		):
			with self.assertRaises(RuntimeError):
				oplog.log_error(title="t", message="m")
		self.assertEqual(len(captured.named("error")), 1)

	def test_api_errors_use_the_correlation_id_set_before_authentication(self):
		from oan_auth_service.api.utils import handle_api_errors

		@handle_api_errors
		def boom():
			raise ValueError(f"cannot find {EMAIL}")

		make_test_request("/api/v1/grievances", headers={"X-Request-Id": "trace-abc-12345"})
		rl.begin()
		with mock.patch.object(frappe, "log_error"):
			envelope = boom()
		self.assertEqual(envelope["request_id"], "trace-abc-12345")


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
		@oplog.logged_job("explodes")
		def explode():
			raise RuntimeError("boom")

		with CapturedLog() as captured:
			with self.assertRaises(RuntimeError):
				explode()
		self.assertEqual(captured.named("job.failed")[0]["exception"], "RuntimeError")
		self.assertIsNone(frappe.local.request_id)

	def test_an_unreadable_case_costs_the_line_not_the_work(self):
		class Broken:
			def get(self, *_):
				raise RuntimeError("half-built document")

		with CapturedLog() as captured:
			oplog.sla_event("clock_started", Broken())
			oplog.escalation_event("escalated", Broken())
		self.assertEqual(captured.events, [])

	def test_personal_fields_on_a_case_never_reach_an_sla_line(self):
		with CapturedLog() as captured:
			oplog.sla_event("clock_started", _case())
		self.assertNoLeak(captured.text)
		(line,) = captured.events
		for field in ("submitter_name", "contact_mobile", "contact_email"):
			self.assertNotIn(field, line)


class TestLoggerConfiguration(FrappeTestCase):
	def test_it_writes_json_lines_at_the_configured_level_and_size(self):
		key = oplog._LOGGER_KEY
		logger = logging.getLogger(key)
		saved = (list(logger.handlers), logger.level, logger.__dict__.get("_oan_configured"))
		saved_registry = frappe.loggers.pop(key, None)
		for handler in saved[0]:
			logger.removeHandler(handler)
		logger._oan_configured = False
		try:
			with mock.patch.dict(
				frappe.local.conf,
				{
					"grievance_log_level": "debug",
					"grievance_log_max_bytes": 12345,
					"grievance_log_backup_count": 3,
				},
			):
				fresh = oplog.get_logger()
			self.assertIs(fresh, logger)
			self.assertEqual(fresh.level, logging.DEBUG)
			self.assertTrue(fresh.handlers)
			for handler in fresh.handlers:
				self.assertIsInstance(handler.formatter, oplog.JsonFormatter)
				if hasattr(handler, "maxBytes"):
					self.assertEqual(handler.maxBytes, 12345)
					self.assertEqual(handler.backupCount, 3)
			# Asking again does not stack a second set of handlers.
			count = len(fresh.handlers)
			self.assertEqual(len(oplog.get_logger().handlers), count)
		finally:
			for handler in list(logger.handlers):
				logger.removeHandler(handler)
				handler.close()
			for handler in saved[0]:
				logger.addHandler(handler)
			logger.setLevel(saved[1])
			logger._oan_configured = saved[2]
			if saved_registry is not None:
				frappe.loggers[key] = saved_registry
			else:
				frappe.loggers.pop(key, None)

	def test_the_level_defaults_to_info_and_ignores_nonsense(self):
		self.assertEqual(oplog._level_from_conf(), logging.INFO)
		with mock.patch.dict(frappe.local.conf, {"grievance_log_level": "loud"}):
			self.assertEqual(oplog._level_from_conf(), logging.INFO)
		with mock.patch.dict(frappe.local.conf, {"grievance_log_level": "warning"}):
			self.assertEqual(oplog._level_from_conf(), logging.WARNING)

	def test_a_line_is_one_json_object(self):
		formatter = oplog.JsonFormatter()
		record = logging.LogRecord("x", logging.INFO, __file__, 1, "evt", None, None)
		record.oan_fields = {"a": 1, "b": "é"}
		line = formatter.format(record)
		self.assertNotIn("\n", line)
		self.assertEqual(json.loads(line)["a"], 1)
		self.assertEqual(json.loads(line)["event"], "evt")
