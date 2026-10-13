# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""Organization (NGO / FPO / Cooperative) registration, verification and filing rights."""

import json
import random

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1 import submitter
from oan_grievance_service.api.v1.grievance import _resolve_submitter_identity
from oan_grievance_service.api.v1.organization import (
	get_my_organization,
	get_organization,
	list_organizations,
	register_organization,
	set_organization_status,
	update_my_organization,
)
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import organization as service

PROFILE = "Grievance Submitter Profile"


def _user(label: str, *roles: str) -> str:
	email = f"stg449-{label}-{frappe.generate_hash(length=6)}@example.com"
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": f"STG449 {label}",
			"mobile_no": "+251911" + "".join(random.choices("0123456789", k=6)),
			"roles": [{"role": role} for role in roles],
		}
	).insert(ignore_permissions=True)
	return email


def _payload(**overrides) -> dict:
	payload = {
		"legal_name": "Selam Farmers Cooperative Union",
		"org_type": "FPO",
		"entity_type": "Cooperative Union",
		"registration_number": f"REG-{frappe.generate_hash(length=8).upper()}",
		"contact_email": "office@selam-coop.example.com",
		"contact_phone": "+251911" + "".join(random.choices("0123456789", k=6)),
		"street_address": "Bole Road 12",
		"zone": "East Shewa",
		"region": "Oromia",
		"country": "Ethiopia",
		"postal_code": "1000",
	}
	payload.update(overrides)
	return payload


class OrganizationTestCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		# An API error rolls the transaction back, which would erase this test's fixtures.
		# A REST request commits at its end, which would make them permanent.
		self._rollback, self._commit = frappe.db.rollback, frappe.db.commit
		frappe.db.rollback = frappe.db.commit = lambda *args, **kwargs: None
		# Every test makes five users, which trips core's "Throttled" cap on user creation.
		self._throttle = frappe.conf.get("throttle_user_limit")
		frappe.conf.throttle_user_limit = 100000
		self.rep = _user("rep", C.ROLE_SUBMITTER)
		self.other_rep = _user("other", C.ROLE_SUBMITTER)
		self.admin = _user("admin", C.ROLE_ADMIN)
		self.officer = _user("officer", C.ROLE_OFFICER)
		self.reviewer = _user("reviewer", C.ROLE_REVIEW_OFFICER)
		frappe.clear_messages()

	def tearDown(self):
		frappe.db.rollback, frappe.db.commit = self._rollback, self._commit
		if self._throttle is None:
			frappe.conf.pop("throttle_user_limit", None)
		else:
			frappe.conf.throttle_user_limit = self._throttle
		frappe.set_user("Administrator")

	def register(self, user=None, **overrides):
		frappe.set_user(user or self.rep)
		result = register_organization(**_payload(**overrides))
		frappe.set_user("Administrator")
		return result

	def activate(self, org_id):
		frappe.set_user(self.admin)
		result = set_organization_status(org_id, C.ORG_STATUS_ACTIVE)
		frappe.set_user("Administrator")
		return result

	def profile_of(self, user):
		return frappe.get_doc(PROFILE, frappe.db.get_value(PROFILE, {"user": user}, "name"))


class TestRegistration(OrganizationTestCase):
	def test_register_creates_org_in_review_and_blocked_profile(self):
		result = self.register(brand_name="Selam", website="https://selam-coop.example.com")
		self.assertEqual(result["status"], "success", msg=result)
		org = result["data"]
		self.assertEqual(org["status"], C.ORG_STATUS_IN_REVIEW)
		self.assertEqual(org["org_type"], "FPO")
		self.assertEqual(org["representative_user"], self.rep)
		self.assertTrue(org["representative_name"])
		self.assertEqual(org["brand_name"], "Selam")
		self.assertIsNone(org["verified_by"])

		profile = self.profile_of(self.rep)
		self.assertEqual(profile.organization, org["name"])
		self.assertEqual(profile.submitter_type, "FPO")
		self.assertEqual(profile.submitter_name, org["legal_name"])
		self.assertEqual(profile.dedupe_key, f"org:{org['registration_number']}")
		self.assertEqual(profile.is_blocked, 1)
		self.assertIn("In Review", profile.blocked_reason)

	def test_registration_number_is_normalised_and_alias_accepted(self):
		payload = _payload()
		number = payload.pop("registration_number")
		frappe.set_user(self.rep)
		result = register_organization(**payload, tax_registration_number=number.lower())
		self.assertEqual(result["status"], "success", msg=result)
		self.assertEqual(result["data"]["registration_number"], number)

	def test_cooperative_type_is_accepted_with_its_entity_types(self):
		result = self.register(org_type="Cooperative", entity_type="Primary Cooperative")
		self.assertEqual(result["data"]["org_type"], "Cooperative")
		self.assertEqual(self.profile_of(self.rep).submitter_type, "Cooperative")

	def test_existing_farmer_profile_is_converted_not_duplicated(self):
		farmer = frappe.get_doc(
			{
				"doctype": PROFILE,
				"user": self.rep,
				"submitter_type": "Individual Farmer",
				"submitter_name": "Rep Farmer",
				"contact_mobile": "+251911223344",
			}
		).insert(ignore_permissions=True)
		self.register()
		self.assertEqual(frappe.db.count(PROFILE, {"user": self.rep}), 1)
		profile = self.profile_of(self.rep)
		self.assertEqual(profile.name, farmer.name)
		self.assertTrue(profile.dedupe_key.startswith("org:"))
		self.assertEqual(profile.is_blocked, 1)

	def test_validation_errors_are_keyed_by_field(self):
		frappe.set_user(self.rep)
		result = register_organization(
			**_payload(contact_email="not-an-email", postal_code="!", registration_number="x y")
		)
		self.assertEqual(result["code"], "VALIDATION_ERROR")
		self.assertIn("contact_email", result["details"])
		self.assertIn("postal_code", result["details"])
		self.assertIn("registration_number", result["details"])

	def test_missing_required_field_is_rejected(self):
		frappe.set_user(self.rep)
		payload = _payload()
		del payload["legal_name"]
		result = register_organization(**payload)
		self.assertEqual(result["code"], "VALIDATION_ERROR")
		self.assertIn("legal_name", result["details"])

	def test_status_cannot_be_chosen_at_registration(self):
		frappe.set_user(self.rep)
		result = register_organization(**_payload(), status="Active")
		self.assertEqual(result["code"], "VALIDATION_ERROR")
		self.assertIn("status", result["details"])
		self.assertFalse(frappe.db.exists(service.DOCTYPE, {"representative_user": self.rep}))

	def test_entity_type_must_match_org_type(self):
		frappe.set_user(self.rep)
		result = register_organization(**_payload(org_type="NGO", entity_type="Cooperative Union"))
		self.assertEqual(result["code"], "VALIDATION_ERROR")
		self.assertIn("Entity type", result["message"])

	def test_phone_must_be_a_valid_number(self):
		frappe.set_user(self.rep)
		result = register_organization(**_payload(contact_phone="12345"))
		self.assertEqual(result["code"], "VALIDATION_ERROR")

	def test_one_organization_per_account(self):
		self.register()
		again = self.register()
		self.assertEqual(again["code"], "DUPLICATE_ENTRY")
		self.assertEqual(frappe.db.count(service.DOCTYPE, {"representative_user": self.rep}), 1)

	def test_registration_number_is_unique_across_accounts(self):
		first = self.register()
		clash = self.register(
			user=self.other_rep, registration_number=first["data"]["registration_number"].lower()
		)
		self.assertEqual(clash["code"], "DUPLICATE_ENTRY")
		self.assertIsNone(frappe.db.get_value(PROFILE, {"user": self.other_rep}, "organization"))

	def test_only_submitters_register(self):
		for user in (self.officer, self.reviewer, self.admin):
			frappe.set_user(user)
			result = register_organization(**_payload())
			self.assertEqual(result["code"], "PERMISSION_DENIED", msg=user)

	def test_guest_cannot_register(self):
		frappe.set_user("Guest")
		result = register_organization(**_payload())
		self.assertEqual(result["status"], "error")


class TestMyOrganization(OrganizationTestCase):
	def test_get_returns_404_until_registered_then_the_record(self):
		frappe.set_user(self.rep)
		self.assertEqual(get_my_organization()["code"], "NOT_FOUND")
		frappe.set_user("Administrator")
		org = self.register()["data"]
		frappe.set_user(self.rep)
		self.assertEqual(get_my_organization()["data"]["name"], org["name"])

	def test_representative_sees_only_their_own_organization(self):
		mine = self.register()["data"]
		theirs = self.register(user=self.other_rep)["data"]
		frappe.set_user(self.rep)
		self.assertEqual(get_my_organization()["data"]["name"], mine["name"])
		frappe.set_user(self.other_rep)
		self.assertEqual(get_my_organization()["data"]["name"], theirs["name"])

	def test_update_changes_profile_fields_and_keeps_status(self):
		org = self.register()["data"]
		frappe.set_user(self.rep)
		result = update_my_organization(contact_phone="+251922334455", website=None, kebele="03")
		self.assertEqual(result["status"], "success", msg=result)
		self.assertEqual(result["data"]["contact_phone"], "+251922334455")
		self.assertEqual(result["data"]["kebele"], "03")
		self.assertEqual(result["data"]["status"], C.ORG_STATUS_IN_REVIEW)
		self.assertEqual(self.profile_of(self.rep).contact_mobile, "+251922334455")
		self.assertEqual(org["name"], result["data"]["name"])

	def test_update_without_fields_is_rejected(self):
		self.register()
		frappe.set_user(self.rep)
		self.assertEqual(update_my_organization()["code"], "VALIDATION_ERROR")

	def test_update_cannot_set_status_or_representative(self):
		self.register()
		frappe.set_user(self.rep)
		for field, value in (
			("status", "Active"),
			("verification_status", "Active"),
			("representative_user", self.other_rep),
			("verified_by", self.admin),
		):
			result = update_my_organization(**{field: value})
			self.assertEqual(result["code"], "VALIDATION_ERROR", msg=field)
			self.assertIn(field, result["details"])
		frappe.set_user("Administrator")
		self.assertEqual(
			frappe.db.get_value(service.DOCTYPE, {"representative_user": self.rep}, "verification_status"),
			C.ORG_STATUS_IN_REVIEW,
		)

	def test_update_rejects_a_taken_registration_number(self):
		taken = self.register(user=self.other_rep)["data"]["registration_number"]
		self.register()
		frappe.set_user(self.rep)
		result = update_my_organization(registration_number=taken)
		self.assertEqual(result["code"], "DUPLICATE_ENTRY")

	def test_changing_identity_of_an_active_organization_returns_it_to_review(self):
		org = self.register()["data"]
		self.activate(org["name"])
		self.assertEqual(self.profile_of(self.rep).is_blocked, 0)

		frappe.set_user(self.rep)
		result = update_my_organization(contact_email="new@selam-coop.example.com")
		self.assertEqual(result["data"]["status"], C.ORG_STATUS_ACTIVE)

		result = update_my_organization(legal_name="Selam Union (renamed)")
		self.assertEqual(result["data"]["status"], C.ORG_STATUS_IN_REVIEW)
		self.assertIn("Legal Name", result["data"]["status_reason"])
		self.assertEqual(self.profile_of(self.rep).is_blocked, 1)

	def test_correcting_the_registration_number_reanchors_the_profile(self):
		self.register()
		frappe.set_user(self.rep)
		result = update_my_organization(registration_number="NEW-7788")
		self.assertEqual(result["status"], "success", msg=result)
		self.assertEqual(self.profile_of(self.rep).dedupe_key, "org:NEW-7788")

	def test_suspended_organization_is_read_only_to_its_representative(self):
		org = self.register()["data"]
		frappe.set_user(self.admin)
		set_organization_status(org["name"], C.ORG_STATUS_SUSPENDED, "Incomplete documents")
		frappe.set_user(self.rep)
		result = update_my_organization(kebele="05")
		self.assertEqual(result["code"], "PERMISSION_DENIED")
		self.assertEqual(get_my_organization()["data"]["status_reason"], "Incomplete documents")


class TestVerificationWorkflow(OrganizationTestCase):
	def test_admin_activation_unblocks_the_profile_and_records_the_decision(self):
		org = self.register()["data"]
		result = self.activate(org["name"])
		self.assertEqual(result["status"], "success", msg=result)
		self.assertEqual(result["data"]["status"], C.ORG_STATUS_ACTIVE)
		self.assertEqual(result["data"]["verified_by"], self.admin)
		self.assertTrue(result["data"]["verified_on"])
		profile = self.profile_of(self.rep)
		self.assertEqual(profile.is_blocked, 0)
		self.assertIsNone(profile.blocked_reason)

	def test_suspending_needs_a_reason_and_blocks_the_profile(self):
		org = self.register()["data"]
		frappe.set_user(self.admin)
		self.assertEqual(
			set_organization_status(org["name"], C.ORG_STATUS_SUSPENDED)["code"], "VALIDATION_ERROR"
		)
		result = set_organization_status(org["name"], C.ORG_STATUS_SUSPENDED, "Licence expired")
		self.assertEqual(result["data"]["status"], C.ORG_STATUS_SUSPENDED)
		self.assertEqual(result["data"]["status_reason"], "Licence expired")
		profile = self.profile_of(self.rep)
		self.assertEqual(profile.is_blocked, 1)
		self.assertIn("Licence expired", profile.blocked_reason)

	def test_full_lifecycle_reinstate_and_reopen(self):
		org = self.register()["data"]["name"]
		frappe.set_user(self.admin)
		set_organization_status(org, C.ORG_STATUS_ACTIVE)
		set_organization_status(org, C.ORG_STATUS_SUSPENDED, "Under investigation")
		reopened = set_organization_status(org, C.ORG_STATUS_IN_REVIEW, "Documents resubmitted")
		self.assertEqual(reopened["data"]["status"], C.ORG_STATUS_IN_REVIEW)
		self.assertEqual(self.profile_of(self.rep).is_blocked, 1)
		set_organization_status(org, C.ORG_STATUS_SUSPENDED, "Rejected after review")
		reinstated = set_organization_status(org, C.ORG_STATUS_ACTIVE)
		self.assertEqual(reinstated["data"]["status"], C.ORG_STATUS_ACTIVE)
		self.assertEqual(self.profile_of(self.rep).is_blocked, 0)

	def test_repeating_the_current_status_is_not_a_transition(self):
		org = self.register()["data"]["name"]
		self.activate(org)
		frappe.set_user(self.admin)
		again = set_organization_status(org, C.ORG_STATUS_ACTIVE)
		self.assertEqual(again["status"], "success")
		self.assertEqual(
			frappe.db.get_value(service.DOCTYPE, org, "verification_status"), C.ORG_STATUS_ACTIVE
		)

	def test_the_transition_table_guards_every_save(self):
		org = frappe.get_doc(service.DOCTYPE, self.register()["data"]["name"])
		org.verification_status = "Archived"
		with self.assertRaises(frappe.ValidationError):
			org.save(ignore_permissions=True)
		self.assertEqual(
			{status for _from, status in service.TRANSITIONS},
			{C.ORG_STATUS_IN_REVIEW, C.ORG_STATUS_ACTIVE, C.ORG_STATUS_SUSPENDED},
		)

	def test_verifying_does_not_lift_a_block_an_officer_placed(self):
		org = self.register()["data"]["name"]
		profile = self.profile_of(self.rep)
		profile.blocked_reason = "Demonstrated abuse"
		profile.save(ignore_permissions=True)
		self.activate(org)
		profile.reload()
		self.assertEqual(profile.is_blocked, 1)
		self.assertEqual(profile.blocked_reason, "Demonstrated abuse")

	def test_unknown_organization_is_404(self):
		frappe.set_user(self.admin)
		self.assertEqual(set_organization_status("ORG-99999", C.ORG_STATUS_ACTIVE)["code"], "NOT_FOUND")
		self.assertEqual(get_organization("ORG-99999")["code"], "NOT_FOUND")

	def test_invalid_status_value_is_rejected(self):
		org = self.register()["data"]["name"]
		frappe.set_user(self.admin)
		self.assertEqual(set_organization_status(org, "Archived")["code"], "VALIDATION_ERROR")


class TestPermissions(OrganizationTestCase):
	def test_only_admins_decide(self):
		org = self.register()["data"]["name"]
		for user in (self.rep, self.other_rep, self.officer, self.reviewer):
			frappe.set_user(user)
			result = set_organization_status(org, C.ORG_STATUS_ACTIVE)
			self.assertEqual(result["code"], "PERMISSION_DENIED", msg=user)
		frappe.set_user("Administrator")
		self.assertEqual(
			frappe.db.get_value(service.DOCTYPE, org, "verification_status"), C.ORG_STATUS_IN_REVIEW
		)

	def test_the_representative_cannot_verify_their_own_organization(self):
		org = self.register()["data"]["name"]
		frappe.set_user(self.rep)
		self.assertEqual(set_organization_status(org, C.ORG_STATUS_ACTIVE)["code"], "PERMISSION_DENIED")
		self.assertEqual(self.profile_of(self.rep).is_blocked, 1)

	def test_queue_and_dossier_are_for_admins_and_reviewers(self):
		org = self.register()["data"]["name"]
		for user in (self.admin, self.reviewer):
			frappe.set_user(user)
			self.assertEqual(list_organizations()["status"], "success", msg=user)
			self.assertEqual(get_organization(org)["data"]["name"], org, msg=user)
		for user in (self.rep, self.other_rep, self.officer):
			frappe.set_user(user)
			self.assertEqual(list_organizations()["code"], "PERMISSION_DENIED", msg=user)
			self.assertEqual(get_organization(org)["code"], "PERMISSION_DENIED", msg=user)

	def test_staff_cannot_use_the_representative_endpoints(self):
		self.register()
		for user in (self.officer, self.reviewer, self.admin):
			frappe.set_user(user)
			self.assertEqual(get_my_organization()["code"], "PERMISSION_DENIED", msg=user)
			self.assertEqual(update_my_organization(kebele="1")["code"], "PERMISSION_DENIED", msg=user)

	def test_organization_doctype_is_not_readable_by_other_submitters(self):
		org = self.register()["data"]["name"]
		self.assertTrue(frappe.has_permission(service.DOCTYPE, "read", org, user=self.rep))
		self.assertFalse(frappe.has_permission(service.DOCTYPE, "read", org, user=self.other_rep))
		self.assertFalse(frappe.has_permission(service.DOCTYPE, "write", org, user=self.rep))


class TestQueue(OrganizationTestCase):
	def setUp(self):
		super().setUp()
		self.tag = frappe.generate_hash(length=6)
		self.ngo = self.register(
			org_type="NGO", entity_type="Local NGO", legal_name=f"Abay Water Trust {self.tag}"
		)["data"]
		self.fpo = self.register(user=self.other_rep, legal_name=f"Zeway Growers Union {self.tag}")["data"]
		self.activate(self.fpo["name"])
		frappe.set_user(self.admin)

	def names(self, **filters):
		result = list_organizations(**filters)
		self.assertEqual(result["status"], "success", msg=result)
		return [row["name"] for row in result["data"]["organizations"]]

	def test_status_and_type_filters(self):
		self.assertEqual(self.names(status="In Review").count(self.ngo["name"]), 1)
		self.assertNotIn(self.fpo["name"], self.names(status="In Review"))
		self.assertEqual(self.names(status="Active", org_type="FPO").count(self.fpo["name"]), 1)
		self.assertNotIn(self.ngo["name"], self.names(org_type="FPO"))

	def test_search_matches_name_and_registration_number(self):
		self.assertEqual(self.names(search=f"abay water trust {self.tag}"), [self.ngo["name"]])
		self.assertEqual(self.names(search=self.fpo["registration_number"][4:]), [self.fpo["name"]])
		self.assertEqual(self.names(search="no such organization"), [])

	def test_pagination_and_ordering_are_applied_in_the_query(self):
		page = list_organizations(search="STG449-nothing", page_size=1)
		self.assertEqual(page["data"]["pagination"]["total_count"], 0)
		both = list_organizations(page_size=1, sort="oldest", org_type=None)["data"]
		self.assertEqual(len(both["organizations"]), 1)
		self.assertGreaterEqual(both["pagination"]["total_count"], 2)
		self.assertTrue(both["pagination"]["has_next"])
		oldest = both["organizations"][0]["creation"]
		newest = list_organizations(page_size=1, sort="newest")["data"]["organizations"][0]["creation"]
		self.assertLessEqual(oldest, newest)

	def test_unknown_filter_and_bad_values_are_rejected(self):
		self.assertEqual(list_organizations(status="Archived")["code"], "VALIDATION_ERROR")
		self.assertEqual(list_organizations(page=0)["code"], "VALIDATION_ERROR")
		self.assertEqual(list_organizations(bogus="x")["code"], "VALIDATION_ERROR")

	def test_blank_filters_mean_unfiltered(self):
		self.assertIn(self.ngo["name"], self.names(status="", org_type="", search=""))


class TestFilingRights(OrganizationTestCase):
	def test_pending_organization_cannot_file(self):
		self.register()
		frappe.set_user(self.rep)
		with self.assertRaises(frappe.PermissionError):
			_resolve_submitter_identity({})

	def test_active_organization_files_through_its_representative(self):
		org = self.register()["data"]
		self.activate(org["name"])
		frappe.set_user(self.rep)
		identity = _resolve_submitter_identity({})
		self.assertEqual(identity["submitter"], self.profile_of(self.rep).name)
		self.assertEqual(identity["submitter_type"], "FPO")
		self.assertEqual(identity["submitter_name"], org["legal_name"])

	def test_suspending_stops_filing_at_once(self):
		org = self.register()["data"]["name"]
		self.activate(org)
		frappe.set_user(self.admin)
		set_organization_status(org, C.ORG_STATUS_SUSPENDED, "Fraud report")
		frappe.set_user(self.rep)
		with self.assertRaises(frappe.PermissionError):
			_resolve_submitter_identity({})

	def test_nobody_else_may_act_for_the_organization(self):
		org = self.register()["data"]["name"]
		self.activate(org)
		profile = self.profile_of(self.rep).name
		with self.assertRaises(frappe.PermissionError):
			service.assert_may_file(profile, self.other_rep)
		with self.assertRaises(frappe.PermissionError):
			service.assert_may_file(profile, "Guest")
		service.assert_may_file(profile, self.rep)

	def test_staff_may_file_for_an_active_organization_but_not_a_pending_one(self):
		org = self.register()["data"]["name"]
		profile = self.profile_of(self.rep).name
		with self.assertRaises(frappe.PermissionError):
			service.assert_may_file(profile, self.officer)
		self.activate(org)
		service.assert_may_file(profile, self.officer)

	def test_a_representative_filing_as_a_staff_named_profile_is_refused(self):
		"""The submitter named in the request is ignored for a non-staff caller."""
		org = self.register()["data"]["name"]
		self.activate(org)
		org_profile = self.profile_of(self.rep).name
		frappe.set_user(self.other_rep)
		with self.assertRaises(frappe.ValidationError):
			# No profile of their own: the org's is not reachable by naming it.
			_resolve_submitter_identity({"submitter": org_profile})

	def test_intake_by_phone_never_lands_on_an_organizations_profile(self):
		from oan_grievance_service.services.identity import find_or_create_submitter

		org = self.register()["data"]
		found = find_or_create_submitter(
			{"submitter_type": "Individual Farmer", "contact_mobile": org["contact_phone"]}
		)
		self.assertNotEqual(found, self.profile_of(self.rep).name)
		self.assertIsNone(frappe.db.get_value(PROFILE, found, "organization"))

	def test_profiles_without_an_organization_are_unaffected(self):
		farmer = frappe.get_doc(
			{
				"doctype": PROFILE,
				"user": self.other_rep,
				"submitter_type": "Individual Farmer",
				"submitter_name": "Plain Farmer",
				"contact_mobile": "+251933445566",
			}
		).insert(ignore_permissions=True)
		service.assert_may_file(farmer.name, self.other_rep)
		service.assert_may_file(None, self.other_rep)
		frappe.set_user(self.other_rep)
		self.assertEqual(_resolve_submitter_identity({})["submitter"], farmer.name)

	def test_staff_cannot_unblock_an_unverified_organizations_profile(self):
		org = self.register()["data"]["name"]
		profile = self.profile_of(self.rep).name
		for user in (self.officer, self.admin):
			frappe.set_user(user)
			result = submitter.unblock_submitter(profile)
			self.assertEqual(result["code"], "PERMISSION_DENIED", msg=user)
		self.assertEqual(frappe.db.get_value(PROFILE, profile, "is_blocked"), 1)
		self.activate(org)
		self.assertEqual(frappe.db.get_value(PROFILE, profile, "is_blocked"), 0)


class TestAuthMeProfile(OrganizationTestCase):
	"""`GET /auth/me` fills the Submit Grievance identity step from this hook."""

	def me(self, user):
		from oan_grievance_service.api.v1.profile import resolve_user_profile_hook

		namespace, data = resolve_user_profile_hook(frappe.get_doc("User", user))
		self.assertEqual(namespace, "grievance")
		return data

	def test_representative_gets_the_organization_details(self):
		org = self.register(org_type="NGO", entity_type="Local NGO", representative_name="Almaz Bekele")[
			"data"
		]
		data = self.me(self.rep)

		self.assertEqual(data["type"], "NGO")
		self.assertEqual(data["submitter_name"], org["legal_name"])
		self.assertEqual(data["registration_number"], org["registration_number"])
		self.assertEqual(data["representative_name"], "Almaz Bekele")
		self.assertEqual(
			data["organization"],
			{
				"id": org["name"],
				"legal_name": org["legal_name"],
				"brand_name": None,
				"org_type": "NGO",
				"entity_type": "Local NGO",
				"registration_number": org["registration_number"],
				"representative_name": "Almaz Bekele",
				"status": C.ORG_STATUS_IN_REVIEW,
				"status_reason": None,
				"can_file": False,
			},
		)
		# What the screen already read is still there.
		self.assertEqual(data["contact_mobile"], org["contact_phone"])
		self.assertEqual(data["contact_email"], org["contact_email"])
		self.assertEqual(data["identities"], [{"scheme": "org", "value": org["registration_number"]}])

	def test_organizations_expose_no_fayda_id(self):
		"""A representative who had a Fayda-keyed personal profile first does not leak it."""
		frappe.get_doc(
			{
				"doctype": PROFILE,
				"user": self.rep,
				"submitter_type": "Individual Farmer",
				"submitter_name": "Almaz Bekele",
				"contact_mobile": "+251911223344",
				"dedupe_key": "fayda:1122334455667788",
			}
		).insert(ignore_permissions=True)
		org = self.register(org_type="NGO", entity_type="Local NGO")["data"]
		data = self.me(self.rep)
		self.assertEqual(data["type"], "NGO")
		self.assertEqual(data["identities"], [{"scheme": "org", "value": org["registration_number"]}])
		self.assertNotIn("fayda", json.dumps(data).lower())
		self.assertNotIn("fayda", json.dumps(org).lower())
		frappe.set_user(self.rep)
		self.assertNotIn("fayda", json.dumps(get_my_organization()["data"]).lower())

	def test_can_file_follows_the_verification_status(self):
		org = self.register()["data"]["name"]
		self.activate(org)
		self.assertTrue(self.me(self.rep)["organization"]["can_file"])
		frappe.set_user(self.admin)
		set_organization_status(org, C.ORG_STATUS_SUSPENDED, "Licence expired")
		frappe.set_user("Administrator")
		details = self.me(self.rep)["organization"]
		self.assertFalse(details["can_file"])
		self.assertEqual(details["status"], C.ORG_STATUS_SUSPENDED)
		self.assertEqual(details["status_reason"], "Licence expired")

	def test_other_profiles_are_unchanged(self):
		frappe.get_doc(
			{
				"doctype": PROFILE,
				"user": self.other_rep,
				"submitter_type": "Individual Farmer",
				"submitter_name": "Plain Farmer",
				"contact_mobile": "+251933445566",
			}
		).insert(ignore_permissions=True)
		data = self.me(self.other_rep)
		self.assertNotIn("organization", data)
		self.assertNotIn("registration_number", data)
		self.assertNotIn("representative_name", data)


class TestRestRoutes(OrganizationTestCase):
	def call(self, user, path, method="GET", data=None):
		import frappe.api

		from oan_grievance_service.api.router import ensure_routes_registered
		from oan_grievance_service.tests.test_router import make_test_request

		ensure_routes_registered()
		frappe.set_user(user)
		response = frappe.api.handle(make_test_request(path, method=method, data=data))
		return response.status_code, json.loads(response.get_data(as_text=True))

	def test_register_review_and_decide_over_http(self):
		code, body = self.call(self.rep, "/api/v1/orgs", "POST", _payload())
		self.assertEqual(code, 200, msg=body)
		org = body["data"]
		self.assertEqual(org["status"], "In Review")

		code, body = self.call(self.rep, "/api/v1/orgs/me")
		self.assertEqual((code, body["data"]["name"]), (200, org["name"]))

		code, body = self.call(self.rep, f"/api/v1/orgs/{org['name']}/status", "PATCH", {"status": "Active"})
		self.assertEqual(code, 403)

		code, body = self.call(self.admin, "/api/v1/orgs?status=In Review&search=" + org["legal_name"])
		self.assertEqual(code, 200)
		self.assertIn(org["name"], [row["name"] for row in body["data"]["organizations"]])

		code, body = self.call(
			self.admin, f"/api/v1/orgs/{org['name']}/status", "PATCH", {"status": "Active"}
		)
		self.assertEqual((code, body["data"]["status"]), (200, "Active"))

		code, body = self.call(self.admin, f"/api/v1/orgs/{org['name']}")
		self.assertEqual((code, body["data"]["verified_by"]), (200, self.admin))

	def test_me_is_not_captured_by_the_id_route(self):
		code, body = self.call(self.admin, "/api/v1/orgs/me")
		self.assertEqual(code, 403)
		self.assertEqual(body["code"], "PERMISSION_DENIED")

	def test_unauthenticated_requests_are_refused(self):
		code, _body = self.call("Guest", "/api/v1/orgs")
		self.assertIn(code, (401, 403))
