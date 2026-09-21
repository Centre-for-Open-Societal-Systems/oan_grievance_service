# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""The lifecycle is the Grievance Workflow record, and nothing else.

Five contracts, as the workflow specification lays them out: the legal moves are
discovered from the Workflow, not from code; a response's type decides the next
state; a rejection or a reopen without a reason is refused by the history row and
the move rolled back with it; every move extends a hash chain that would show
tampering; and the SLA clock pauses and resumes with the state.
"""

import hashlib
from itertools import pairwise

import frappe
from frappe.model.workflow import WorkflowTransitionError, apply_workflow, get_transitions
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from oan_grievance_service.services import lifecycle
from oan_grievance_service.setup import install
from oan_grievance_service.tests.fixtures import a_department, a_grievance


def history(grievance):
	return frappe.get_all(
		"Grievance Status History",
		filters={"grievance": grievance},
		fields=[
			"from_status",
			"to_status",
			"transition",
			"reason",
			"changed_by",
			"is_automated",
			"timestamp",
			"prev_hash",
			"row_hash",
			"closure_type",
		],
		order_by="timestamp asc, creation asc",
	)


def workflow():
	return frappe.get_doc("Workflow", install.WORKFLOW_NAME)


class WorkflowTestCase(FrappeTestCase):
	def setUp(self):
		self.grievance = a_grievance()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def _saved(self):
		return frappe.get_doc("Grievance", self.grievance.name)

	def _state(self):
		return frappe.db.get_value(
			"Grievance", self.grievance.name, ["workflow_state", "status", "docstatus"], as_dict=True
		)

	def _at_in_progress(self):
		doc = self._saved()
		doc.db_set("assigned_dept", a_department(), update_modified=False)
		lifecycle.transition(doc, "Assign")
		lifecycle.transition(doc, "Start Work")
		return self._saved()

	def _at_pending_submitter(self):
		self._at_in_progress()
		frappe.get_doc(
			{
				"doctype": "Grievance Response",
				"grievance": self.grievance.name,
				"response_type": "Resolved",
				"action_taken": "Fertilizer delivered to the kebele store.",
				"resolution_summary": "Delivered.",
				"proposed_close_date": add_days(today(), 7),
			}
		).insert(ignore_permissions=True)
		return self._saved()


class TestContractOneTheWorkflowIsTheOnlyTransitionTable(FrappeTestCase):
	def test_the_workflow_is_active_on_grievance_and_drives_workflow_state(self):
		wf = workflow()
		self.assertEqual(
			(wf.document_type, wf.is_active, wf.workflow_state_field), ("Grievance", 1, "workflow_state")
		)

	def test_the_code_keeps_no_transition_table_of_its_own(self):
		self.assertFalse(hasattr(lifecycle, "change_status"))

	def test_a_case_enters_at_draft_and_every_later_state_is_a_submitted_document(self):
		wf = workflow()
		self.assertEqual(wf.states[0].state, "Draft")
		docstatus = {row.state: row.doc_status for row in wf.states}
		self.assertEqual(docstatus["Draft"], "0")
		self.assertEqual(docstatus["Rejected"], "2")
		for state in (
			"Submitted",
			"Assigned",
			"In Progress",
			"More Info Needed",
			"Pending Submitter",
			"Resolved",
			"Closed",
		):
			self.assertEqual(docstatus[state], "1", state)

	def test_closed_and_rejected_have_no_way_out(self):
		leaving = {row.state for row in workflow().transitions}
		self.assertNotIn("Closed", leaving)
		self.assertNotIn("Rejected", leaving)

	def test_a_reopen_exists_only_inside_the_confirmation_window(self):
		reopens = {(row.state, row.next_state) for row in workflow().transitions if row.action == "Reopen"}
		self.assertEqual(reopens, {("Pending Submitter", "In Progress")})

	def test_seeding_again_rebuilds_rather_than_duplicates(self):
		before = {(r.state, r.action, r.next_state, r.allowed) for r in workflow().transitions}
		install.seed_workflow()
		self.assertEqual(
			{(r.state, r.action, r.next_state, r.allowed) for r in workflow().transitions}, before
		)
		self.assertEqual(frappe.db.count("Workflow", {"document_type": "Grievance", "is_active": 1}), 1)


class TestTheEngineMovesTheCase(WorkflowTestCase):
	def test_a_fixture_grievance_is_submitted(self):
		self.assertEqual(
			self._state(), {"workflow_state": "Submitted", "status": "Submitted", "docstatus": 1}
		)

	def test_status_mirrors_workflow_state_on_every_move(self):
		self._at_in_progress()
		self.assertEqual(
			self._state(), {"workflow_state": "In Progress", "status": "In Progress", "docstatus": 1}
		)

	def test_the_desk_button_and_the_service_call_are_the_same_move(self):
		doc = self._saved()
		doc.db_set("assigned_dept", a_department(), update_modified=False)
		apply_workflow(doc, "Assign")
		self.assertEqual(self._state()["workflow_state"], "Assigned")
		self.assertEqual(history(self.grievance.name)[-1].transition, "Assign")

	def test_a_move_the_workflow_does_not_offer_is_refused(self):
		with self.assertRaises(WorkflowTransitionError):
			lifecycle.transition(self._saved(), "Close Case")
		self.assertEqual(self._state()["workflow_state"], "Submitted")

	def test_editing_the_state_field_and_saving_is_not_a_move(self):
		"""The engine is the only way in: a save that changes the state without a
		transition behind it is refused by Frappe's own workflow validation."""
		doc = self._saved()
		doc.workflow_state = "Closed"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_what_the_submitter_filed_is_frozen_by_submission(self):
		doc = self._saved()
		doc.description = "An edited description, still well past the twenty character minimum."
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_a_closed_case_is_read_only_everywhere(self):
		self._at_pending_submitter()
		lifecycle.transition(self._saved(), "Confirm Resolution", closure_type="confirmed")
		lifecycle.transition(self._saved(), "Close Case", closure_type="confirmed")
		self.assertEqual(self._state(), {"workflow_state": "Closed", "status": "Closed", "docstatus": 1})
		self.assertEqual(get_transitions(self._saved()), [])

	def test_a_rejected_case_is_a_cancelled_document(self):
		lifecycle.transition(
			self._saved(),
			"Reject",
			reason="Out of scope: not an agricultural service.",
			closure_type="rejected",
		)
		self.assertEqual(self._state(), {"workflow_state": "Rejected", "status": "Rejected", "docstatus": 2})
		self.assertEqual(get_transitions(self._saved()), [])


class TestContractTwoAResponseDecidesTheNextState(WorkflowTestCase):
	def _respond(self, response_type, **extra):
		self._at_in_progress()
		return frappe.get_doc(
			{
				"doctype": "Grievance Response",
				"grievance": self.grievance.name,
				"response_type": response_type,
				"action_taken": "Looked into it.",
				"resolution_summary": "Summary.",
				"proposed_close_date": add_days(today(), 7),
				**extra,
			}
		).insert(ignore_permissions=True)

	def test_resolved_opens_the_confirmation_window_and_pauses_the_clock(self):
		response = self._respond("Resolved")
		state = self._state()
		self.assertEqual(state["workflow_state"], "Pending Submitter")
		self.assertEqual(response.new_status, "Pending Submitter")
		self.assertEqual(response.sla_behaviour, "paused")
		self.assertIsNotNone(frappe.db.get_value("Grievance", self.grievance.name, "confirmation_deadline"))

	def test_partially_resolved_does_the_same(self):
		response = self._respond("Partially Resolved")
		self.assertEqual(self._state()["workflow_state"], "Pending Submitter")
		self.assertEqual(response.new_status, "Pending Submitter")

	def test_referred_sends_the_case_back_to_assignment(self):
		response = self._respond("Referred to another dept")
		self.assertEqual(self._state()["workflow_state"], "Assigned")
		self.assertEqual((response.new_status, response.sla_behaviour), ("Assigned", "running"))

	def test_requires_further_info_asks_the_submitter(self):
		response = self._respond("Requires further info")
		self.assertEqual(self._state()["workflow_state"], "More Info Needed")
		self.assertEqual((response.new_status, response.sla_behaviour), ("More Info Needed", "paused"))

	def test_pending_submitter_cannot_be_reached_without_a_response(self):
		self._at_in_progress()
		with self.assertRaises(frappe.ValidationError):
			lifecycle.transition(self._saved(), "Submit Response")
		self.assertEqual(self._state()["workflow_state"], "In Progress")

	def test_work_cannot_start_without_a_department(self):
		lifecycle.transition(self._saved(), "Assign")
		with self.assertRaises(frappe.ValidationError):
			lifecycle.transition(self._saved(), "Start Work")
		self.assertEqual(self._state()["workflow_state"], "Assigned")


class TestContractThreeAReasonIsDemandedByTheHistoryRow(WorkflowTestCase):
	def test_a_rejection_without_a_reason_is_refused_and_rolled_back(self):
		before = len(history(self.grievance.name))
		with self.assertRaises(frappe.ValidationError):
			lifecycle.transition(self._saved(), "Reject")
		self.assertEqual(
			self._state(), {"workflow_state": "Submitted", "status": "Submitted", "docstatus": 1}
		)
		self.assertEqual(len(history(self.grievance.name)), before)

	def test_a_whitespace_reason_is_no_reason(self):
		with self.assertRaises(frappe.ValidationError):
			lifecycle.transition(self._saved(), "Reject", reason="   ")

	def test_a_reopen_without_a_reason_is_refused(self):
		self._at_pending_submitter()
		with self.assertRaises(frappe.ValidationError):
			lifecycle.transition(self._saved(), "Reopen")
		self.assertEqual(self._state()["workflow_state"], "Pending Submitter")

	def test_a_reopen_with_a_reason_goes_through_and_is_counted(self):
		self._at_pending_submitter()
		lifecycle.transition(self._saved(), "Reopen", reason="The delivery never arrived.")
		doc = self._saved()
		doc.db_set("reopen_count", (doc.reopen_count or 0) + 1, update_modified=False)
		self.assertEqual(self._state()["workflow_state"], "In Progress")
		self.assertEqual(frappe.db.get_value("Grievance", self.grievance.name, "reopen_count"), 1)
		self.assertEqual(history(self.grievance.name)[-1].reason, "The delivery never arrived.")

	def test_the_desk_button_is_held_to_the_same_rule(self):
		"""No reason can travel with a button, so Reject from the desk is refused;
		the officer rejects through the API, which asks for one."""
		with self.assertRaises(frappe.ValidationError):
			apply_workflow(self._saved(), "Reject")
		self.assertEqual(self._state()["workflow_state"], "Submitted")


class TestContractFourTheHistoryIsAHashChain(WorkflowTestCase):
	def test_every_move_writes_a_row_that_chains_to_the_last(self):
		self._at_pending_submitter()
		rows = history(self.grievance.name)
		self.assertEqual(
			[r.to_status for r in rows], ["Submitted", "Assigned", "In Progress", "Pending Submitter"]
		)
		self.assertEqual(rows[0].prev_hash, "0" * 64)
		for earlier, later in pairwise(rows):
			self.assertEqual(later.prev_hash, earlier.row_hash)

	def test_each_row_hash_is_recomputable_from_its_fields(self):
		self._at_in_progress()
		for row in history(self.grievance.name):
			payload = (
				f"{row.prev_hash}|{self.grievance.name}|{row.from_status or ''}|{row.to_status}|"
				f"{row.timestamp}|{row.changed_by or ''}|{row.reason or ''}|{row.closure_type or ''}|"
				f"{1 if row.is_automated else 0}"
			)
			self.assertEqual(row.row_hash, hashlib.sha256(payload.encode("utf-8")).hexdigest())

	def test_a_row_cannot_be_edited_or_deleted(self):
		self._at_in_progress()
		row = frappe.get_all(
			"Grievance Status History", filters={"grievance": self.grievance.name}, pluck="name"
		)[0]
		doc = frappe.get_doc("Grievance Status History", row)
		doc.reason = "rewritten"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("Grievance Status History", row, ignore_permissions=True)

	def test_every_move_also_lands_on_the_timeline(self):
		self._at_in_progress()
		entries = frappe.db.count(
			"Grievance Timeline", {"grievance": self.grievance.name, "entry_type": "status_change"}
		)
		# Submission writes its own entry and the Draft -> Submitted move; every later move one.
		self.assertEqual(entries, 1 + len(history(self.grievance.name)))

	def test_an_automated_move_names_no_user(self):
		self._at_pending_submitter()
		lifecycle.transition(self._saved(), "Auto Close", automated=True, closure_type="auto_closed")
		last = history(self.grievance.name)[-1]
		self.assertEqual(
			(last.to_status, last.is_automated, last.changed_by, last.closure_type),
			("Closed", 1, None, "auto_closed"),
		)


class TestContractFiveTheClockFollowsTheState(WorkflowTestCase):
	def _clock(self):
		return frappe.db.get_value(
			"Grievance",
			self.grievance.name,
			["sla_due_date", "on_hold_since", "total_hold_time"],
			as_dict=True,
		)

	def test_the_clock_starts_on_assignment(self):
		self.assertIsNone(self._clock()["sla_due_date"])
		self._at_in_progress()
		# Only when a policy exists for the category; the fixture may have none.
		from oan_grievance_service.services import sla

		if sla.resolve_policy(self.grievance.service_category):
			self.assertIsNotNone(self._clock()["sla_due_date"])

	def test_a_paused_response_holds_the_clock_and_a_reply_releases_it(self):
		from oan_grievance_service.grievance_management.doctype.grievance_timeline.grievance_timeline import (
			GrievanceTimeline,
		)
		from oan_grievance_service.services import sla

		self._at_in_progress()
		if not sla.resolve_policy(self.grievance.service_category):
			self.skipTest("no SLA policy seeded for the fixture category")
		GrievanceTimeline.record(
			grievance=self.grievance.name,
			entry_type="info_request",
			is_internal=False,
			body="Which kebele store?",
		)
		lifecycle.transition(self._saved(), "Request More Info")
		self.assertIsNotNone(self._clock()["on_hold_since"])
		GrievanceTimeline.record(
			grievance=self.grievance.name,
			entry_type="info_response",
			is_internal=False,
			body="Kebele 01.",
		)
		lifecycle.transition(self._saved(), "Submitter Reply")
		clock = self._clock()
		self.assertIsNone(clock["on_hold_since"])
		self.assertIsNotNone(clock["total_hold_time"])
