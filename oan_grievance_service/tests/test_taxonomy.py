# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1 import draft
from oan_grievance_service.api.v1._options import get_grievance_types, get_service_categories
from oan_grievance_service.api.v1.category_assignment import create_assignment
from oan_grievance_service.api.v1.taxonomy import (
	CATEGORY_EDITABLE,
	TYPE_EDITABLE,
	_editable,
	create_grievance_type,
	create_service_category,
	deactivate_grievance_type,
	deactivate_service_category,
	get_grievance_type,
	get_service_category,
	list_grievance_types,
	list_service_categories,
	update_grievance_type,
	update_service_category,
)
from oan_grievance_service.grievance_masters.doctype.grievance_service_category.grievance_service_category import (
	get_default_category,
	rename_category,
)
from oan_grievance_service.grievance_masters.doctype.grievance_type.grievance_type import get_fallback_type
from oan_grievance_service.services import constants as C
from oan_grievance_service.tests.fixtures import a_grievance
from oan_grievance_service.tests.test_category_assignment import (
	_department,
	_keep_transaction,
	_role_level,
	_user,
)

CATEGORY = "STG406 Inputs"
CODE = "Q51"


class TestTaxonomy(FrappeTestCase):
	def setUp(self):
		# FrappeTestCase rolls back per class, and every test here makes the same names.
		frappe.db.rollback()
		frappe.set_user("Administrator")
		frappe.clear_messages()

	def _category(self, name=CATEGORY, code=CODE, **extra):
		result = create_service_category(category_name=name, code=code, **extra)
		self.assertEqual(result["status"], "success", msg=result)
		return result["data"]["service_category"]

	def _type(self, category=CATEGORY, name="STG406 Late delivery", **extra):
		result = create_grievance_type(service_category=category, type_name=name, **extra)
		self.assertEqual(result["status"], "success", msg=result)
		return result["data"]["grievance_type"]

	# Service categories
	# ------------------

	def test_category_crud(self):
		created = self._category(sort_order=40)
		self.assertEqual(created["category_name"], CATEGORY)
		self.assertEqual(created["code"], CODE)
		self.assertEqual(created["sort_order"], 40)
		self.assertTrue(created["is_active"])
		self.assertFalse(created["code_locked"])
		self.assertEqual(created["grievance_type_count"], 0)

		by_code = get_service_category(CODE)
		self.assertEqual(by_code["data"]["service_category"]["category_name"], CATEGORY)
		self.assertEqual(get_service_category(CATEGORY)["status"], "success")

		updated = update_service_category(CATEGORY, code="Q52", sort_order=41)
		self.assertEqual(updated["status"], "success", msg=updated)
		self.assertEqual(updated["data"]["service_category"]["code"], "Q52")
		self.assertEqual(updated["data"]["service_category"]["sort_order"], 41)

		deactivated = deactivate_service_category(CATEGORY)
		self.assertFalse(deactivated["data"]["service_category"]["is_active"])
		again = deactivate_service_category(CATEGORY)
		self.assertEqual(again["status"], "success")
		self.assertFalse(again["data"]["service_category"]["is_active"])

		reactivated = update_service_category(CATEGORY, is_active=True)
		self.assertTrue(reactivated["data"]["service_category"]["is_active"])

	def test_sort_order_defaults_to_the_end_of_the_list(self):
		last = max(frappe.get_all("Grievance Service Category", pluck="sort_order") or [0])
		self.assertEqual(self._category()["sort_order"], last + 1)

	def test_category_code_is_normalised_and_checked(self):
		self.assertEqual(self._category(code=" q51 ")["code"], "Q51")
		with _keep_transaction():
			for bad in ("Q5", "Q511", "QIL", "QO1", "Q5U"):
				result = create_service_category(category_name=f"STG406 {bad}", code=bad)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=(bad, result))

	def test_duplicate_name_or_code_is_rejected(self):
		self._category()
		with _keep_transaction():
			same_name = create_service_category(category_name=CATEGORY, code="Q53")
			self.assertEqual(same_name["code"], "DUPLICATE_ENTRY", msg=same_name)
			same_code = create_service_category(category_name="STG406 Other name", code=CODE)
			self.assertEqual(same_code["code"], "DUPLICATE_ENTRY", msg=same_code)
			other = self._category(name="STG406 Second", code="Q54")
			clash = update_service_category(other["category_name"], code=CODE)
			self.assertEqual(clash["code"], "DUPLICATE_ENTRY", msg=clash)

	def test_unknown_fields_blank_names_and_empty_updates_are_rejected(self):
		self._category()
		with _keep_transaction():
			for result in (
				create_service_category(category_name=CATEGORY, code="Q55", priority=1),
				create_service_category(category_name=" ", code="Q55"),
				create_service_category(category_name="STG406 Neg", code="Q55", sort_order=-1),
				update_service_category(CATEGORY, category_name=" "),
				update_service_category(CATEGORY, code=None),
				list_service_categories(unknown="x"),
				list_service_categories(page_size=500),
			):
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
			self.assertEqual(update_service_category(CATEGORY)["code"], "VALIDATION_ERROR")

	def test_missing_category_is_not_found(self):
		for result in (
			get_service_category("No such category"),
			update_service_category("No such category", sort_order=1),
			deactivate_service_category("No such category"),
		):
			self.assertEqual(result["code"], "NOT_FOUND", msg=result)

	def test_list_filters_search_and_order(self):
		self._category(name="STG406 Alpha", code="Q56", sort_order=9001)
		self._category(name="STG406 Beta", code="Q57", sort_order=9000, is_active=False)

		everything = list_service_categories(search="STG406")["data"]
		self.assertEqual(
			[row["category_name"] for row in everything["service_categories"]],
			["STG406 Beta", "STG406 Alpha"],
		)
		self.assertEqual(everything["pagination"]["total_count"], 2)

		active = list_service_categories(search="STG406", is_active="1")["data"]
		self.assertEqual([row["category_name"] for row in active["service_categories"]], ["STG406 Alpha"])
		inactive = list_service_categories(search="STG406", is_active=False)["data"]
		self.assertEqual([row["code"] for row in inactive["service_categories"]], ["Q57"])
		self.assertEqual(list_service_categories(search="q56")["data"]["pagination"]["total_count"], 1)

		page = list_service_categories(search="STG406", page=2, page_size=1)["data"]
		self.assertEqual([row["code"] for row in page["service_categories"]], ["Q56"])
		self.assertTrue(page["pagination"]["has_prev"])

	def test_code_is_frozen_once_tickets_exist(self):
		category = self._category()
		kind = self._type()
		before = get_service_category(CATEGORY)["data"]["service_category"]
		self.assertFalse(before["code_locked"])
		a_grievance(service_category=category["category_name"], grievance_type=kind["grievance_type_id"])

		with _keep_transaction():
			result = update_service_category(CATEGORY, code="Q58")
			self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
			self.assertIn("tickets have already been issued", result["message"] + str(result))
		after = get_service_category(CATEGORY)["data"]["service_category"]
		self.assertEqual(after["code"], CODE)
		self.assertTrue(after["code_locked"])
		self.assertTrue(after["has_grievances"])
		# Everything else about the category can still change.
		self.assertEqual(update_service_category(CATEGORY, sort_order=7)["status"], "success")

	def test_rename_carries_every_link(self):
		category = self._category()
		kind = self._type()
		case = a_grievance(
			service_category=category["category_name"], grievance_type=kind["grievance_type_id"]
		)

		renamed = update_service_category(CATEGORY, category_name="STG406 Renamed")
		self.assertEqual(renamed["status"], "success", msg=renamed)
		self.assertEqual(renamed["data"]["service_category"]["category_name"], "STG406 Renamed")
		self.assertEqual(renamed["data"]["service_category"]["code"], CODE)
		self.assertFalse(frappe.db.exists("Grievance Service Category", CATEGORY))
		self.assertEqual(
			frappe.db.get_value("Grievance Type", kind["grievance_type_id"], "service_category"),
			"STG406 Renamed",
		)
		self.assertEqual(frappe.db.get_value("Grievance", case.name, "service_category"), "STG406 Renamed")
		self.assertEqual(
			frappe.db.get_value("Grievance Service Category", "STG406 Renamed", "category_name"),
			"STG406 Renamed",
		)
		with _keep_transaction():
			taken = update_service_category("STG406 Renamed", category_name=C.FALLBACK_SERVICE_CATEGORY)
			self.assertIn(taken["code"], ("VALIDATION_ERROR", "DUPLICATE_ENTRY"), msg=taken)

	def test_rename_is_queued_and_the_record_shows_it_until_it_is_done(self):
		category = self._category()
		kind = self._type()
		case = a_grievance(
			service_category=category["category_name"], grievance_type=kind["grievance_type_id"]
		)
		queued = []
		original = frappe.enqueue
		frappe.enqueue = lambda method, **kwargs: queued.append((method, kwargs))
		try:
			result = update_service_category(CATEGORY, category_name="STG406 Queued", sort_order=5)
			self.assertEqual(result["status"], "success", msg=result)
			record = result["data"]["service_category"]
			# The other fields were saved at once; the name has not moved yet.
			self.assertEqual((record["category_name"], record["sort_order"]), (CATEGORY, 5))
			self.assertEqual(record["renaming_to"], "STG406 Queued")
			self.assertIn("in progress", result["message"])
			self.assertEqual(len(queued), 1)
			self.assertTrue(queued[0][1]["enqueue_after_commit"])
			self.assertEqual(frappe.db.get_value("Grievance", case.name, "service_category"), CATEGORY)

			with _keep_transaction():
				again = update_service_category(CATEGORY, category_name="STG406 Another")
				self.assertEqual(again["code"], "VALIDATION_ERROR", msg=again)
				taken = update_service_category(CATEGORY, category_name=C.FALLBACK_SERVICE_CATEGORY)
				self.assertIn(taken["code"], ("VALIDATION_ERROR", "DUPLICATE_ENTRY"), msg=taken)
		finally:
			frappe.enqueue = original

		# The job does the move and clears the marker.
		rename_category(CATEGORY, "STG406 Queued")
		self.assertEqual(frappe.db.get_value("Grievance", case.name, "service_category"), "STG406 Queued")
		done = get_service_category("STG406 Queued")["data"]["service_category"]
		self.assertIsNone(done["renaming_to"])
		self.assertEqual(done["sort_order"], 5)

	def test_listing_never_counts_grievances(self):
		category = self._category()
		kind = self._type()
		a_grievance(service_category=category["category_name"], grievance_type=kind["grievance_type_id"])
		queries = []
		original = frappe.db.sql

		def record(query, *args, **kwargs):
			queries.append(str(query))
			return original(query, *args, **kwargs)

		frappe.db.sql = record
		try:
			listed = list_service_categories(search="STG406")["data"]["service_categories"]
			typed = list_grievance_types(search="STG406")["data"]["grievance_types"]
		finally:
			frappe.db.sql = original
		self.assertTrue(listed[0]["has_grievances"])
		self.assertTrue(typed[0]["has_grievances"])
		counted = [q for q in queries if "tabGrievance`" in q and "count(" in q.lower()]
		self.assertEqual(counted, [])

	def test_deactivating_a_category_deactivates_its_types(self):
		self._category()
		first = self._type()
		second = self._type(name="STG406 Wrong amount")
		self._category(name="STG406 Elsewhere", code="Q59")
		elsewhere = self._type(category="STG406 Elsewhere", name="STG406 Late delivery")

		self.assertEqual(
			get_service_category(CATEGORY)["data"]["service_category"]["grievance_type_count"], 2
		)
		deactivate_service_category(CATEGORY)

		for kind in (first, second):
			self.assertEqual(frappe.db.get_value("Grievance Type", kind["grievance_type_id"], "is_active"), 0)
		self.assertEqual(
			frappe.db.get_value("Grievance Type", elsewhere["grievance_type_id"], "is_active"), 1
		)
		self.assertEqual(
			get_service_category(CATEGORY)["data"]["service_category"]["grievance_type_count"], 0
		)

		# Reactivating the category does not silently bring the types back.
		update_service_category(CATEGORY, is_active=True)
		self.assertEqual(frappe.db.get_value("Grievance Type", first["grievance_type_id"], "is_active"), 0)

	# The default category
	# --------------------

	def _default(self):
		return frappe.db.get_value("Grievance Service Category", {"is_default": 1}, "name")

	def test_exactly_one_category_is_the_default(self):
		_ensure_default()
		self.assertEqual(frappe.db.count("Grievance Service Category", {"is_default": 1}), 1)
		first = self._default()
		made = self._category(is_default=True)
		self.assertTrue(made["is_default"])
		self.assertEqual(self._default(), CATEGORY)
		self.assertEqual(frappe.db.count("Grievance Service Category", {"is_default": 1}), 1)
		self.assertFalse(get_service_category(first)["data"]["service_category"]["is_default"])

	def test_promoting_a_category_clears_the_previous_default(self):
		_ensure_default()
		previous = self._default()
		self._category()
		self.assertNotEqual(self._default(), CATEGORY)

		promoted = update_service_category(CATEGORY, is_default=True)
		self.assertEqual(promoted["status"], "success", msg=promoted)
		self.assertTrue(promoted["data"]["service_category"]["is_default"])
		self.assertEqual(self._default(), CATEGORY)
		listed = list_service_categories(page_size=100)["data"]["service_categories"]
		self.assertEqual([row["category_name"] for row in listed if row["is_default"]], [CATEGORY])
		self.assertEqual(frappe.db.get_value("Grievance Service Category", previous, "is_active"), 1)

		# The previous default is an ordinary category again.
		self.assertEqual(deactivate_service_category(previous)["status"], "success")

	def test_the_default_cannot_be_deactivated_or_unset(self):
		_ensure_default()
		current = self._default()
		with _keep_transaction():
			for result in (
				deactivate_service_category(current),
				update_service_category(current, is_active=False),
				update_service_category(current, is_default=False),
				create_service_category(
					category_name="STG406 Inactive default", code="Q5C", is_active=False, is_default=True
				),
			):
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
			self.assertEqual(self._default(), current)
			self._category()
			inactive = update_service_category(CATEGORY, is_active=False)
			self.assertEqual(inactive["status"], "success", msg=inactive)
			refused = update_service_category(CATEGORY, is_default=True)
			self.assertEqual(refused["code"], "VALIDATION_ERROR", msg=refused)
		self.assertEqual(self._default(), current)

	def test_the_new_default_gets_a_catch_all_type(self):
		_ensure_default()
		self._category()
		self.assertIsNone(frappe.db.exists("Grievance Type", {"service_category": CATEGORY}))
		update_service_category(CATEGORY, is_default=True)
		catch_all = frappe.db.get_value(
			"Grievance Type",
			{"service_category": CATEGORY, "type_name": C.FALLBACK_GRIEVANCE_TYPE},
			["name", "is_active"],
			as_dict=True,
		)
		self.assertEqual(catch_all.is_active, 1)
		self.assertEqual(get_fallback_type(CATEGORY), catch_all.name)

		# A catch-all that had been retired is brought back when its category is promoted again.
		_ensure_default()
		frappe.db.set_value("Grievance Type", catch_all.name, "is_active", 0)
		update_service_category(CATEGORY, is_default=True)
		self.assertEqual(frappe.db.get_value("Grievance Type", catch_all.name, "is_active"), 1)

	def test_the_default_categorys_catch_all_type_cannot_be_deactivated(self):
		_ensure_default()
		self._category()
		update_service_category(CATEGORY, is_default=True)
		kind = get_fallback_type(CATEGORY)
		with _keep_transaction():
			for result in (deactivate_grievance_type(kind), update_grievance_type(kind, is_active=False)):
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
		# Once another category is the default it is an ordinary type.
		_ensure_default()
		self.assertEqual(deactivate_grievance_type(kind)["status"], "success")

	def test_a_draft_without_a_category_falls_to_the_default(self):
		_ensure_default()
		self._category()
		update_service_category(CATEGORY, is_default=True)
		catch_all = get_fallback_type(CATEGORY)
		saved = draft.save(
			client_submission_uuid=frappe.generate_hash(length=20),
			submitter_name="Test Submitter",
			description="A description long enough to clear the twenty character minimum.",
		)
		self.assertEqual(saved["status"], "success", msg=saved)
		self.assertEqual(saved["data"]["service_category"], CATEGORY)
		self.assertEqual(saved["data"]["grievance_type"], catch_all)

	def test_renaming_the_default_keeps_it_the_default(self):
		_ensure_default()
		self._category(is_default=True)
		renamed = update_service_category(CATEGORY, category_name="STG406 Renamed default")
		self.assertEqual(renamed["status"], "success", msg=renamed)
		self.assertTrue(renamed["data"]["service_category"]["is_default"])
		self.assertEqual(get_default_category(), "STG406 Renamed default")

	def test_patch_only_writes_the_editable_fields(self):
		sent = {"category_name": "X", "is_default": True, "owner": "someone", "creation": "2000-01-01"}
		self.assertEqual(_editable(sent, CATEGORY_EDITABLE), {"category_name": "X", "is_default": 1})
		self.assertEqual(
			_editable({"type_name": "Y", "service_category": "Z"}, TYPE_EDITABLE), {"type_name": "Y"}
		)

	# Grievance types
	# ---------------

	def test_type_crud(self):
		self._category()
		created = self._type()
		self.assertEqual(created["type_name"], "STG406 Late delivery")
		self.assertEqual(created["service_category"], CATEGORY)
		self.assertTrue(created["is_active"])
		self.assertNotIn("code", created)
		type_id = created["grievance_type_id"]

		fetched = get_grievance_type(type_id)["data"]["grievance_type"]
		self.assertEqual(fetched["type_name"], "STG406 Late delivery")

		updated = update_grievance_type(type_id, type_name="STG406 Delayed delivery")
		self.assertEqual(updated["status"], "success", msg=updated)
		self.assertEqual(updated["data"]["grievance_type"]["type_name"], "STG406 Delayed delivery")
		self.assertEqual(updated["data"]["grievance_type"]["grievance_type_id"], type_id)

		deactivated = deactivate_grievance_type(type_id)
		self.assertFalse(deactivated["data"]["grievance_type"]["is_active"])
		self.assertEqual(deactivate_grievance_type(type_id)["status"], "success")
		self.assertTrue(update_grievance_type(type_id, is_active=True)["data"]["grievance_type"]["is_active"])

	def test_type_name_is_unique_within_a_category_only(self):
		self._category()
		self._category(name="STG406 Second", code="Q54")
		first = self._type()
		with _keep_transaction():
			same_name = create_grievance_type(service_category=CATEGORY, type_name=first["type_name"])
			self.assertEqual(same_name["code"], "DUPLICATE_ENTRY", msg=same_name)
			other = self._type(name="STG406 Other")
			clash = update_grievance_type(other["grievance_type_id"], type_name=first["type_name"])
			self.assertEqual(clash["code"], "DUPLICATE_ENTRY", msg=clash)
		# The same name under another category is a different type.
		elsewhere = self._type(category="STG406 Second", name=first["type_name"])
		self.assertNotEqual(elsewhere["grievance_type_id"], first["grievance_type_id"])

	def test_type_request_validation_and_unknown_category(self):
		self._category()
		with _keep_transaction():
			for result in (
				create_grievance_type(service_category=CATEGORY, type_name=" "),
				create_grievance_type(service_category=CATEGORY, type_name="STG406 Bad", code="BAD"),
				create_grievance_type(service_category=CATEGORY),
				create_grievance_type(service_category="No such", type_name="STG406 Bad"),
			):
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
			kind = self._type()
			for result in (
				update_grievance_type(kind["grievance_type_id"], code="LATE"),
				update_grievance_type(kind["grievance_type_id"]),
			):
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)

	def test_category_can_be_named_by_code_when_creating_a_type(self):
		self._category()
		self.assertEqual(self._type(category=CODE)["service_category"], CATEGORY)

	def test_type_cannot_change_category_or_be_created_under_an_inactive_one(self):
		self._category()
		self._category(name="STG406 Second", code="Q54")
		kind = self._type()
		with _keep_transaction():
			moved = update_grievance_type(kind["grievance_type_id"], service_category="STG406 Second")
			self.assertEqual(moved["code"], "VALIDATION_ERROR", msg=moved)
			self.assertIn("service_category", moved["details"])

			deactivate_service_category("STG406 Second")
			created = create_grievance_type(service_category="STG406 Second", type_name="STG406 New")
			self.assertEqual(created["code"], "VALIDATION_ERROR", msg=created)
			self.assertIn("inactive", created["message"] + str(created))

			deactivate_service_category(CATEGORY)
			reactivated = update_grievance_type(kind["grievance_type_id"], is_active=True)
			self.assertEqual(reactivated["code"], "VALIDATION_ERROR", msg=reactivated)
		self.assertEqual(frappe.db.get_value("Grievance Type", kind["grievance_type_id"], "is_active"), 0)

	def test_type_in_use_cannot_move_category_outside_the_api(self):
		category = self._category()
		other = self._category(name="STG406 Second", code="Q54")
		kind = self._type()
		a_grievance(service_category=category["category_name"], grievance_type=kind["grievance_type_id"])
		doc = frappe.get_doc("Grievance Type", kind["grievance_type_id"])
		doc.service_category = other["category_name"]
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_type_list_filters_and_order(self):
		self._category()
		self._category(name="STG406 Second", code="Q54")
		self._type(name="STG406 Alpha")
		beta = self._type(name="STG406 Beta", is_active=False)
		self._type(category="STG406 Second", name="STG406 Alpha")
		self._type(category="STG406 Second", name="STG406 Gamma")

		in_first = list_grievance_types(service_category=CATEGORY)["data"]
		self.assertEqual(
			[row["type_name"] for row in in_first["grievance_types"]], ["STG406 Alpha", "STG406 Beta"]
		)
		by_code = list_grievance_types(service_category=CODE, is_active=True)["data"]
		self.assertEqual([row["type_name"] for row in by_code["grievance_types"]], ["STG406 Alpha"])
		found = list_grievance_types(search="gam")["data"]
		self.assertEqual([row["type_name"] for row in found["grievance_types"]], ["STG406 Gamma"])
		self.assertEqual(found["grievance_types"][0]["service_category"], "STG406 Second")
		inactive = list_grievance_types(service_category=CATEGORY, is_active="0")["data"]
		self.assertEqual(inactive["grievance_types"][0]["grievance_type_id"], beta["grievance_type_id"])

		# The same name in two categories is two rows, and paging walks them without a gap or repeat.
		seen = []
		for number in (1, 2, 3, 4):
			page = list_grievance_types(search="STG406", page=number, page_size=1)["data"]
			seen += [row["grievance_type_id"] for row in page["grievance_types"]]
		everything = list_grievance_types(search="STG406", page_size=100)["data"]["grievance_types"]
		self.assertEqual(seen, [row["grievance_type_id"] for row in everything])
		self.assertEqual(len(set(seen)), 4)
		with _keep_transaction():
			self.assertEqual(list_grievance_types(service_category="No such")["code"], "VALIDATION_ERROR")
			self.assertEqual(get_grievance_type("GTYPE-NOPE")["code"], "NOT_FOUND")
			self.assertEqual(update_grievance_type("GTYPE-NOPE", type_name="X")["code"], "NOT_FOUND")
			self.assertEqual(deactivate_grievance_type("GTYPE-NOPE")["code"], "NOT_FOUND")

	# Consistency with the readers of the reference data
	# --------------------------------------------------

	def test_dropdowns_offer_only_active_categories_and_types(self):
		self._category()
		kind = self._type()
		self._type(name="STG406 Retired", is_active=False)

		def offered():
			categories = {row["category_name"]: row for row in get_service_categories()}
			types = {row["type_name"]: row for row in get_grievance_types(service_category=CATEGORY)}
			return categories, types

		categories, types = offered()
		self.assertEqual(categories[CATEGORY]["code"], CODE)
		self.assertEqual(list(types), ["STG406 Late delivery"])
		self.assertEqual(types["STG406 Late delivery"]["grievance_type_id"], kind["grievance_type_id"])

		deactivate_grievance_type(kind["grievance_type_id"])
		self.assertEqual(offered()[1], {})

		deactivate_service_category(CATEGORY)
		self.assertNotIn(CATEGORY, offered()[0])

	def test_renamed_and_reordered_categories_reach_the_dropdown(self):
		self._category(sort_order=9100)
		self._category(name="STG406 Second", code="Q54", sort_order=9099)
		names = [row["category_name"] for row in get_service_categories() if "STG406" in row["category_name"]]
		self.assertEqual(names, ["STG406 Second", CATEGORY])
		update_service_category(CATEGORY, category_name="STG406 Renamed", sort_order=9098)
		names = [row["category_name"] for row in get_service_categories() if "STG406" in row["category_name"]]
		self.assertEqual(names, ["STG406 Renamed", "STG406 Second"])

	def test_inactive_category_or_type_cannot_take_a_new_grievance(self):
		category = self._category()["category_name"]
		kind = self._type()["grievance_type_id"]
		other = self._type(name="STG406 Wrong amount")["grievance_type_id"]
		case = a_grievance(service_category=category, grievance_type=kind).name

		deactivate_grievance_type(kind)
		with self.assertRaises(frappe.ValidationError):
			a_grievance(service_category=category, grievance_type=kind)
		a_grievance(service_category=category, grievance_type=other)

		# A case already filed keeps its classification and is not blocked by it...
		filed = frappe.get_doc("Grievance", case)
		filed.load_doc_before_save()
		filed.validate_classification_is_active()
		# ...but it cannot be reclassified into a retired type.
		reclassified = frappe.get_doc("Grievance", case)
		reclassified.load_doc_before_save()
		reclassified.grievance_type = other
		reclassified.validate_classification_is_active()
		deactivate_service_category(CATEGORY)
		with self.assertRaises(frappe.ValidationError):
			reclassified.validate_classification_is_active()
		with self.assertRaises(frappe.ValidationError):
			a_grievance(service_category=category, grievance_type=other)

	def test_routing_and_templates_keep_working_for_a_retired_category(self):
		category = self._category()
		l1 = _user("stg406-l1@example.com", "Hana Bekele")
		_role_level("nodal_officer", 10)
		_role_level("senior_nodal_officer", 20)
		department = _department("STG406 Agency", "S406")
		desk = create_assignment(
			service_category=category["category_name"], department=department, l1_officer=l1, sla_days=5
		)
		self.assertEqual(desk["status"], "success", msg=desk)
		template = frappe.get_doc(
			{
				"doctype": "Grievance Response Template",
				"title": "STG406 Resolution",
				"workflow_action": "Resolve",
				"body": "Resolved.",
				"service_category": category["category_name"],
				"is_active": 1,
			}
		).insert()

		counts = get_service_category(CATEGORY)["data"]["service_category"]
		self.assertEqual(counts["assignment_count"], 1)
		self.assertEqual(counts["response_template_count"], 1)

		deactivate_service_category(CATEGORY)

		# What is already configured for in-flight cases stays as it was...
		desk_name = desk["data"]["assignment"]["name"]
		self.assertEqual(frappe.db.get_value("Grievance RBAC Assignment", desk_name, "active"), 1)
		self.assertEqual(frappe.db.get_value("Grievance Response Template", template.name, "is_active"), 1)
		# ...and nothing new can be set up on the retired category.
		with _keep_transaction():
			again = create_assignment(
				service_category=category["category_name"],
				department=department,
				l1_officer=l1,
				sla_days=5,
			)
			self.assertEqual(again["status"], "error", msg=again)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Grievance Response Template",
					"title": "STG406 Another",
					"workflow_action": "Resolve",
					"body": "Resolved.",
					"service_category": category["category_name"],
					"is_active": 1,
				}
			).insert()

	# Roles
	# -----

	def test_roles(self):
		self._category()
		kind = self._type()
		admin = _user("stg406-admin@example.com", "Selam Admin", role="Grievance Admin")
		reviewer = _user("stg406-review@example.com", "Dawit Review", role="Grievance Review Officer")
		officer = _user("stg406-officer@example.com", "Almaz Officer")

		frappe.set_user(admin)
		self.assertEqual(self._category(name="STG406 Admin made", code="Q5A")["code"], "Q5A")
		self.assertEqual(
			update_grievance_type(kind["grievance_type_id"], type_name="Admin edit")["status"], "success"
		)

		for user, can_read in ((reviewer, True), (officer, False), ("Guest", False)):
			frappe.set_user(user)
			with _keep_transaction():
				reads = (
					list_service_categories(),
					get_service_category(CATEGORY),
					list_grievance_types(),
					get_grievance_type(kind["grievance_type_id"]),
				)
				for result in reads:
					self.assertEqual(result["status"] == "success", can_read, msg=(user, result))
					if not can_read:
						self.assertEqual(result["code"], "PERMISSION_DENIED", msg=(user, result))
				for result in (
					create_service_category(category_name="STG406 Refused", code="Q5B"),
					update_service_category(CATEGORY, sort_order=3),
					deactivate_service_category(CATEGORY),
					create_grievance_type(service_category=CATEGORY, type_name="STG406 Refused"),
					update_grievance_type(kind["grievance_type_id"], type_name="Refused"),
					deactivate_grievance_type(kind["grievance_type_id"]),
					# A bad body must not turn a refusal into a 400.
					create_service_category(priority=1),
				):
					self.assertEqual(result["code"], "PERMISSION_DENIED", msg=(user, result))
		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.exists("Grievance Service Category", "STG406 Refused"))


def _ensure_default():
	"""Make the catch-all the default, as the installer and the patch do on a real site."""
	name = _ensure_category(C.FALLBACK_SERVICE_CATEGORY, "Q5Z")
	if frappe.db.get_value("Grievance Service Category", {"is_default": 1}) != name:
		frappe.db.set_value("Grievance Service Category", {"is_default": 1}, "is_default", 0)
		frappe.db.set_value("Grievance Service Category", name, "is_default", 1)
	_ensure_type(name, C.FALLBACK_GRIEVANCE_TYPE)
	return name


def _ensure_category(name, code):
	if frappe.db.exists("Grievance Service Category", name):
		return name
	return (
		frappe.get_doc({"doctype": "Grievance Service Category", "category_name": name, "code": code})
		.insert(ignore_permissions=True)
		.name
	)


def _ensure_type(category, type_name):
	existing = frappe.db.get_value("Grievance Type", {"service_category": category, "type_name": type_name})
	if existing:
		return existing
	return (
		frappe.get_doc({"doctype": "Grievance Type", "service_category": category, "type_name": type_name})
		.insert(ignore_permissions=True)
		.name
	)
