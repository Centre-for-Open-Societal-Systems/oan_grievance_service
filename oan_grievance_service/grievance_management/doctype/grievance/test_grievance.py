# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from pydantic import ValidationError as PydanticValidationError

from oan_grievance_service.api.v1.grievance import (
	CLIENT_IMMUTABLE_FIELDS,
	_resolve_submitter_identity,
	submit,
)
from oan_grievance_service.permissions import grievance_query_conditions, has_grievance_permission
from oan_grievance_service.services import routing, ticket_number
from oan_grievance_service.services.identity import validate_submission_payload
from oan_grievance_service.tests.fixtures import discard_grievance


class TestGrievance(FrappeTestCase):
	def setUp(self):
		# Setup tree: Country -> Region -> Woreda (leaf)
		root_name = frappe.db.get_value(
			"Grievance Administrative Area", {"area_name": "Tree Root Country"}, "name"
		)
		if not root_name:
			self.root_area = frappe.get_doc(
				{
					"doctype": "Grievance Administrative Area",
					"area_name": "Tree Root Country",
					"level_name": "Country",
					"code": "TRC",
					"is_group": 1,
				}
			).insert(ignore_permissions=True)
		else:
			self.root_area = frappe.get_doc("Grievance Administrative Area", root_name)

		region_name = frappe.db.get_value(
			"Grievance Administrative Area", {"area_name": "Tree Test Region"}, "name"
		)
		if not region_name:
			self.region_area = frappe.get_doc(
				{
					"doctype": "Grievance Administrative Area",
					"area_name": "Tree Test Region",
					"level_name": "Region",
					"code": "TTR",
					"ticket_code": "T",
					"parent_administrative_area": self.root_area.name,
					"is_group": 1,
				}
			).insert(ignore_permissions=True)
		else:
			self.region_area = frappe.get_doc("Grievance Administrative Area", region_name)
			# Existing region rows from older fixtures may lack ticket_code.
			if not self.region_area.ticket_code:
				self.region_area.db_set("ticket_code", "T", update_modified=False)
				self.region_area.reload()

		woreda_name = frappe.db.get_value(
			"Grievance Administrative Area", {"area_name": "Tree Test Woreda Leaf"}, "name"
		)
		if not woreda_name:
			self.woreda_leaf = frappe.get_doc(
				{
					"doctype": "Grievance Administrative Area",
					"area_name": "Tree Test Woreda Leaf",
					"level_name": "Woreda",
					"code": "TTW",
					"parent_administrative_area": self.region_area.name,
					"is_group": 0,
				}
			).insert(ignore_permissions=True)
		else:
			self.woreda_leaf = frappe.get_doc("Grievance Administrative Area", woreda_name)

		self.root_area.reload()
		self.region_area.reload()
		self.woreda_leaf.reload()

		# Ensure masters
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

		existing_gtype = frappe.db.get_value("Grievance Type", {"type_name": "Fertilizer Shortage"}, "name")
		if not existing_gtype:
			self.gtype_doc = frappe.get_doc(
				{
					"doctype": "Grievance Type",
					"type_name": "Fertilizer Shortage",
					"service_category": "Inputs",
					"is_active": 1,
				}
			).insert(ignore_permissions=True)
		else:
			self.gtype_doc = frappe.get_doc("Grievance Type", existing_gtype)

		if not frappe.db.exists("Grievance Department", "Agriculture Dept"):
			frappe.get_doc(
				{
					"doctype": "Grievance Department",
					"dept_name": "Agriculture Dept",
					"email_account": "agri@example.com",
					"active": 1,
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("Grievance Department", "Regional Agronomy Dept"):
			frappe.get_doc(
				{
					"doctype": "Grievance Department",
					"dept_name": "Regional Agronomy Dept",
					"email_account": "regional@example.com",
					"active": 1,
				}
			).insert(ignore_permissions=True)

	def test_grievance_creation_sets_area_lft_and_path_code(self):
		g = frappe.get_doc(
			{
				"doctype": "Grievance",
				"submitter_type": "Individual Farmer",
				"submitter_name": "Tesfaye",
				"contact_mobile": "+251911334455",
				"submission_channel": "Mobile App",
				"administrative_area": self.woreda_leaf.name,
				"service_category": "Inputs",
				"grievance_type": self.gtype_doc.name,
				"description": "Fertilizer subsidy has not been delivered for 3 weeks.",
			}
		).insert(ignore_permissions=True)

		self.assertEqual(g.area_lft, self.woreda_leaf.lft)
		self.assertTrue(bool(g.area_path_code))

	def _submit(self, **overrides):
		payload = {
			"doctype": "Grievance",
			"submitter_type": "Individual Farmer",
			"submitter_name": "Tesfaye",
			"contact_mobile": "+251911334455",
			"submission_channel": "Mobile App",
			"administrative_area": self.woreda_leaf.name,
			"service_category": "Inputs",
			"grievance_type": self.gtype_doc.name,
			"description": "Fertilizer subsidy has not been delivered for 3 weeks.",
		}
		payload.update(overrides)
		return frappe.get_doc(payload).insert(ignore_permissions=True)

	def test_ticket_number_has_the_agreed_shape(self):
		"""Nine characters: region 1, category 3, sequence 4, year 1."""
		g = self._submit()
		ticket = g.ticket_number

		self.assertEqual(len(ticket), ticket_number.TICKET_WIDTH)
		self.assertEqual(ticket[0], "T", "region character")
		self.assertEqual(ticket[1:4], "001", "category code")
		self.assertEqual(ticket[8], ticket_number.year_segment(), "year character")

		# Every character must come from the Base32 alphabet, so none of the
		# excluded I, L, O or U can reach a submitter.
		for char in ticket:
			self.assertIn(char, ticket_number.ALPHABET, f"{char} is outside the alphabet")

		# The name is the ticket number; reports and notifications read both.
		self.assertEqual(g.name, ticket)

	def test_ticket_number_takes_the_region_not_the_leaf(self):
		"""The grievance attaches to a woreda; the ticket still names its region."""
		self.assertIsNone(self.woreda_leaf.ticket_code, "leaf carries no ticket code")
		g = self._submit()
		self.assertEqual(g.ticket_number[0], self.region_area.ticket_code)

	def test_ticket_numbers_are_unique_within_one_scope(self):
		"""Same region, category and year: the sequence must still separate them."""
		first = self._submit()
		second = self._submit()

		self.assertNotEqual(first.ticket_number, second.ticket_number)
		# Region, category and year are shared; only the sequence differs.
		self.assertEqual(first.ticket_number[:4], second.ticket_number[:4])
		self.assertEqual(first.ticket_number[8], second.ticket_number[8])
		self.assertEqual(
			ticket_number.decode(second.ticket_number[4:8]),
			ticket_number.decode(first.ticket_number[4:8]) + 1,
			"the sequence should advance by one",
		)

	def test_missing_region_ticket_code_is_refused(self):
		"""A guessed region would be wrong for the life of the case, so fail loudly."""
		region = frappe.get_doc(
			{
				"doctype": "Grievance Administrative Area",
				"area_name": "Uncoded Region",
				"level_name": "Region",
				"parent_administrative_area": self.root_area.name,
				"is_group": 1,
			}
		).insert(ignore_permissions=True)
		woreda = frappe.get_doc(
			{
				"doctype": "Grievance Administrative Area",
				"area_name": "Uncoded Woreda",
				"level_name": "Woreda",
				"parent_administrative_area": region.name,
				"is_group": 0,
			}
		).insert(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError):
			self._submit(administrative_area=woreda.name)

	def test_base32_round_trips_and_pads(self):
		self.assertEqual(ticket_number.encode(0, 4), "0000")
		self.assertEqual(ticket_number.encode(1, 4), "0001")
		self.assertEqual(ticket_number.encode(31, 4), "000Z")
		self.assertEqual(ticket_number.encode(32, 4), "0010")
		self.assertEqual(ticket_number.encode(42, 4), "001A")

		for value in (0, 1, 31, 32, 42, 1000, 1048575):
			self.assertEqual(ticket_number.decode(ticket_number.encode(value, 4)), value)

	def test_base32_excludes_and_forgives_confusable_characters(self):
		for char in "ILOU":
			self.assertNotIn(char, ticket_number.ALPHABET)

		# A submitter reading "0" as "O" and "1" as "I" or "L" still resolves,
		# and the display hyphens and lower case are accepted.
		self.assertEqual(ticket_number.decode("O1"), ticket_number.decode("01"))
		self.assertEqual(ticket_number.decode("I0"), ticket_number.decode("10"))
		self.assertEqual(ticket_number.decode("L0"), ticket_number.decode("10"))
		self.assertEqual(ticket_number.decode("3-001-002a-0"), ticket_number.decode("3001002A0"))

	def test_sequence_exhaustion_is_refused_not_widened(self):
		"""Overflowing must fail rather than silently emit a tenth character."""
		with self.assertRaises(frappe.ValidationError):
			ticket_number.encode(ticket_number.BASE**ticket_number.SEQUENCE_WIDTH, 4)

	def test_ethiopian_year_turns_in_september(self):
		"""The manual form numbers its references by the Ethiopian year."""
		import datetime

		# The annexure's sample: filed 15 November 2025, reference LK/GR/2018/0147.
		self.assertEqual(ticket_number.ethiopian_year(datetime.date(2025, 11, 15)), 2018)
		# Still 2018 before the new year, 2019 on and after 11 September 2026.
		self.assertEqual(ticket_number.ethiopian_year(datetime.date(2026, 1, 1)), 2018)
		self.assertEqual(ticket_number.ethiopian_year(datetime.date(2026, 9, 10)), 2018)
		self.assertEqual(ticket_number.ethiopian_year(datetime.date(2026, 9, 11)), 2019)
		# 2028 is a Gregorian leap year, so the 2027 new year falls a day later.
		self.assertEqual(ticket_number.ethiopian_year(datetime.date(2027, 9, 11)), 2019)
		self.assertEqual(ticket_number.ethiopian_year(datetime.date(2027, 9, 12)), 2020)

	def test_segments_are_read_only(self):
		"""Describing a ticket must not consume a sequence number."""
		before = ticket_number.segments(self.woreda_leaf.name, "Inputs")
		self.assertEqual((before.region, before.category, before.year), ("T", "001", before.year))

		g = self._submit()
		after = ticket_number.segments(self.woreda_leaf.name, "Inputs")
		self.assertEqual(before, after)
		self.assertTrue(g.ticket_number.startswith(before.region + before.category))

	def test_display_grouping_is_presentation_only(self):
		self.assertEqual(ticket_number.display("3001002A0"), "3-001-002A-0")
		g = self._submit()
		self.assertEqual(ticket_number.display(g.ticket_number).replace("-", ""), g.ticket_number)

	def test_normalize_accepts_a_ticket_however_it_was_typed(self):
		"""What we print grouped must come back in whatever form a person sends."""
		stored = "3001002A0"
		for typed in (
			"3001002A0",
			"3-001-002A-0",  # as displayed
			"3 001 002A 0",  # read out with pauses
			"3001002a0",  # lower case
			"  3-001-002a-0  ",  # pasted with whitespace
			"3OO1OO2AO",  # O read for 0
			"300I002A0",  # I read for 1
			"300L002A0",  # L read for 1
		):
			self.assertEqual(ticket_number.normalize(typed), stored, f"failed on {typed!r}")

	def test_normalize_leaves_non_tickets_alone(self):
		"""A search for something else must not be mangled into a false match."""
		for other in ("", "not-a-ticket", "3001002A", "3001002A00"):
			self.assertEqual(ticket_number.normalize(other), other.strip())

	def test_a_ticket_can_be_looked_up_as_it_was_displayed(self):
		"""The grouped form we send is the form a submitter will quote back."""
		from oan_grievance_service.api.v1.grievance import _load

		g = self._submit()
		grouped = ticket_number.display(g.ticket_number)
		self.assertNotEqual(grouped, g.ticket_number, "the display form should differ")

		self.assertEqual(_load(grouped).name, g.name)
		self.assertEqual(_load(grouped.lower()).name, g.name)
		self.assertEqual(_load(g.ticket_number).name, g.name)

	def test_cannot_attach_grievance_to_group_area(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Grievance",
					"submitter_type": "Individual Farmer",
					"submitter_name": "Tesfaye",
					"contact_mobile": "+251911334455",
					"submission_channel": "Mobile App",
					"administrative_area": self.region_area.name,  # is_group: 1
					"service_category": "Inputs",
					"grievance_type": self.gtype_doc.name,
					"description": "Fertilizer subsidy has not been delivered for 3 weeks.",
				}
			).insert(ignore_permissions=True)

	def test_submission_payload_validates_domain_rules(self):
		"""Domain-only checks: ET mobile, description length, type↔category, filing area."""
		base = {
			"submitter_type": "Individual Farmer",
			"submitter_name": "Tesfaye",
			"contact_mobile": "+251911334455",
			"submission_channel": "Mobile App",
			"administrative_area": self.woreda_leaf.name,
			"service_category": "Inputs",
			"grievance_type": self.gtype_doc.name,
			"description": "Fertilizer subsidy has not been delivered for 3 weeks.",
		}

		validate_submission_payload(base)

		with self.assertRaises(PydanticValidationError):
			validate_submission_payload({**base, "contact_mobile": "+255911334455"})

		with self.assertRaises(PydanticValidationError):
			validate_submission_payload({**base, "description": "too short"})

		# Cross-field rule beyond Link: type must belong to the chosen category.
		other_cat = "Credit"
		if not frappe.db.exists("Grievance Service Category", other_cat):
			frappe.get_doc(
				{
					"doctype": "Grievance Service Category",
					"category_name": other_cat,
					"code": "004",
					"is_active": 1,
				}
			).insert(ignore_permissions=True)
		with self.assertRaises(PydanticValidationError):
			validate_submission_payload({**base, "service_category": other_cat})

		with self.assertRaises(PydanticValidationError):
			validate_submission_payload({**base, "administrative_area": self.region_area.name})

	def test_can_attach_grievance_to_woreda_group_area(self):
		"""Woredas with child kebeles (is_group=1) must still be allowed for grievance filing."""
		# Create a child kebele under woreda to ensure it is marked as a group
		kebele_name = frappe.db.get_value(
			"Grievance Administrative Area", {"area_name": "Test Child Kebele"}, "name"
		)
		if not kebele_name:
			frappe.get_doc(
				{
					"doctype": "Grievance Administrative Area",
					"area_name": "Test Child Kebele",
					"level_name": "Kebele",
					"code": "TCK",
					"parent_administrative_area": self.woreda_leaf.name,
					"is_group": 0,
				}
			).insert(ignore_permissions=True)

		self.woreda_leaf.reload()
		g = frappe.get_doc(
			{
				"doctype": "Grievance",
				"submitter_type": "Individual Farmer",
				"submitter_name": "Tesfaye",
				"contact_mobile": "+251911334455",
				"submission_channel": "Mobile App",
				"administrative_area": self.woreda_leaf.name,
				"service_category": "Inputs",
				"grievance_type": self.gtype_doc.name,
				"description": "Fertilizer subsidy has not been delivered for 3 weeks.",
			}
		).insert(ignore_permissions=True)

		self.assertEqual(g.administrative_area, self.woreda_leaf.name)
		self.assertEqual(g.area_lft, self.woreda_leaf.lft)
		self.assertTrue(bool(g.area_path_code))
		discard_grievance(g.name)

	def test_can_file_grievance_at_woreda_level_via_api(self):
		"""Submit API accepts woreda parameter and optional kebele free text."""
		res = submit(
			submitter_type="Individual Farmer",
			submitter_name="Tesfaye",
			contact_mobile="+251911334455",
			submission_channel="Mobile App",
			woreda=self.woreda_leaf.name,
			kebele="Village 2 West",
			service_category="Inputs",
			grievance_type=self.gtype_doc.name,
			description="Fertilizer subsidy has not been delivered for 3 weeks.",
			consent_given=1,
		)
		self.assertEqual(res["status"], "success")
		ticket = res["data"]["ticket_number"]
		g = frappe.get_doc("Grievance", ticket)
		self.assertEqual(g.administrative_area, self.woreda_leaf.name)
		self.assertEqual(g.administrative_unit, "Village 2 West")
		discard_grievance(ticket)

	def test_kebele_digit_name_does_not_silently_misroute(self):
		"""Kebele passed as common numeric name (e.g. '1') must not match random other region."""
		res = submit(
			submitter_type="Individual Farmer",
			submitter_name="Tesfaye",
			contact_mobile="+251911334455",
			submission_channel="Mobile App",
			woreda=self.woreda_leaf.name,
			kebele="1",
			service_category="Inputs",
			grievance_type=self.gtype_doc.name,
			description="Fertilizer subsidy has not been delivered for 3 weeks.",
			consent_given=1,
		)
		self.assertEqual(res["status"], "success")
		ticket = res["data"]["ticket_number"]
		g = frappe.get_doc("Grievance", ticket)
		# Must remain attached to woreda, not random kebele '1' across the country
		self.assertEqual(g.administrative_area, self.woreda_leaf.name)
		self.assertEqual(g.administrative_unit, "1")
		discard_grievance(ticket)

	def test_nearest_ancestor_routing(self):
		# Create a broad rule on Region, and a specific rule on Woreda Leaf
		broad_rule = frappe.get_doc(
			{
				"doctype": "Grievance Routing Rule",
				"rule_precedence": 10,
				"service_category": "Inputs",
				"administrative_area": self.region_area.name,
				"assigned_dept": "Regional Agronomy Dept",
				"active": 1,
			}
		).insert(ignore_permissions=True)

		specific_rule = frappe.get_doc(
			{
				"doctype": "Grievance Routing Rule",
				"rule_precedence": 10,
				"service_category": "Inputs",
				"administrative_area": self.woreda_leaf.name,
				"assigned_dept": "Agriculture Dept",
				"active": 1,
			}
		).insert(ignore_permissions=True)

		g = None
		try:
			g = frappe.get_doc(
				{
					"doctype": "Grievance",
					"submitter_type": "Individual Farmer",
					"submitter_name": "Tesfaye",
					"contact_mobile": "+251911334455",
					"submission_channel": "Mobile App",
					"administrative_area": self.woreda_leaf.name,
					"service_category": "Inputs",
					"grievance_type": self.gtype_doc.name,
					"description": "Fertilizer subsidy has not been delivered for 3 weeks.",
				}
			).insert(ignore_permissions=True)

			matched = routing.find_matching_rule(g)
			self.assertIsNotNone(matched)
			# Specific woreda rule must win over broader region rule because it has narrower span
			self.assertEqual(matched.name, specific_rule.name)
			self.assertEqual(matched.assigned_dept, "Agriculture Dept")
		finally:
			if g and frappe.db.exists("Grievance", g.name):
				discard_grievance(g.name)
			if frappe.db.exists("Grievance Routing Rule", broad_rule.name):
				frappe.delete_doc(
					"Grievance Routing Rule", broad_rule.name, force=True, ignore_permissions=True
				)
			if frappe.db.exists("Grievance Routing Rule", specific_rule.name):
				frappe.delete_doc(
					"Grievance Routing Rule", specific_rule.name, force=True, ignore_permissions=True
				)


class TestGrievanceSubmitterOwnership(FrappeTestCase):
	"""The submission endpoint owns identity: a caller cannot choose whose case it is.

	`permissions.py` grants read and write on a grievance by matching `submitter`
	against the profiles a user owns, so if the request could set that field a caller
	could file cases attributed to other people.
	"""

	def setUp(self):
		if not frappe.db.exists("Grievance Submitter Type", "Individual Farmer"):
			frappe.get_doc(
				{"doctype": "Grievance Submitter Type", "type_name": "Individual Farmer", "code": "IND"}
			).insert(ignore_permissions=True)

		self.owner_user = self._user("owner.ownership@example.com", ["Grievance Submitter"])
		self.other_user = self._user("other.ownership@example.com", ["Grievance Submitter"])
		self.officer_user = self._user("officer.ownership@example.com", ["Grievance Officer"])

		self.owner_profile = self._profile(self.owner_user, "Alemayehu Bekele", "+251911000111")
		self.other_profile = self._profile(self.other_user, "Hirut Tadesse", "+251911000222")

		self.addCleanup(frappe.set_user, "Administrator")

	def _user(self, email, roles):
		if frappe.db.exists("User", email):
			frappe.delete_doc("User", email, force=True, ignore_permissions=True)
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split(".")[0].title(),
				"send_welcome_email": 0,
				"roles": [{"role": role} for role in roles],
			}
		).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "User", email, force=True, ignore_permissions=True)
		return user.name

	def _profile(self, user, name, mobile):
		profile = frappe.get_doc(
			{
				"doctype": "Grievance Submitter Profile",
				"submitter_type": "Individual Farmer",
				"submitter_name": name,
				"contact_mobile": mobile,
				"user": user,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc,
			"Grievance Submitter Profile",
			profile.name,
			force=True,
			ignore_permissions=True,
		)
		return profile

	def test_submitter_identity_comes_from_session_not_request(self):
		"""A submitter naming someone else's profile still files as themselves."""
		frappe.set_user(self.owner_user)

		identity = _resolve_submitter_identity(
			{
				"submitter": self.other_profile.name,
				"submitter_name": "Forged Name",
				"contact_mobile": "+251900000000",
			}
		)

		self.assertEqual(identity["submitter"], self.owner_profile.name)
		self.assertEqual(identity["submitter_name"], "Alemayehu Bekele")
		self.assertEqual(identity["contact_mobile"], "+251911000111")

	def test_submitter_cannot_set_assisting_officer(self):
		frappe.set_user(self.owner_user)

		identity = _resolve_submitter_identity({"assisted_by_officer": self.officer_user})

		self.assertIsNone(identity["assisted_by_officer"])

	def test_submitter_without_profile_is_rejected(self):
		orphan = self._user("orphan.ownership@example.com", ["Grievance Submitter"])
		frappe.set_user(orphan)

		with self.assertRaises(frappe.ValidationError):
			_resolve_submitter_identity({})

	def test_blocked_profile_cannot_file(self):
		frappe.db.set_value("Grievance Submitter Profile", self.owner_profile.name, "is_blocked", 1)
		frappe.set_user(self.owner_user)

		with self.assertRaises(frappe.ValidationError):
			_resolve_submitter_identity({})

	def test_officer_files_on_behalf_and_is_recorded_as_assisting(self):
		"""The named submitter stays the owner; the officer is the audit trail."""
		frappe.set_user(self.officer_user)

		identity = _resolve_submitter_identity(
			{"submitter": self.other_profile.name, "submitter_name": "Ignored"}
		)

		self.assertEqual(identity["submitter"], self.other_profile.name)
		self.assertEqual(identity["submitter_name"], "Hirut Tadesse")
		self.assertEqual(identity["assisted_by_officer"], self.officer_user)

	def test_officer_may_supply_details_for_a_walk_in_without_a_profile(self):
		frappe.set_user(self.officer_user)

		identity = _resolve_submitter_identity(
			{"submitter_name": "Walk In Caller", "contact_mobile": "+251911000333"}
		)

		self.assertIsNone(identity["submitter"])
		self.assertEqual(identity["submitter_name"], "Walk In Caller")
		self.assertEqual(identity["assisted_by_officer"], self.officer_user)

	def test_server_owned_fields_are_stripped_from_the_request(self):
		"""The area snapshot and ownership columns are not settable by the caller."""
		for field in ("submitter", "assisted_by_officer", "area_lft", "area_path_code", "status"):
			self.assertIn(field, CLIENT_IMMUTABLE_FIELDS)

	def test_authenticated_submit_may_omit_identity_at_schema_edge(self):
		"""Profile-backed submitters send case fields only; pydantic must not require identity."""
		from oan_auth_service.api.utils import validate_mobile

		from oan_grievance_service.api.v1.grievance import SubmitGrievanceRequest

		# HTTP edge: no submitter_type / name / mobile — would have failed RequiredPhone.
		req = SubmitGrievanceRequest(
			submission_channel="Mobile App",
			administrative_area="placeholder-area",
			service_category="Inputs",
			grievance_type="placeholder-type",
			description="Fertilizer subsidy has not been delivered for 3 weeks.",
		)
		self.assertIsNone(req.submitter_type)
		self.assertIsNone(req.submitter_name)
		self.assertIsNone(req.contact_mobile)

		frappe.set_user(self.owner_user)
		resolved = {**req.model_dump(), **_resolve_submitter_identity({})}

		self.assertEqual(resolved["submitter"], self.owner_profile.name)
		self.assertEqual(resolved["submitter_name"], "Alemayehu Bekele")
		self.assertEqual(resolved["contact_mobile"], "+251911000111")
		self.assertEqual(resolved["submitter_type"], "Individual Farmer")

		# Strict Frappe phone validation with country code
		self.assertEqual(
			validate_mobile(resolved["contact_mobile"]),
			"+251911000111",
		)
		self.assertEqual(
			validate_mobile("+251911000111"),
			"+251911000111",
		)


class TestGrievanceStaffOptions(FrappeTestCase):
	def setUp(self):
		if not frappe.db.exists("Grievance Service Category", "Inputs"):
			frappe.get_doc(
				{
					"doctype": "Grievance Service Category",
					"category_name": "Inputs",
					"code": "001",
					"is_active": 1,
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("Grievance Department", "Test Agri Dept"):
			frappe.get_doc(
				{
					"doctype": "Grievance Department",
					"dept_name": "Test Agri Dept",
					"email_account": "test_agri@example.com",
					"active": 1,
				}
			).insert(ignore_permissions=True)

		# Setup officer user
		if frappe.db.exists("User", "officer.options@example.com"):
			frappe.delete_doc("User", "officer.options@example.com", force=True, ignore_permissions=True)
		self.officer_user = frappe.get_doc(
			{
				"doctype": "User",
				"email": "officer.options@example.com",
				"first_name": "Officer",
				"send_welcome_email": 0,
				"roles": [{"role": "Grievance Officer"}],
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "User", "officer.options@example.com", force=True, ignore_permissions=True
		)

		# Setup submitter user
		if frappe.db.exists("User", "submitter.options@example.com"):
			frappe.delete_doc("User", "submitter.options@example.com", force=True, ignore_permissions=True)
		self.submitter_user = frappe.get_doc(
			{
				"doctype": "User",
				"email": "submitter.options@example.com",
				"first_name": "Submitter",
				"send_welcome_email": 0,
				"roles": [{"role": "Grievance Submitter"}],
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "User", "submitter.options@example.com", force=True, ignore_permissions=True
		)

		self.addCleanup(frappe.set_user, "Administrator")

	def test_officer_can_fetch_staff_options(self):
		from oan_grievance_service.api.v1.grievance import options

		frappe.set_user(self.officer_user.name)
		res = options()

		self.assertIn("data", res)
		data = res["data"]

		# Validate departments
		self.assertIn("departments", data)
		dept_names = [d["department_name"] for d in data["departments"]]
		self.assertIn("Test Agri Dept", dept_names)

		# Validate lifecycle statuses with metadata
		self.assertIn("statuses", data)
		status_names = [s["status"] for s in data["statuses"]]
		self.assertIn("Submitted", status_names)
		self.assertIn("In Progress", status_names)
		self.assertIn("Closed", status_names)

		# Validate categories and channels
		self.assertIn("service_categories", data)
		self.assertIn("grievance_types", data)
		self.assertIn("submission_channels", data)

	def test_submitter_can_access_options(self):
		from oan_grievance_service.api.v1.grievance import options

		frappe.set_user(self.submitter_user.name)
		res = options()
		self.assertEqual(res.get("status"), "success")
		self.assertIn("data", res)
		self.assertIn("service_categories", res["data"])

	def test_unauthorized_user_cannot_access_options(self):
		from oan_grievance_service.api.v1.grievance import options

		frappe.set_user("Guest")
		res = options()
		self.assertEqual(res.get("status"), "error")
		self.assertEqual(res.get("code"), "PERMISSION_DENIED")
		self.assertEqual(frappe.response.get("http_status_code"), 403)
