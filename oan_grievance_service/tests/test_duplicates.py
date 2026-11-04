# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Duplicate grievances: flagging a likely repeat, the officer's ruling, the read-only state,
and the dashboard counts that include them."""

import random

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1 import change_request, grievance
from oan_grievance_service.api.v1._options import get_status_summary, public_status
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import dashboard_rollup, duplicates, identity
from oan_grievance_service.services import ticket_number as tn
from oan_grievance_service.tests.fixtures import (
	a_grievance,
	a_grievance_type,
	a_leaf_area,
	a_service_category,
	discard_grievance,
)
from oan_grievance_service.tests.test_category_assignment import _keep_transaction

FERTILIZER = "Fertilizer allocated for the season has not reached the kebele store."
CANAL = "My irrigation canal is blocked by silt and the water cannot reach my plot at all."


def a_submitter_profile():
	"""A new farmer's profile, found by a unique Fayda ID; the caller deletes it."""
	fayda = str(random.randrange(10**15, 10**16))
	mobile = f"+2519{random.randrange(10**7, 10**8)}"
	return identity.find_or_create_submitter(
		{"fayda_id": fayda, "submitter_name": "Test Farmer", "contact_mobile": mobile}
	)


class TestDuplicateScoring(FrappeTestCase):
	def test_normalize_folds_case_punctuation_and_geez_homophones(self):
		self.assertEqual(duplicates.normalize("  Fertilizer, NOT arrived!! "), "fertilizer not arrived")
		# The same word spelled with either of two homophone letters is one word.
		self.assertEqual(duplicates.normalize("ሐገር"), duplicates.normalize("ሀገር"))
		self.assertEqual(duplicates.normalize("ሠላም"), duplicates.normalize("ሰላም"))
		self.assertEqual(duplicates.normalize("ዐለም"), duplicates.normalize("አለም"))

	def test_description_similarity_ranks_alike_above_unalike(self):
		same = duplicates.description_similarity(FERTILIZER, FERTILIZER.upper().replace(".", "!!"))
		paraphrase = duplicates.description_similarity(
			FERTILIZER, "The fertilizer allocated this season never arrived at our kebele store."
		)
		unrelated = duplicates.description_similarity(FERTILIZER, CANAL)
		self.assertAlmostEqual(same, 1.0)
		self.assertGreater(paraphrase, 0.5)
		self.assertLess(unrelated, 0.3)
		self.assertEqual(duplicates.description_similarity("", FERTILIZER), 0.0)

	def test_labels_alone_can_never_reach_the_threshold(self):
		self.assertLess(duplicates.W_CATEGORY + duplicates.W_TYPE, duplicates.THRESHOLD)
		self.assertAlmostEqual(duplicates.W_DESCRIPTION + duplicates.W_CATEGORY + duplicates.W_TYPE, 1.0)

	def test_identical_text_under_another_category_is_still_flagged(self):
		first = frappe._dict(description=FERTILIZER, service_category="A", grievance_type="X")
		other = frappe._dict(description=FERTILIZER, service_category="B", grievance_type="Y")
		self.assertAlmostEqual(duplicates.score(first, other), duplicates.W_DESCRIPTION)
		self.assertGreaterEqual(duplicates.score(first, other), duplicates.THRESHOLD)


class TestDuplicateFlow(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.farmer = a_submitter_profile()
		self.other_farmer = a_submitter_profile()
		self.profiles = [self.farmer, self.other_farmer]
		self.made = []
		frappe.clear_messages()

	def tearDown(self):
		frappe.set_user("Administrator")
		for name in reversed(self.made):
			discard_grievance(name)
		for profile in self.profiles:
			frappe.delete_doc("Grievance Submitter Profile", profile, force=True, ignore_permissions=True)

	def _case(self, **overrides):
		overrides.setdefault("submitter", self.farmer)
		overrides.setdefault("description", FERTILIZER)
		doc = a_grievance(**overrides)
		self.made.append(doc.name)
		return doc

	def _pair(self, **newer):
		"""An earlier ticket and a later one flagged against it."""
		main = self._case()
		dup = self._case(**newer)
		return main, dup

	# Flagging

	def test_a_repeat_by_the_same_farmer_is_flagged_against_the_older_ticket(self):
		main, dup = self._pair()
		rows = duplicates.flag_possible_duplicates(dup)
		self.assertEqual(len(rows), 1)
		self.assertEqual((rows[0].grievance, rows[0].duplicate_of), (dup.name, main.name))
		self.assertEqual(rows[0].detection_method, duplicates.METHOD_SIMILARITY)
		self.assertEqual(rows[0].match_level, "High")
		self.assertAlmostEqual(rows[0].similarity_score, 1.0)
		self.assertEqual(dup.status, C.STATE_SUBMITTED, "flagging does not change the ticket")

	def test_the_older_ticket_is_never_flagged_against_the_newer(self):
		main, _dup = self._pair()
		self.assertEqual(duplicates.flag_possible_duplicates(main), [])

	def test_flagging_twice_records_the_pair_once(self):
		_main, dup = self._pair()
		duplicates.flag_possible_duplicates(dup)
		self.assertEqual(duplicates.flag_possible_duplicates(dup), [])
		self.assertEqual(len(duplicates.pending_reviews(dup.name)), 1)

	def test_another_farmers_ticket_is_not_compared(self):
		self._case(submitter=self.other_farmer)
		dup = self._case()
		self.assertEqual(duplicates.flag_possible_duplicates(dup), [])

	def test_an_anonymous_ticket_has_no_history_to_compare(self):
		self._case()
		anonymous = self._case(submitter=None)
		self.assertEqual(duplicates.flag_possible_duplicates(anonymous), [])

	def test_unrelated_text_is_not_flagged_even_with_the_same_category_and_type(self):
		self._case()
		dup = self._case(description=CANAL)
		self.assertEqual(duplicates.flag_possible_duplicates(dup), [])

	def test_identical_text_under_another_category_and_type_is_flagged(self):
		self._case()
		dup = self._case(service_category=a_service_category(), grievance_type=a_grievance_type())
		self.assertEqual(len(duplicates.flag_possible_duplicates(dup)), 1)

	def test_only_live_or_resolved_tickets_are_candidates(self):
		anchor = self._case(submitter=self.other_farmer)
		for state in (C.STATE_CLOSED, C.STATE_REJECTED):
			self._case(workflow_state=state)
		self._case(workflow_state=C.STATE_DUPLICATE, duplicate_of=anchor.name)
		dup = self._case()
		self.assertEqual(duplicates.flag_possible_duplicates(dup), [])
		for state in (C.STATE_RESOLVED, C.STATE_IN_PROGRESS, C.STATE_MORE_INFO_NEEDED):
			# A fresh farmer per state, so earlier tickets do not fill the flag cap.
			farmer = a_submitter_profile()
			self.profiles.append(farmer)
			kept = self._case(submitter=farmer, workflow_state=state)
			later = self._case(submitter=farmer)
			flagged = {row.duplicate_of for row in duplicates.flag_possible_duplicates(later)}
			self.assertIn(kept.name, flagged, state)

	def test_a_ticket_is_flagged_against_its_best_matches_only(self):
		for _ in range(duplicates.MAX_FLAGS + 2):
			self._case()
		dup = self._case()
		self.assertEqual(len(duplicates.flag_possible_duplicates(dup)), duplicates.MAX_FLAGS)

	# The officer's ruling

	def test_confirming_makes_the_ticket_a_read_only_duplicate_of_the_main(self):
		main, dup = self._pair()
		duplicates.flag_possible_duplicates(dup)

		result = grievance.confirm_duplicate(ticket_number=dup.name, note="Same fertilizer complaint")

		self.assertEqual(result["data"]["status"], C.STATE_DUPLICATE, msg=result)
		self.assertEqual(result["data"]["duplicate_of"], tn.display(main.name))
		dup.reload()
		self.assertEqual(
			(dup.status, dup.workflow_state, dup.duplicate_of), (C.STATE_DUPLICATE,) * 2 + (main.name,)
		)
		row = frappe.get_doc("Grievance Duplicate", {"grievance": dup.name, "duplicate_of": main.name})
		self.assertEqual((row.is_confirmed, row.is_dismissed, row.decided_by), (1, 0, "Administrator"))
		self.assertEqual(row.decision_note, "Same fertilizer complaint")
		self.assertTrue(
			frappe.db.exists(
				"Grievance Status History",
				{"grievance": dup.name, "to_status": C.STATE_DUPLICATE, "action": duplicates.ACTION_CONFIRM},
			)
		)
		entry = frappe.get_all(
			"Grievance Timeline",
			filters={"grievance": dup.name, "entry_type": "status_change"},
			fields=["body", "is_internal"],
		)[0]
		self.assertIn("All further handling will happen on the main ticket", entry.body)
		self.assertFalse(entry.is_internal)

	def test_a_confirmed_duplicate_cannot_be_changed_or_discussed(self):
		main, dup = self._pair()
		duplicates.flag_possible_duplicates(dup)
		grievance.confirm_duplicate(ticket_number=dup.name)
		dup.reload()

		dup.description = "A changed description that is long enough to be valid."
		with self.assertRaises(frappe.ValidationError):
			dup.save(ignore_permissions=True)
		dup.reload()
		with _keep_transaction():
			posted = grievance.message(ticket_number=dup.name, body="Any news on this?")
		self.assertEqual(posted["code"], "VALIDATION_ERROR", msg=posted)
		with self.assertRaises(frappe.ValidationError):
			change_request.raise_change_request(dup, "Defer", {"sla_due_date": "2030-01-01"})
		self.assertFalse(
			frappe.db.exists("Grievance Timeline", {"grievance": dup.name, "entry_type": "message"})
		)

	def test_the_main_defaults_to_the_best_match_and_an_officer_may_name_another(self):
		main = self._case()
		chosen = self._case(description=CANAL)
		dup = self._case()
		duplicates.flag_possible_duplicates(dup)

		result = grievance.confirm_duplicate(ticket_number=dup.name, main_ticket=chosen.name)

		self.assertEqual(result["data"]["duplicate_of"], tn.display(chosen.name), msg=result)
		manual = frappe.get_doc("Grievance Duplicate", {"grievance": dup.name, "duplicate_of": chosen.name})
		self.assertEqual((manual.detection_method, manual.is_confirmed), (duplicates.METHOD_MANUAL, 1))
		self.assertEqual(
			frappe.db.get_value(
				"Grievance Duplicate", {"grievance": dup.name, "duplicate_of": main.name}, "is_confirmed"
			),
			0,
		)

	def test_a_ticket_cannot_duplicate_itself_or_another_duplicate(self):
		main, dup = self._pair()
		third = self._case()
		duplicates.flag_possible_duplicates(dup)
		grievance.confirm_duplicate(ticket_number=dup.name)
		with _keep_transaction():
			itself = grievance.confirm_duplicate(ticket_number=third.name, main_ticket=third.name)
			chained = grievance.confirm_duplicate(ticket_number=third.name, main_ticket=dup.name)
		self.assertEqual(itself["code"], "VALIDATION_ERROR", msg=itself)
		self.assertEqual(chained["code"], "VALIDATION_ERROR", msg=chained)
		self.assertEqual(frappe.db.get_value("Grievance", third.name, "status"), C.STATE_SUBMITTED)

	def test_confirming_needs_a_main_ticket_when_nothing_was_flagged(self):
		lone = self._case()
		with _keep_transaction():
			result = grievance.confirm_duplicate(ticket_number=lone.name)
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)

	def test_dismissing_leaves_the_ticket_a_normal_one(self):
		_main, dup = self._pair()
		duplicates.flag_possible_duplicates(dup)

		result = grievance.dismiss_duplicate(ticket_number=dup.name, note="Different plot")

		self.assertEqual(result["data"]["status"], C.STATE_SUBMITTED, msg=result)
		self.assertIsNone(result["data"]["current_state"]["possible_duplicate"])
		self.assertEqual(duplicates.pending_reviews(dup.name), [])
		row = frappe.get_doc("Grievance Duplicate", {"grievance": dup.name})
		self.assertEqual((row.is_dismissed, row.is_confirmed), (1, 0))
		self.assertTrue(
			frappe.db.exists(
				"Grievance Timeline", {"grievance": dup.name, "entry_type": "note", "is_internal": 1}
			)
		)
		with _keep_transaction():
			again = grievance.dismiss_duplicate(ticket_number=dup.name)
		self.assertEqual(again["code"], "VALIDATION_ERROR", msg=again)

	def test_the_generic_action_endpoint_cannot_confirm_a_duplicate(self):
		_main, dup = self._pair()
		duplicates.flag_possible_duplicates(dup)
		with _keep_transaction():
			result = grievance.action(ticket_number=dup.name, action=duplicates.ACTION_CONFIRM, reason="Same")
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
		offered = grievance._current_state(dup)["available_actions"]
		self.assertNotIn(duplicates.ACTION_CONFIRM, [a["action"] for a in offered])

	# What the officer and the farmer see

	def test_the_flag_tells_the_officer_what_matched(self):
		main, dup = self._pair()
		duplicates.flag_possible_duplicates(dup)

		flag = grievance._current_state(dup)["possible_duplicate"]

		self.assertEqual(flag["ticket_number"], tn.display(main.name))
		self.assertEqual(flag["match_level"], "High")
		self.assertTrue(flag["can_review"])
		self.assertTrue(flag["same_submitter"] and flag["same_category"] and flag["same_type"])
		self.assertEqual(flag["description"], main.description)

	def test_the_list_shows_the_flag_then_the_main_ticket(self):
		main, dup = self._pair()
		duplicates.flag_possible_duplicates(dup)

		def listed():
			items = grievance.list_grievances(search=dup.name, page_size=5)["data"]["items"]
			return next(item for item in items if item["ticket_number"] == tn.display(dup.name))

		self.assertEqual(listed()["possible_duplicate_of"], tn.display(main.name))
		self.assertIsNone(listed()["duplicate_of"])
		grievance.confirm_duplicate(ticket_number=dup.name)
		after = listed()
		self.assertEqual(after["status"], "Duplicate")
		self.assertEqual(after["duplicate_of"], tn.display(main.name))
		self.assertIsNone(after["possible_duplicate_of"])

	def test_the_duplicate_status_is_shown_as_it_is(self):
		self.assertEqual(public_status(C.STATE_DUPLICATE), "Duplicate")

	# Dashboard

	def test_the_status_cards_count_duplicates_and_fold_more_info_needed_into_in_progress(self):
		def counts():
			cards = {card["status"]: card["count"] for card in get_status_summary()}
			return cards

		before = counts()
		self.assertNotIn("More Info Needed", before)
		main = self._case()
		self._case(workflow_state=C.STATE_IN_PROGRESS)
		self._case(workflow_state=C.STATE_MORE_INFO_NEEDED)
		self._case(workflow_state=C.STATE_DUPLICATE, duplicate_of=main.name)
		after = counts()

		self.assertEqual(after["Duplicate"] - before["Duplicate"], 1)
		self.assertEqual(after["In Progress"] - before["In Progress"], 2)
		self.assertEqual(after["Submitted"] - before["Submitted"], 1)
		self.assertEqual(after["All"] - before["All"], 4, "All includes duplicates")
		self.assertEqual(public_status(C.STATE_MORE_INFO_NEEDED), C.STATE_MORE_INFO_NEEDED)

	def test_the_dashboard_counts_the_duplicate_state(self):
		self.assertIn(C.STATE_DUPLICATE, dashboard_rollup.live_states())


class TestAskBeforeFiling(FrappeTestCase):
	"""A farmer on the web portal is asked, once, before a repeat of the last day is filed."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.farmer = a_submitter_profile()
		self.category = a_service_category()
		self.type = a_grievance_type()
		self.area = a_leaf_area()
		frappe.clear_messages()

	def tearDown(self):
		frappe.set_user("Administrator")
		for name in frappe.get_all("Grievance", filters={"submitter": self.farmer}, pluck="name"):
			discard_grievance(name)
		frappe.delete_doc("Grievance Submitter Profile", self.farmer, force=True, ignore_permissions=True)

	def _file(self, **overrides):
		values = {
			"submitter": self.farmer,
			"description": FERTILIZER,
			"service_category": self.category,
			"grievance_type": self.type,
			"administrative_area": self.area,
			"submission_channel": "Web Portal",
		}
		values.update(overrides)
		with _keep_transaction():
			return grievance.submit(**values)

	def _filed(self):
		"""Names filed so far, oldest first."""
		return frappe.get_all(
			"Grievance", filters={"submitter": self.farmer}, order_by="creation asc, name asc", pluck="name"
		)

	def test_a_similar_grievance_of_the_last_day_is_asked_about_not_filed(self):
		self._file()
		result = self._file()

		self.assertEqual(result["code"], "POSSIBLE_DUPLICATE", msg=result)
		self.assertEqual(frappe.response.get("http_status_code"), 409)
		(found,) = result["details"]["possible_duplicates"]
		self.assertEqual(found["match_level"], "High")
		self.assertEqual(found["description"], FERTILIZER)
		self.assertEqual(len(self._filed()), 1, "nothing was filed by the question")

	def test_saying_it_is_new_files_it_and_it_is_still_flagged_for_an_officer(self):
		self._file()
		result = self._file(confirm_new=1)

		self.assertEqual(result["status"], "success", msg=result)
		self.assertEqual(len(self._filed()), 2)
		self.assertEqual(len(duplicates.pending_reviews(self._filed()[-1])), 1)

	def test_a_one_way_channel_is_never_asked_but_is_flagged(self):
		self._file()
		result = self._file(submission_channel="IVR Helpline")

		self.assertEqual(result["status"], "success", msg=result)
		self.assertEqual(len(self._filed()), 2)

	def test_a_ticket_older_than_a_day_is_left_to_the_officers_flag(self):
		self._file()
		older = frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-25)
		frappe.db.set_value("Grievance", self._filed()[0], "creation", older, update_modified=False)

		self.assertEqual(self._file()["status"], "success")
		self.assertEqual(len(self._filed()), 2)

	def test_a_different_issue_is_not_asked_about(self):
		self._file()
		self.assertEqual(self._file(description=CANAL)["status"], "success")

	def test_an_anonymous_filing_has_nobody_to_ask(self):
		self._file()
		self.assertEqual(
			self._file(submitter=None, submitter_name="Anon", contact_mobile="+251911000001")["status"],
			"success",
		)
