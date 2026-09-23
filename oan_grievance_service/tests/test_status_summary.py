# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""STG-397: the all-grievances queue exposes fixed statuses, and nothing else."""

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1._options import (
	_is_terminal,
	expand_status_filter,
	get_status_options,
	get_status_summary,
	public_status,
)
from oan_grievance_service.api.v1.grievance import list_grievances, summary
from oan_grievance_service.tests.fixtures import a_grievance, discard_grievance

STAFF_QUEUE_STATUSES = (
	"All",
	"Assigned",
	"In Progress",
	"Require More Info",
	"Rejected",
	"Resolved",
	"Closed",
)


class TestStatusSummary(FrappeTestCase):
	def test_terminal_uses_the_workflow_and_is_non_terminal_when_absent(self):
		workflow = {"In Progress": 0, "Rejected": 1, "Closed": 1}
		self.assertEqual(_is_terminal(("In Progress",), workflow), 0)
		self.assertEqual(_is_terminal(("Rejected",), workflow), 1)
		self.assertEqual(_is_terminal(("Not A State",), workflow), 0)
		self.assertEqual(_is_terminal(("Closed",), None), 0)
		self.assertEqual(_is_terminal((), workflow), 0)

	def test_options_are_only_the_queue_statuses_in_order_for_staff(self):
		frappe.set_user("Administrator")
		options = get_status_options()
		self.assertEqual([row["status"] for row in options], list(STAFF_QUEUE_STATUSES))
		self.assertEqual([row["order"] for row in options], [1, 2, 3, 4, 5, 6, 7])
		by_status = {row["status"]: row for row in options}
		self.assertEqual(by_status["All"]["is_terminal"], 0)
		self.assertEqual(by_status["Assigned"]["is_terminal"], 0)
		self.assertEqual(by_status["In Progress"]["is_terminal"], 0)
		self.assertEqual(by_status["Require More Info"]["is_terminal"], 0)
		self.assertEqual(by_status["Rejected"]["is_terminal"], 1)
		self.assertEqual(by_status["Resolved"]["is_terminal"], 0)
		self.assertEqual(by_status["Closed"]["is_terminal"], 1)
		self.assertNotIn("Draft", by_status)
		self.assertNotIn("Submitted", by_status)
		self.assertNotIn("More Info Needed", by_status)

	def test_summary_counts_roll_other_workflow_states_into_the_cards(self):
		frappe.set_user("Administrator")
		before = {card["status"]: card["count"] for card in get_status_summary()}

		created = {
			"submitted": a_grievance(),
			"assigned": a_grievance(),
			"more_info": a_grievance(),
			"rejected": a_grievance(),
			"resolved": a_grievance(),
			"closed": a_grievance(),
			"draft": a_grievance(workflow_state="Draft"),
		}
		self.addCleanup(self._discard, created)
		frappe.db.set_value(
			"Grievance", created["assigned"].name, {"status": "Assigned", "workflow_state": "Assigned"}
		)
		frappe.db.set_value(
			"Grievance",
			created["more_info"].name,
			{"status": "More Info Needed", "workflow_state": "More Info Needed"},
		)
		frappe.db.set_value(
			"Grievance", created["rejected"].name, {"status": "Rejected", "workflow_state": "Rejected"}
		)
		frappe.db.set_value(
			"Grievance", created["resolved"].name, {"status": "Resolved", "workflow_state": "Resolved"}
		)
		frappe.db.set_value(
			"Grievance", created["closed"].name, {"status": "Closed", "workflow_state": "Closed"}
		)

		after = {card["status"]: card["count"] for card in get_status_summary()}
		self.assertEqual(after["All"] - before["All"], 6)
		self.assertEqual(after["Assigned"] - before["Assigned"], 1)
		self.assertEqual(after["In Progress"] - before["In Progress"], 1)
		self.assertEqual(after["Require More Info"] - before["Require More Info"], 1)
		self.assertEqual(after["Rejected"] - before["Rejected"], 1)
		self.assertEqual(after["Resolved"] - before["Resolved"], 1)
		self.assertEqual(after["Closed"] - before["Closed"], 1)
		self.assertEqual(
			after["All"],
			after["Assigned"]
			+ after["In Progress"]
			+ after["Require More Info"]
			+ after["Rejected"]
			+ after["Resolved"]
			+ after["Closed"],
		)

		res = summary()
		self.assertEqual(res["status"], "success")
		self.assertEqual([card["status"] for card in res["data"]["cards"]], list(STAFF_QUEUE_STATUSES))

	def test_list_hides_drafts_and_reports_the_queue_status(self):
		frappe.set_user("Administrator")
		open_case = a_grievance()
		draft = a_grievance(workflow_state="Draft")
		self.addCleanup(discard_grievance, open_case.name)
		self.addCleanup(discard_grievance, draft.name)

		self.assertEqual(public_status("Submitted"), "In Progress")
		self.assertEqual(public_status("Assigned"), "Assigned")
		self.assertEqual(public_status("More Info Needed"), "Require More Info")
		self.assertIsNone(expand_status_filter(["all"]))
		self.assertIn("Assigned", expand_status_filter(["Assigned"]))
		self.assertIn("More Info Needed", expand_status_filter(["Require More Info"]))

		res = list_grievances(status="In Progress", page_size=100)
		names = {item["name"] for item in res["data"]["items"]}
		self.assertIn(open_case.name, names)
		self.assertNotIn(draft.name, names)
		shown = next(item for item in res["data"]["items"] if item["name"] == open_case.name)
		self.assertEqual(shown["status"], "In Progress")

	def _discard(self, created):
		frappe.set_user("Administrator")
		for doc in created.values():
			discard_grievance(doc.name)
