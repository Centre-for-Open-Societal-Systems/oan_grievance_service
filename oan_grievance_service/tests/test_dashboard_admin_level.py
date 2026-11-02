# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""grvByAdminLevel: grievance counts at Federal, Region and Woreda level.

Each class builds its own Country, Region, Woredas and Kebeles, and its own departments, and
deletes them again, so a count over those departments is exact whatever else the site holds.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1 import charts as charts_api
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import dashboard, dashboard_rollup
from oan_grievance_service.tests.fixtures import (
	a_desk,
	a_free_ticket_code,
	a_grievance,
	a_role_level,
	a_service_category,
	a_unique_department,
	a_user_with_role,
	an_administrative_area,
	discard_grievance,
)

CHART = "grvByAdminLevel"
ROW_KEYS = {"level", "code", "name", "region_code", "count"}


def _rows(params):
	"""Build straight from the registry, past the cache."""
	return dashboard.CHARTS[CHART].build(dashboard.Context(params, admin=True))


def _by_level(rows):
	result = {}
	for row in rows:
		result.setdefault(row["level"], {})[row["code"]] = row
	return result


def _federal(rows):
	(row,) = (r for r in rows if r["level"] == "federal")
	return row


def _area_name(area):
	return frappe.db.get_value("Grievance Administrative Area", area, "area_name")


class AdminLevelFixture(FrappeTestCase):
	"""Country > Region R > Woreda W1 (Kebele K1) and Woreda W2 (Kebele K2).

	Department D1 has two cases in W1 and one in W2; D2 has one in W1.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		suffix = frappe.generate_hash(length=6)
		cls.areas = []

		def area(name, parent, level_name, **overrides):
			created = an_administrative_area(f"{name} {suffix}", parent, level_name, **overrides)
			cls.areas.append(created)
			return created

		cls.country = area("Level Country", None, C.AREA_LEVEL_COUNTRY)
		cls.region = area("Level Region", cls.country, C.AREA_LEVEL_REGION, ticket_code=a_free_ticket_code())
		cls.w1 = area("Level Woreda One", cls.region, C.AREA_LEVEL_WOREDA)
		cls.w2 = area("Level Woreda Two", cls.region, C.AREA_LEVEL_WOREDA)
		cls.k1 = area("Level Kebele One", cls.w1, "Kebele")
		cls.k2 = area("Level Kebele Two", cls.w2, "Kebele")
		cls.region_code = cls._code(cls.region)
		cls.w1_code, cls.w2_code = cls._code(cls.w1), cls._code(cls.w2)

		cls.category = a_service_category()
		cls.role_level = a_role_level()
		cls.d1, cls.d2, cls.d3 = (a_unique_department(label) for label in ("One", "Two", "Three"))
		cls.mine = (cls.d1, cls.d2)
		# Users are throttled (Frappe allows 60 a hour), so a few are shared by the class's tests.
		cls.officers = [a_user_with_role(C.ROLE_OFFICER, f"officer{i}") for i in range(3)]
		cls.reader = a_user_with_role(C.ROLE_DASHBOARD_READER, "reader")
		cls.users = [*cls.officers, cls.reader]

		cls.created = [
			a_grievance(administrative_area=where, assigned_dept=dept).name
			for where, dept in (
				(cls.k1, cls.d1),
				(cls.k1, cls.d1),
				(cls.k2, cls.d1),
				(cls.k1, cls.d2),
			)
		]
		dashboard_rollup.refresh()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for name in cls.created:
			discard_grievance(name)
		for doctype, names in (
			("User", cls.users),
			("Grievance Administrative Area", reversed(cls.areas)),
			("Grievance Department", (cls.d1, cls.d2, cls.d3)),
			("Grievance Role Level", (cls.role_level,)),
		):
			for name in names:
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		dashboard_rollup.refresh()
		super().tearDownClass()

	@staticmethod
	def _code(area):
		return frappe.db.get_value("Grievance Administrative Area", area, "code")

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.local.response = frappe._dict()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _desk_of(self, department, *officers):
		"""One desk for `department`, held by each (user, area) pair. A department and category
		take one desk, so officers who share a department share it."""
		name = a_desk(department, self.category, self.role_level, *officers)
		self.addCleanup(
			frappe.delete_doc, "Grievance RBAC Assignment", name, force=True, ignore_permissions=True
		)

	def _desk(self, user, area=None, department=None):
		self._desk_of(department, (user, area))

	def _as_officer(self, user, **params):
		frappe.set_user(user)
		res = charts_api.get_charts(charts=CHART, **params)
		self.assertEqual(res["status"], "success", res)
		return res["data"][CHART]


class TestAdminLevelAggregation(AdminLevelFixture):
	def _levels(self, **params):
		params.setdefault("assigned_dept", self.mine)
		return _by_level(_rows(dashboard.Params(**params)))

	def test_woreda_rows_count_the_cases_filed_under_them(self):
		woredas = self._levels(assigned_dept=(self.d1,))["woreda"]
		self.assertEqual(woredas[self.w1_code]["count"], 2)
		self.assertEqual(woredas[self.w2_code]["count"], 1)

	def test_a_case_in_a_kebele_counts_for_its_woreda_not_the_next(self):
		woredas = self._levels()["woreda"]
		self.assertEqual(woredas[self.w1_code]["count"], 3)
		self.assertEqual(woredas[self.w2_code]["count"], 1)

	def test_a_region_row_is_the_sum_of_its_woredas(self):
		levels = self._levels()
		self.assertEqual(
			levels["region"][self.region_code]["count"],
			levels["woreda"][self.w1_code]["count"] + levels["woreda"][self.w2_code]["count"],
		)

	def test_the_federal_row_counts_every_case(self):
		federal = _federal(_rows(dashboard.Params(assigned_dept=self.mine)))
		self.assertEqual(federal["count"], 4)
		self.assertIsNone(federal["region_code"])

	def test_the_federal_row_is_the_region_rows_plus_the_cases_under_no_region(self):
		"""A case needs a region to be filed, so seed the rollup with one that has none."""
		dashboard_rollup._insert(
			dashboard_rollup.AREA,
			dashboard_rollup.AREA_FIELDS,
			[
				(self.region_code, self.w1_code, self.category, self.d3, 2),
				("", "", self.category, self.d3, 5),
			],
		)
		self.addCleanup(frappe.db.delete, dashboard_rollup.AREA, {"assigned_dept": self.d3})
		levels = self._levels(assigned_dept=(self.d3,))
		in_regions = sum(r["count"] for r in levels["region"].values())
		self.assertEqual(in_regions, 2)
		self.assertEqual(_federal(_rows(dashboard.Params(assigned_dept=(self.d3,))))["count"], in_regions + 5)

	def test_rows_carry_names_and_their_region(self):
		levels = self._levels()
		woreda = levels["woreda"][self.w1_code]
		self.assertEqual(woreda["name"], _area_name(self.w1))
		self.assertEqual(woreda["region_code"], self.region_code)
		region = levels["region"][self.region_code]
		self.assertEqual(region["name"], _area_name(self.region))
		self.assertEqual(region["region_code"], self.region_code)

	def test_the_levels_come_widest_first_then_by_count(self):
		rows = _rows(dashboard.Params(assigned_dept=self.mine))
		self.assertEqual([r["level"] for r in rows], ["federal", "region", "woreda", "woreda"])
		self.assertEqual([r["count"] for r in rows if r["level"] == "woreda"], [3, 1])

	def test_a_region_or_woreda_without_a_case_is_left_out(self):
		woredas = self._levels(assigned_dept=(self.d2,))["woreda"]
		self.assertEqual(set(woredas), {self.w1_code})

	def test_the_level_filter_picks_levels(self):
		rows = _rows(dashboard.Params(assigned_dept=self.mine, level=("woreda",)))
		self.assertEqual({r["level"] for r in rows}, {"woreda"})
		rows = _rows(dashboard.Params(assigned_dept=self.mine, level=("federal", "region")))
		self.assertEqual({r["level"] for r in rows}, {"federal", "region"})

	def test_the_region_filter_narrows_every_level(self):
		levels = self._levels(region=(self.region_code,))
		self.assertEqual(set(levels["region"]), {self.region_code})
		self.assertEqual(
			_federal(_rows(dashboard.Params(assigned_dept=self.mine, region=(self.region_code,))))["count"], 4
		)
		other = _rows(dashboard.Params(assigned_dept=self.mine, region=("NOSUCHREGION",)))
		self.assertEqual([(r["level"], r["count"]) for r in other], [("federal", 0)])

	def test_the_department_filter_narrows_the_counts(self):
		self.assertEqual(_federal(_rows(dashboard.Params(assigned_dept=(self.d2,))))["count"], 1)

	def test_the_rollup_records_the_woreda_of_each_case(self):
		A = frappe.qb.DocType(dashboard_rollup.AREA)
		woredas = frappe.qb.from_(A).select(A.woreda).where(A.assigned_dept == self.d1).run(pluck=True)
		self.assertEqual(set(woredas), {self.w1_code, self.w2_code})

	def test_a_woreda_without_a_code_is_keyed_by_its_name(self):
		area_name = f"Level No Code {frappe.generate_hash(length=6)}"
		woreda = an_administrative_area(area_name, self.region, C.AREA_LEVEL_WOREDA, code=None)
		kebele = an_administrative_area(f"{area_name} Kebele", woreda, "Kebele")
		department = a_unique_department("NoCode")
		case = a_grievance(administrative_area=kebele, assigned_dept=department).name
		# Cleanups run last in, first out: the case goes first, the rollup is rebuilt last.
		self.addCleanup(dashboard_rollup.refresh)
		self.addCleanup(frappe.delete_doc, "Grievance Department", department, force=True)
		self.addCleanup(frappe.delete_doc, "Grievance Administrative Area", woreda, force=True)
		self.addCleanup(frappe.delete_doc, "Grievance Administrative Area", kebele, force=True)
		self.addCleanup(discard_grievance, case)
		dashboard_rollup.refresh()
		row = self._levels(assigned_dept=(department,))["woreda"][woreda]
		self.assertEqual(row["count"], 1)
		self.assertEqual(row["name"], area_name)

	def test_the_endpoint_returns_the_chart_and_echoes_the_level(self):
		res = charts_api.get_charts(charts=CHART, level="Region, woreda", assigned_dept=",".join(self.mine))
		self.assertEqual(res["status"], "success", res)
		self.assertEqual({r["level"] for r in res["data"][CHART]}, {"region", "woreda"})
		self.assertEqual(res["meta"]["filters"]["level"], ["region", "woreda"])

	def test_an_empty_level_means_every_level(self):
		res = charts_api.get_charts(charts=CHART, level="")
		self.assertEqual(res["status"], "success", res)
		self.assertEqual({r["level"] for r in res["data"][CHART]}, set(dashboard.LEVELS))

	def test_the_chart_is_public_and_counts_only(self):
		self.assertIn(CHART, dashboard.PUBLIC_CHARTS)
		rows = dashboard.get_chart(CHART, dashboard.Params(), admin=False)["rows"]
		self.assertEqual({r["level"] for r in rows}, set(dashboard.LEVELS))
		for row in rows:
			self.assertEqual(set(row), ROW_KEYS)


class TestAdminLevelAccess(AdminLevelFixture):
	"""What the Dashboard Reader and an admin read."""

	def test_the_dashboard_reader_reads_every_level_on_the_public_route(self):
		frappe.set_user(self.reader)
		res = charts_api._public_endpoint(CHART)()
		self.assertEqual({r["level"] for r in res["data"]}, set(dashboard.LEVELS))
		self.assertGreaterEqual(_federal(res["data"])["count"], 4)
		woredas = {r["code"] for r in res["data"] if r["level"] == "woreda"}
		self.assertTrue({self.w1_code, self.w2_code} <= woredas)

	def test_the_public_route_ignores_the_admin_only_department_filter(self):
		frappe.set_user(self.reader)
		public = charts_api._public_endpoint(CHART)
		self.assertEqual(public(assigned_dept=self.d1)["data"], public()["data"])
		self.assertNotIn("assigned_dept", public()["meta"]["filters"])

	def test_an_admin_reads_every_level_on_the_jwt_route(self):
		rows = self._as_officer("Administrator", assigned_dept=",".join(self.mine))
		self.assertEqual({r["level"] for r in rows}, set(dashboard.LEVELS))


class TestAdminLevelErrors(FrappeTestCase):
	"""Error paths. handle_api_errors rolls the transaction back on an error, which would take
	the rows another class builds in setUpClass with it, so these have no fixtures of their own."""

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.local.response = frappe._dict()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _user(self, role, label):
		frappe.set_user("Administrator")
		user = a_user_with_role(role, label)
		self.addCleanup(frappe.delete_doc, "User", user, force=True, ignore_permissions=True)
		return user

	def test_an_unknown_level_is_a_400_naming_the_field(self):
		res = charts_api.get_charts(charts=CHART, level="district")
		self.assertEqual(res["code"], "VALIDATION_ERROR")
		self.assertIn("level", res["details"])
		self.assertEqual(frappe.local.response.get("http_status_code"), 400)

	def test_an_over_long_level_is_a_400(self):
		for level in ("federal," * 20, ["federal", "region", "woreda", "federal"], "x" * 50):
			frappe.local.response = frappe._dict()
			res = charts_api.get_charts(charts=CHART, level=level)
			self.assertEqual(res["code"], "VALIDATION_ERROR", level)
			self.assertEqual(frappe.local.response.get("http_status_code"), 400, level)

	def test_a_submitter_is_refused_on_the_jwt_route(self):
		frappe.set_user(self._user(C.ROLE_SUBMITTER, "submitter"))
		res = charts_api.get_charts(charts=CHART)
		self.assertEqual(res["code"], "PERMISSION_DENIED")
		self.assertEqual(frappe.local.response.get("http_status_code"), 403)

	def test_the_dashboard_reader_is_refused_on_the_jwt_route(self):
		frappe.set_user(self._user(C.ROLE_DASHBOARD_READER, "reader"))
		res = charts_api.get_charts(charts=CHART)
		self.assertEqual(res["code"], "PERMISSION_DENIED")
		self.assertEqual(frappe.local.response.get("http_status_code"), 403)

	def test_a_guest_is_refused_on_both_routes(self):
		frappe.set_user("Guest")
		self.assertEqual(charts_api.get_charts(charts=CHART)["code"], "PERMISSION_DENIED")
		frappe.local.response = frappe._dict()
		self.assertEqual(charts_api._public_endpoint(CHART)()["code"], "PERMISSION_DENIED")

	def test_only_the_reader_and_admins_may_call_the_public_route(self):
		public = charts_api._public_endpoint(CHART)
		for role in (C.ROLE_OFFICER, C.ROLE_SUBMITTER):
			frappe.local.response = frappe._dict()
			frappe.set_user(self._user(role, "denied"))
			res = public()
			self.assertEqual(res["code"], "PERMISSION_DENIED", role)
			self.assertEqual(frappe.local.response.get("http_status_code"), 403, role)


class TestAdminLevelScope(AdminLevelFixture):
	"""What an officer reads: their own level and below, over the cases of their desks."""

	def test_a_desk_without_an_area_reads_every_level_over_its_cases(self):
		officer = self.officers[0]
		self._desk(officer, department=self.d1)
		levels = _by_level(self._as_officer(officer))
		self.assertEqual(set(levels), set(dashboard.LEVELS))
		self.assertEqual(next(iter(levels["federal"].values()))["count"], 3)
		self.assertEqual(levels["region"][self.region_code]["count"], 3)
		self.assertEqual(levels["woreda"][self.w1_code]["count"], 2)

	def test_a_country_desk_reads_the_federal_level(self):
		officer = self.officers[0]
		self._desk(officer, area=self.country, department=self.d1)
		levels = _by_level(self._as_officer(officer))
		self.assertEqual(set(levels), set(dashboard.LEVELS))
		self.assertEqual(next(iter(levels["federal"].values()))["count"], 3)

	def test_a_region_desk_reads_its_region_and_woredas_but_not_federal(self):
		officer = self.officers[0]
		self._desk(officer, area=self.region, department=self.d1)
		levels = _by_level(self._as_officer(officer))
		self.assertEqual(set(levels), {"region", "woreda"})
		self.assertEqual(levels["region"][self.region_code]["count"], 3)
		self.assertEqual(set(levels["woreda"]), {self.w1_code, self.w2_code})

	def test_a_region_desk_cannot_ask_for_the_federal_level(self):
		officer = self.officers[0]
		self._desk(officer, area=self.region, department=self.d1)
		self.assertEqual(self._as_officer(officer, level="federal"), [])

	def test_a_woreda_desk_reads_its_own_woreda_only(self):
		officer = self.officers[0]
		self._desk(officer, area=self.w1, department=self.d1)
		rows = self._as_officer(officer)
		self.assertEqual([(r["level"], r["code"], r["count"]) for r in rows], [("woreda", self.w1_code, 2)])

	def test_a_woreda_desk_does_not_reach_a_sibling_woreda(self):
		officer = self.officers[0]
		self._desk(officer, area=self.w2, department=self.d1)
		rows = self._as_officer(officer)
		self.assertEqual([(r["code"], r["count"]) for r in rows], [(self.w2_code, 1)])

	def test_a_woreda_desk_is_limited_to_its_department(self):
		officer = self.officers[0]
		self._desk(officer, area=self.w1, department=self.d2)
		rows = self._as_officer(officer)
		self.assertEqual([(r["code"], r["count"]) for r in rows], [(self.w1_code, 1)])

	def test_a_desk_below_woreda_level_reads_nothing(self):
		officer = self.officers[0]
		self._desk(officer, area=self.k1, department=self.d1)
		self.assertEqual(self._as_officer(officer), [])

	def test_an_officer_on_no_desk_reads_nothing(self):
		self.assertEqual(self._as_officer(self.officers[0]), [])

	def test_desks_are_alternatives_across_levels(self):
		officer = self.officers[0]
		self._desk(officer, area=self.w1, department=self.d2)
		self._desk(officer, area=self.w2, department=self.d1)
		levels = _by_level(self._as_officer(officer))
		self.assertEqual(set(levels), {"woreda"})
		self.assertEqual(
			{code: row["count"] for code, row in levels["woreda"].items()},
			{self.w1_code: 1, self.w2_code: 1},
		)

	def test_officers_do_not_share_cached_levels(self):
		a, b = self.officers[0], self.officers[1]
		self._desk_of(self.d1, (a, self.w1), (b, self.w2))
		self.assertEqual({r["code"] for r in self._as_officer(a)}, {self.w1_code})
		self.assertEqual({r["code"] for r in self._as_officer(b)}, {self.w2_code})
		self.assertEqual({r["code"] for r in self._as_officer(a)}, {self.w1_code})

	def test_the_officer_view_carries_no_case_detail(self):
		officer = self.officers[0]
		self._desk(officer, department=self.d1)
		for row in self._as_officer(officer):
			self.assertEqual(set(row), ROW_KEYS)

	def test_a_woreda_desk_does_not_widen_the_other_charts(self):
		"""The daily rollup has no Woreda column: other charts keep reading such a desk as nothing."""
		officer = self.officers[0]
		self._desk(officer, area=self.w1, department=self.d1)
		frappe.set_user(officer)
		res = charts_api.get_charts(charts="grvKpis,grvMonthlyTrend")
		self.assertEqual(res["status"], "success", res)
		self.assertEqual(res["meta"]["errors"], {})
		total = next(r["value"] for r in res["data"]["grvKpis"] if r["metric"] == "total")
		self.assertEqual(total, 0)

	def test_caller_scope_names_the_level_of_each_desk(self):
		desks = (
			(self.officers[0], None, "federal"),
			(self.officers[1], self.region, "region"),
			(self.officers[2], self.w1, "woreda"),
		)
		self._desk_of(self.d1, *((user, area) for user, area, _ in desks))
		for user, _, level in desks:
			(clause,) = dashboard.caller_scope(user)
			self.assertEqual(clause.level, level, user)
		(clause,) = dashboard.caller_scope(desks[2][0])
		self.assertEqual(clause.woredas, (self.w1_code,))
		self.assertIsNone(clause.regions)
