# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""Service provider directory: search and single lookup for the grievance form's dropdown."""

import frappe
from frappe.tests.utils import FrappeTestCase
from oan_auth_service.api.middleware import validate_jwt_request
from oan_auth_service.api.router import ensure_routes_registered
from oan_auth_service.tests.test_router import make_test_request
from oan_auth_service.tests.utils import configured_keys

from oan_grievance_service.api.v1.service_provider import get_service_provider, list_service_providers
from oan_grievance_service.tests.fixtures import a_service_provider, an_administrative_area
from oan_grievance_service.tests.test_category_assignment import _keep_transaction, _user


class TestServiceProviderApi(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		t = cls.tag = frappe.generate_hash(length=6)

		# Testland > Region A > Zone A > Woreda 1 > Kebele 1, with Woreda 2 beside Woreda 1
		# and Region B > Zone B > Woreda 3 in another branch of the tree.
		country = an_administrative_area(f"Testland {t}", None, "Country")
		cls.region = an_administrative_area(f"Region A {t}", country, "Region")
		zone = an_administrative_area(f"Zone A {t}", cls.region, "Zone")
		cls.woreda = an_administrative_area(f"Woreda 1 {t}", zone, "Woreda")
		cls.sibling_woreda = an_administrative_area(f"Woreda 2 {t}", zone, "Woreda")
		cls.kebele = an_administrative_area(f"Kebele 1 {t}", cls.woreda, "Kebele")
		other_region = an_administrative_area(f"Region B {t}", country, "Region")
		other_zone = an_administrative_area(f"Zone B {t}", other_region, "Zone")
		other_woreda = an_administrative_area(f"Woreda 3 {t}", other_zone, "Woreda")

		cls.national = a_service_provider(f"{t} National Bank", provider_type="Bank")
		cls.regional = a_service_provider(
			f"{t} Region Union", provider_type="Cooperative Union", administrative_area=cls.region
		)
		cls.local = a_service_provider(
			f"{t} Woreda Coop",
			provider_type="Cooperative",
			administrative_area=cls.woreda,
			registration_number=f"REG-{t}",
		)
		cls.store = a_service_provider(
			f"{t} Kebele Store",
			provider_type="Input Store",
			administrative_area=cls.kebele,
			external_ref=f"PB-{t}",
			registered_region="Kebele Land",
			logo="/files/store-logo.png",
		)
		cls.branch = a_service_provider(
			f"{t} Branch",
			provider_type="Bank",
			parent_provider=cls.national,
			administrative_area=cls.woreda,
		)
		cls.sibling = a_service_provider(
			f"{t} Sibling Market", provider_type="Market", administrative_area=cls.sibling_woreda
		)
		cls.elsewhere = a_service_provider(
			f"{t} Elsewhere Office", provider_type="Government Office", administrative_area=other_woreda
		)
		cls.infix = a_service_provider(f"Aardvark {t} Infix", administrative_area=cls.kebele)
		cls.closed = a_service_provider(
			f"{t} Closed Store", provider_type="Input Store", administrative_area=cls.woreda, is_active=0
		)

		cls.submitter = _user(f"sp-{t}-submitter@example.com", "SP Submitter", role="Grievance Submitter")
		# One user per role that holds `read` on the provider DocType, taken from the DocType itself.
		cls.readers = {
			p.role: _user(f"sp-{t}-{i}@example.com", f"SP Reader {i}", role=p.role)
			for i, p in enumerate(frappe.get_meta("Grievance Service Provider").permissions)
			if p.read
		}
		cls.outsider = f"sp-{t}-outsider@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": cls.outsider,
				"first_name": "SP Outsider",
				"send_welcome_email": 0,
				"roles": [],
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _search(self, **params):
		result = list_service_providers(**params)
		self.assertEqual(result["status"], "success", msg=result)
		return result["data"]

	def _ids(self, **params):
		return [item["id"] for item in self._search(**params)["items"]]

	def _error(self, fn, *args, **params):
		with _keep_transaction():
			return fn(*args, **params)

	# --- search ---------------------------------------------------------------------------

	def test_search_hides_inactive_providers_and_matches_names_in_any_case(self):
		expected = {
			self.national,
			self.regional,
			self.local,
			self.store,
			self.branch,
			self.sibling,
			self.elsewhere,
			self.infix,
		}
		self.assertEqual(set(self._ids(search=self.tag)), expected)
		self.assertEqual(set(self._ids(search=self.tag.upper())), expected)

	def test_search_matches_the_registration_number(self):
		self.assertEqual(self._ids(search=f"reg-{self.tag}"), [self.local])

	def test_search_matches_the_parent_name_so_a_head_office_finds_its_branches(self):
		self.assertEqual(set(self._ids(search=f"{self.tag} National Bank")), {self.national, self.branch})

	def test_search_treats_like_wildcards_as_text(self):
		self.assertEqual(self._search(search="%%")["pagination"]["total_count"], 0)
		self.assertEqual(self._search(search="__")["pagination"]["total_count"], 0)

	def test_a_blank_search_means_no_search(self):
		data = self._search(search="  ", provider_type="Cooperative Union")
		self.assertIn(self.regional, [item["id"] for item in data["items"]])

	def test_search_filters_by_provider_type(self):
		self.assertEqual(set(self._ids(search=self.tag, provider_type="Bank")), {self.national, self.branch})

	def test_names_that_start_with_the_search_text_sort_before_names_that_contain_it(self):
		ids = self._ids(search=self.tag)
		self.assertEqual(ids[-1], self.infix)

	# --- area filter ----------------------------------------------------------------------

	def test_area_filter_keeps_the_area_its_ancestors_and_nationwide_providers_only(self):
		got = set(self._ids(search=self.tag, area=self.kebele))
		self.assertEqual(got, {self.national, self.regional, self.local, self.branch, self.store, self.infix})

	def test_area_filter_includes_providers_below_the_area_and_excludes_siblings(self):
		got = set(self._ids(search=self.tag, area=self.woreda))
		self.assertEqual(got, {self.national, self.regional, self.local, self.branch, self.store, self.infix})
		self.assertNotIn(self.sibling, got)
		self.assertNotIn(self.elsewhere, got)

	def test_area_filter_at_region_level_takes_in_the_whole_subtree(self):
		got = set(self._ids(search=self.tag, area=self.region))
		self.assertIn(self.sibling, got)
		self.assertIn(self.store, got)
		self.assertNotIn(self.elsewhere, got)

	def test_the_most_local_provider_sorts_first_and_a_nationwide_one_last(self):
		ids = [i for i in self._ids(search=self.tag, area=self.kebele) if i != self.infix]
		# Same-depth providers (branch, local coop) fall back to name order.
		self.assertEqual(ids, [self.store, self.branch, self.local, self.regional, self.national])

	def test_an_unknown_area_is_not_found(self):
		result = self._error(list_service_providers, area="NO-SUCH-AREA")
		self.assertEqual(result["code"], "NOT_FOUND", msg=result)

	# --- response shape and paging ----------------------------------------------------------

	def test_items_carry_the_parent_and_a_root_to_leaf_area_label(self):
		by_id = {item["id"]: item for item in self._search(search=self.tag)["items"]}

		store = by_id[self.store]
		self.assertEqual(store["name"], f"{self.tag} Kebele Store")
		self.assertEqual(store["type"], "Input Store")
		self.assertIsNone(store["parent"])
		self.assertIsNone(store["registration_number"])
		self.assertEqual(
			store["area"],
			{
				"id": self.kebele,
				"name": f"Kebele 1 {self.tag}",
				"path_label": f" {chr(0x203A)} ".join(
					f"{n} {self.tag}" for n in ("Region A", "Zone A", "Woreda 1", "Kebele 1")
				),
			},
		)
		self.assertEqual(
			by_id[self.branch]["parent"], {"id": self.national, "name": f"{self.tag} National Bank"}
		)
		self.assertEqual(by_id[self.local]["registration_number"], f"REG-{self.tag}")
		self.assertIsNone(by_id[self.national]["area"])
		self.assertNotIn("is_active", store)

	def test_items_carry_the_sources_id_region_and_logo_as_the_source_named_them(self):
		by_id = {item["id"]: item for item in self._search(search=self.tag)["items"]}

		store = by_id[self.store]
		self.assertEqual(store["external_ref"], f"PB-{self.tag}")
		self.assertEqual(store["registered_region"], "Kebele Land")
		self.assertEqual(store["logo"], "/files/store-logo.png")
		# A hand-made provider has none of the three, and says so with null.
		national = by_id[self.national]
		self.assertEqual(
			(national["external_ref"], national["registered_region"], national["logo"]), (None, None, None)
		)

	def test_the_lookup_carries_the_same_fields_as_a_search_result(self):
		data = get_service_provider(self.store)["data"]
		self.assertEqual(
			(data["external_ref"], data["registered_region"], data["logo"]),
			(f"PB-{self.tag}", "Kebele Land", "/files/store-logo.png"),
		)
		self.assertEqual(
			set(data),
			{*self._search(search=self.tag)["items"][0], "is_active"},
			"same item shape plus is_active",
		)

	def test_pages_split_the_results_without_gaps_or_repeats(self):
		first = self._search(search=self.tag, page_size=3)
		self.assertEqual(
			first["pagination"],
			{
				"page": 1,
				"page_size": 3,
				"total_count": 8,
				"total_pages": 3,
				"has_next": True,
				"has_prev": False,
			},
		)
		pages = [first] + [self._search(search=self.tag, page_size=3, page=n) for n in (2, 3)]
		self.assertEqual([len(p["items"]) for p in pages], [3, 3, 2])
		self.assertFalse(pages[2]["pagination"]["has_next"])
		ids = [item["id"] for p in pages for item in p["items"]]
		self.assertEqual(len(set(ids)), 8)
		self.assertEqual(ids, self._ids(search=self.tag, page_size=50))

	def test_a_page_past_the_end_is_empty(self):
		data = self._search(search=self.tag, page=9)
		self.assertEqual(data["items"], [])
		self.assertEqual(data["pagination"]["total_count"], 8)

	# --- invalid input ----------------------------------------------------------------------

	def test_invalid_input_is_a_validation_error(self):
		for params in (
			{"search": "a"},
			{"search": "x" * 101},
			{"provider_type": "Pawn Shop"},
			{"page": 0},
			{"page_size": 0},
			{"page_size": 51},
			{"unknown_filter": "x"},
		):
			with self.subTest(params=params):
				result = self._error(list_service_providers, **params)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)

	def test_an_unknown_type_is_refused_by_name(self):
		result = self._error(list_service_providers, provider_type="Pawn Shop")
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
		self.assertIn("Pawn Shop", result["message"])

	def test_a_type_added_later_works_as_a_filter_with_no_code_change(self):
		# Its own name, not the class tag: a provider matching the tag would change the counts in
		# every other test, since the class's data is rolled back only once.
		own = frappe.generate_hash(length=8)
		kind = frappe.get_doc(
			{"doctype": "Grievance Service Provider Type", "type_name": f"Agro {own}"}
		).insert()
		dealer = a_service_provider(f"{own} Dealer", provider_type=kind.name)

		self.assertEqual(self._ids(search=own, provider_type=kind.name), [dealer])

	def test_the_largest_page_is_fifty(self):
		self.assertEqual(self._search(search=self.tag, page_size=50)["pagination"]["page_size"], 50)

	# --- single lookup ----------------------------------------------------------------------

	def test_lookup_returns_an_inactive_provider_with_its_status(self):
		result = get_service_provider(self.closed)
		self.assertEqual(result["status"], "success", msg=result)
		self.assertEqual(result["data"]["id"], self.closed)
		self.assertEqual(result["data"]["name"], f"{self.tag} Closed Store")
		self.assertIs(result["data"]["is_active"], False)
		self.assertEqual(result["data"]["area"]["id"], self.woreda)

	def test_lookup_returns_an_active_provider_with_its_status(self):
		data = get_service_provider(self.branch)["data"]
		self.assertIs(data["is_active"], True)
		self.assertEqual(data["parent"]["id"], self.national)

	def test_lookup_of_an_unknown_provider_is_not_found(self):
		result = self._error(get_service_provider, "SP-99999")
		self.assertEqual(result["code"], "NOT_FOUND", msg=result)

	# --- access -----------------------------------------------------------------------------

	def test_a_user_who_can_file_a_grievance_can_search(self):
		frappe.set_user(self.submitter)
		self.assertIn(self.national, self._ids(search=self.tag))
		self.assertEqual(get_service_provider(self.national)["status"], "success")

	def test_every_role_that_may_read_a_provider_may_search_and_look_one_up(self):
		"""The guard checks the resource (read on the provider), not the right to file a grievance."""
		self.assertEqual(
			set(self.readers),
			{
				"Grievance Admin",
				"System Manager",
				"Grievance Officer",
				"Grievance Submitter",
				"Grievance Assisted Submitter",
				"Grievance Review Officer",
			},
		)
		for role, user in self.readers.items():
			with self.subTest(role=role):
				frappe.set_user(user)
				self.assertIn(self.national, self._ids(search=self.tag))
				self.assertEqual(get_service_provider(self.national)["status"], "success")

	def test_officers_and_reviewers_are_not_locked_out_for_lacking_create_on_grievance(self):
		for role in ("Grievance Officer", "Grievance Review Officer"):
			with self.subTest(role=role):
				frappe.set_user("Administrator")
				self.assertFalse(frappe.has_permission("Grievance", "create", user=self.readers[role]))
				frappe.set_user(self.readers[role])
				self.assertEqual(self._search(search=self.tag)["pagination"]["total_count"], 8)

	def test_a_user_with_no_role_on_providers_is_refused(self):
		frappe.set_user(self.outsider)
		for result in (
			self._error(list_service_providers, search=self.tag),
			self._error(get_service_provider, self.national),
		):
			self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)

	def test_a_guest_is_stopped_by_the_jwt_middleware(self):
		"""The 401 comes from the middleware, which only lets exempt paths through unsigned."""
		ensure_routes_registered()
		frappe.set_user("Guest")
		frappe.local.session = frappe._dict({"user": "Guest"})
		for path in ("/api/v1/service-providers", f"/api/v1/service-providers/{self.national}"):
			with self.subTest(path=path), configured_keys():
				with self.assertRaises(frappe.AuthenticationError):
					validate_jwt_request(make_test_request(path))

	def test_a_guest_who_reaches_the_handler_anyway_is_refused(self):
		"""Defence in depth: the endpoint guard does not rely on the middleware."""
		frappe.set_user("Guest")
		for result in (
			self._error(list_service_providers, search=self.tag),
			self._error(get_service_provider, self.national),
		):
			self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)
