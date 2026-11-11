"""The farmer registry's failures are logged without the farmer records they carried."""

import json
from unittest import mock

import frappe
import requests
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.services import farmer_registry
from oan_grievance_service.tests.test_grievance_log import CapturedLog

URL = "https://registry.example.org/register-data/search_in_a_register"
NAME = "Abebe Kebede"
PHONE = "0911223344"
FAYDA = "1234567890123456"
EMAIL = "abebe.kebede@example.org"
RECORD = {"name": NAME, "phone": PHONE, "foundational_id": FAYDA, "email": EMAIL, "kebele": "Bole 04"}
PAYLOAD = {"register_id": "farmer_register", "subject_name": NAME, "subject_id": FAYDA}


def a_reply(status=200, header=None, body=None):
	text = json.dumps(
		{"response_header": header or {}, "response_body": {"response_payload": [RECORD]}, **(body or {})}
	)
	reply = mock.Mock(status_code=status, ok=status < 400, text=text, content=text.encode())
	reply.json.return_value = json.loads(text)
	return reply


class TestFarmerRegistryLogging(FrappeTestCase):
	def _post(self, reply=None, error=None):
		session = mock.Mock()
		if error:
			session.post.side_effect = error
		else:
			session.post.return_value = reply
		with CapturedLog() as captured:
			try:
				farmer_registry._post(session, URL, PAYLOAD, pagination={"search_text": FAYDA})
			except Exception as exc:
				captured.raised = exc
		return captured

	def assertNothingPersonalLogged(self, captured):
		for secret in (NAME, "Abebe", "Kebede", PHONE, FAYDA, EMAIL, "Bole 04"):
			self.assertNotIn(secret, captured.text, msg=f"{secret!r} reached the log")

	def test_a_failed_status_logs_the_call_not_the_payload_or_the_body(self):
		captured = self._post(a_reply(status=500))
		self.assertIsInstance(captured.raised, frappe.ValidationError)
		(line,) = captured.named("farmer_registry.error")
		self.assertEqual(line["level"], "ERROR")
		self.assertEqual(line["url"], URL)
		self.assertEqual(line["status"], 500)
		# The names of what was sent, and how big the reply was: enough to debug, nothing more.
		self.assertEqual(line["payload_keys"], ["register_id", "subject_id", "subject_name"])
		self.assertGreater(line["response_bytes"], 0)
		for field in ("payload", "body", "response_body"):
			self.assertNotIn(field, line)
		self.assertNothingPersonalLogged(captured)

	def test_a_registry_error_logs_its_reason_with_personal_data_scrubbed(self):
		header = {
			"response_status": "ERROR",
			"response_error_message": f"No record for {FAYDA} / {PHONE} / {EMAIL}",
		}
		captured = self._post(a_reply(status=200, header=header))
		(line,) = captured.named("farmer_registry.error")
		self.assertEqual(line["registry_status"], "ERROR")
		self.assertIn("No record for", line["reason"])
		self.assertIn("***56", line["reason"])
		self.assertNothingPersonalLogged(captured)
		# The caller still gets the registry's message, as before.
		self.assertIn("No record for", str(captured.raised))

	def test_an_unreachable_registry_logs_what_failed(self):
		captured = self._post(error=requests.ConnectionError(f"could not reach {URL}"))
		(line,) = captured.named("farmer_registry.error")
		self.assertEqual(line["reason"], "unreachable")
		self.assertEqual(line["exception"], "ConnectionError")
		self.assertIn("registry.example.org", line["detail"])
		self.assertNothingPersonalLogged(captured)

	def test_a_good_reply_logs_nothing(self):
		captured = self._post(a_reply(status=200, header={"response_status": "SUCCESS"}))
		self.assertEqual(captured.events, [])

	def test_the_line_carries_the_requests_correlation_id(self):
		frappe.local.request_id = "trace-abc-12345"
		self.addCleanup(setattr, frappe.local, "request_id", None)
		captured = self._post(a_reply(status=500))
		self.assertEqual(captured.named("farmer_registry.error")[0]["correlation_id"], "trace-abc-12345")
