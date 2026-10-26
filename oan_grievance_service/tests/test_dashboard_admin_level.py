# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""STG-471: grvByAdminLevel, grievance counts at Federal, Region and Woreda level.

The tests build their own Woredas and Kebeles under one existing Region and put the cases in
departments made for the class, so a count can be asserted exactly on a site that already
holds grievances. Federal and Region counts are asserted as a change against a baseline.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1 import charts as charts_api
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import dashboard, dashboard_rollup
from oan_grievance_service.tests.fixtures import (
	a_grievance,
	a_leaf_area,
	a_service_category,
	an_administrative_area,
	discard_grievance,
)
from oan_grievance_service.tests.test_dashboard_scope import _department, _officer

CHART = "grvByAdminLevel"


def _area(name, field):
	return frappe.db.get_value("Grievance Administrative Area", name, field)


def _rows(params=None, admin=True, scope=None):
	"""Build straight from the registry, past the cache."""
	ctx = dashboard.Context(params or dashboard.Params(), admin, scope)
	return dashboard.CHARTS[CHART].build(ctx)


def _by_level(rows):
	result = {}
	for row in rows:
		result.setdefault(row["level"], {})[row["code"]] = row
	return result


class AdminLevelFixture(FrappeTestCase):
	"""Region R holds Woreda W1 (Kebele K1) and Woreda W2 (Kebele K2).

	Department D1 has two cases in W1 and one in W2; D2 has one in W1.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		if not frappe.db.exists("Grievance Role Level", "nodal_officer"):
			frappe.get_doc(
				{
					"doctype": "Grievance Role Level",
					"level_name": "Nodal Officer",
					"level_code": "nodal_officer",
					"level_order": 1,
				}
			).insert(ignore_permissions=True)

		leaf = a_leaf_area()
		lft = _area(leaf, "lft")
		cls.region = frappe.db.get_value(
			"Grievance Administrative Area",
			{"level_name": "Region", "lft": ["<=", lft], "rgt": [">=", lft]},
			"name",
		)
		cls.region_code = _area(cls.region, "code")
		cls.country = frappe.db.get_value(
			"Grievance Administrative Area", {"level_name": "Country"}, "name", order_by="lft asc"
		)

		cls.w1 = an_administrative_area("Level Woreda One", cls.region, level_name="Woreda")
		cls.w2 = an_administrative_area("Level Woreda Two", cls.region, level_name="Woreda")
		cls.k1 = an_administrative_area("Level Kebele One", cls.w1, level_name="Kebele")
		cls.k2 = an_administrative_area("Level Kebele Two", cls.w2, level_name="Kebele")
		cls.w1_code, cls.w2_code = _area(cls.w1, "code"), _area(cls.w2, "code")

		cls.category = a_service_category()
		cls.d1, cls.d2 = _department("Level One"), _department("Level Two")

		cls.baseline = cls._counts()
		cls.created = [
			a_grievance(administrative_area=area, assigned_dept=dept).name
			for area, dept in (
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
		dashboard_rollup.refresh(full=True)
		super().tearDownClass()

	@classmethod
	def _counts(cls):
		levels = _by_level(_rows())
		return {
			"federal": levels["federal"][next(iter(levels["federal"]))]["count"],
			"region": levels.get("region", {}).get(cls.region_code, {}).get("count", 0),
		}

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.local.response = frappe._dict()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _desk(self, user, area=None, department=None):
		self._desk_of(department, (user, area))

	def _desk_of(self, department, *officers):
		"""One desk for `department`, held by each (user, area) pair. A department and category
		take one desk, so officers who share a department share it."""
		doc = frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": department,
				"category_scope": self.category,
				"active": 1,
				"effective_from": frappe.utils.today(),
				"officers": [
					{
						"user": user,
						"role_level": "nodal_officer",
						"administrative_area": area,
						"is_primary": 0,
						"active": 1,
					}
					for user, area in officers
				],
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "Grievance RBAC Assignment", doc.name, force=True, ignore_permissions=True
		)

	def _as_officer(self, user, **params):
		frappe.set_user(user)
		res = charts_api.get_charts(charts=CHART, **params)
		self.assertEqual(res["status"], "success", res)
		return res["data"][CHART]


class TestAdminLevelAggregation(AdminLevelFixture):
	def test_woredas_are_counted_where_the_case_was_filed(self):
		woredas = _by_level(_rows(dashboard.Params(assigned_dept=(self.d1,))))["woreda"]
		self.assertEqual(woredas[self.w1_code]["count"], 2)
		self.assertEqual(woredas[self.w2_code]["count"], 1)

	def test_a_case_in_a_kebele_counts_for_its_woreda_not_the_next(self):
		woredas = _by_level(_rows())["woreda"]
		self.assertEqual(woredas[self.w1_code]["count"], 3)
		self.assertEqual(woredas[self.w2_code]["count"], 1)

	def test_a_region_row_is_the_sum_of_its_woredas(self):
		levels = _by_level(_rows())
		self.assertEqual(
			levels["region"][self.region_code]["count"] - self.baseline["region"],
			levels["woreda"][self.w1_code]["count"] + levels["woreda"][self.w2_code]["count"],
		)

	def test_the_federal_row_counts_every_case(self):
		federal = next(iter(_by_level(_rows())["federal"].values()))
		self.assertEqual(federal["count"] - self.baseline["federal"], 4)
		self.assertIsNone(federal["region_code"])

	def test_the_federal_row_is_the_sum_of_the_region_rows_and_what_is_under_none(self):
		rows = _rows()
		levels = _by_level(rows)
		federal = next(iter(levels["federal"].values()))["count"]
		in_regions = sum(r["count"] for r in levels["region"].values())
		self.assertGreaterEqual(federal, in_regions)

	def test_rows_carry_names_and_their_region(self):
		woreda = _by_level(_rows())["woreda"][self.w1_code]
		self.assertEqual(woreda["name"], "Level Woreda One")
		self.assertEqual(woreda["region_code"], self.region_code)
		region = _by_level(_rows())["region"][self.region_code]
		self.assertEqual(region["name"], _area(self.region, "area_name"))

	def test_the_levels_come_widest_first(self):
		order = [r["level"] for r in _rows()]
		self.assertEqual(order, sorted(order, key=dashboard.LEVELS.index))

	def test_a_region_or_woreda_without_a_case_is_left_out(self):
		rows = _rows(dashboard.Params(assigned_dept=(self.d2,)))
		woredas = _by_level(rows)["woreda"]
		self.assertEqual(set(woredas), {self.w1_code})

	def test_the_level_filter_picks_levels(self):
		rows = _rows(dashboard.Params(level=("woreda",)))
		self.assertEqual({r["level"] for r in rows}, {"woreda"})
		rows = _rows(dashboard.Params(level=("federal", "region")))
		self.assertEqual({r["level"] for r in rows}, {"federal", "region"})

	def test_the_region_filter_narrows_every_level(self):
		rows = _rows(dashboard.Params(region=(self.region_code,)))
		levels = _by_level(rows)
		self.assertEqual(set(levels["region"]), {self.region_code})
		federal = next(iter(levels["federal"].values()))["count"]
		self.assertEqual(federal, levels["region"][self.region_code]["count"])

	def test_the_department_filter_narrows_the_counts(self):
		federal = next(iter(_by_level(_rows(dashboard.Params(assigned_dept=(self.d2,))))["federal"].values()))
		self.assertEqual(federal["count"], 1)

	def test_the_snapshot_records_the_woreda(self):
		S = frappe.qb.DocType(dashboard_rollup.SNAPSHOT)
		keys = (
			frappe.qb.from_(S)
			.select(S.woreda)
			.where(S.snapshot_date == frappe.utils.getdate())
			.where(S.assigned_dept == self.d1)
			.run(pluck=True)
		)
		self.assertEqual(set(keys), {self.w1_code, self.w2_code})

	def test_a_woreda_without_a_code_is_keyed_by_its_name(self):
		woreda = an_administrative_area("Level No Code", self.region, level_name="Woreda", code=None)
		kebele = an_administrative_area("Level No Code Kebele", woreda, level_name="Kebele")
		department = _department("Level NoCode")
		case = a_grievance(administrative_area=kebele, assigned_dept=department).name
		# Cleanups run last in, first out: the rollup is rebuilt after the case is gone.
		self.addCleanup(dashboard_rollup.refresh)
		self.addCleanup(discard_grievance, case)
		dashboard_rollup.refresh()
		woredas = _by_level(_rows(dashboard.Params(assigned_dept=(department,))))["woreda"]
		self.assertEqual(woredas[woreda]["count"], 1)
		self.assertEqual(woredas[woreda]["name"], "Level No Code")

	def test_the_endpoint_returns_the_chart_and_echoes_the_level(self):
		res = charts_api.get_charts(charts=CHART, level="Region, woreda")
		self.assertEqual(res["status"], "success", res)
		self.assertEqual({r["level"] for r in res["data"][CHART]}, {"region", "woreda"})
		self.assertEqual(res["meta"]["filters"]["level"], ["region", "woreda"])

	def test_an_unknown_level_is_a_400_naming_the_field(self):
		res = charts_api.get_charts(charts=CHART, level="district")
		self.assertEqual(res["code"], "VALIDATION_ERROR")
		self.assertIn("level", res["details"])
		self.assertEqual(frappe.local.response.get("http_status_code"), 400)

	def test_the_chart_is_public_and_counts_only(self):
		self.assertIn(CHART, dashboard.PUBLIC_CHARTS)
		rows = dashboard.get_chart(CHART, dashboard.Params(), admin=False)["rows"]
		self.assertEqual({r["level"] for r in rows}, set(dashboard.LEVELS))
		for row in rows:
			self.assertEqual(set(row), {"level", "code", "name", "region_code", "count"})

	def test_the_public_route_serves_it(self):
		res = charts_api._public_chart(CHART, {"level": "federal"})
		self.assertEqual([r["level"] for r in res["data"]], ["federal"])


class TestAdminLevelScope(AdminLevelFixture):
	def test_an_unrestricted_admin_reads_every_level(self):
		self.assertEqual({r["level"] for r in self._as_officer("Administrator")}, set(dashboard.LEVELS))

	def test_a_desk_without_an_area_reads_every_level_over_its_cases(self):
		officer = _officer("lvl-all")
		self._desk(officer, department=self.d1)
		levels = _by_level(self._as_officer(officer))
		self.assertEqual(set(levels), set(dashboard.LEVELS))
		self.assertEqual(next(iter(levels["federal"].values()))["count"], 3)
		self.assertEqual(levels["region"][self.region_code]["count"], 3)
		self.assertEqual(levels["woreda"][self.w1_code]["count"], 2)

	def test_a_country_desk_reads_the_federal_level(self):
		if not self.country:
			self.skipTest("the site has no Country area")
		officer = _officer("lvl-country")
		self._desk(officer, area=self.country, department=self.d1)
		self.assertIn("federal", _by_level(self._as_officer(officer)))

	def test_a_region_desk_reads_its_region_and_woredas_but_not_federal(self):
		officer = _officer("lvl-region")
		self._desk(officer, area=self.region, department=self.d1)
		levels = _by_level(self._as_officer(officer))
		self.assertEqual(set(levels), {"region", "woreda"})
		self.assertEqual(levels["region"][self.region_code]["count"], 3)
		self.assertEqual(set(levels["woreda"]), {self.w1_code, self.w2_code})

	def test_a_region_desk_cannot_ask_for_the_federal_level(self):
		officer = _officer("lvl-region-fed")
		self._desk(officer, area=self.region, department=self.d1)
		self.assertEqual(self._as_officer(officer, level="federal"), [])

	def test_a_woreda_desk_reads_its_own_woreda_only(self):
		officer = _officer("lvl-woreda")
		self._desk(officer, area=self.w1, department=self.d1)
		rows = self._as_officer(officer)
		self.assertEqual([(r["level"], r["code"], r["count"]) for r in rows], [("woreda", self.w1_code, 2)])

	def test_a_woreda_desk_does_not_reach_a_sibling_woreda(self):
		officer = _officer("lvl-sibling")
		self._desk(officer, area=self.w2, department=self.d1)
		rows = self._as_officer(officer)
		self.assertEqual([(r["code"], r["count"]) for r in rows], [(self.w2_code, 1)])

	def test_a_woreda_desk_is_limited_to_its_department(self):
		officer = _officer("lvl-woreda-dept")
		self._desk(officer, area=self.w1, department=self.d2)
		rows = self._as_officer(officer)
		self.assertEqual([(r["code"], r["count"]) for r in rows], [(self.w1_code, 1)])

	def test_a_desk_below_woreda_level_reads_nothing(self):
		officer = _officer("lvl-kebele")
		self._desk(officer, area=self.k1, department=self.d1)
		self.assertEqual(self._as_officer(officer), [])

	def test_an_officer_on_no_desk_reads_nothing(self):
		self.assertEqual(self._as_officer(_officer("lvl-none")), [])

	def test_desks_are_alternatives_across_levels(self):
		officer = _officer("lvl-two")
		self._desk(officer, area=self.w1, department=self.d2)
		self._desk(officer, area=self.w2, department=self.d1)
		levels = _by_level(self._as_officer(officer))
		self.assertEqual(set(levels), {"woreda"})
		self.assertEqual(
			{code: row["count"] for code, row in levels["woreda"].items()},
			{self.w1_code: 1, self.w2_code: 1},
		)

	def test_officers_do_not_share_cached_levels(self):
		a, b = _officer("lvl-cache-a"), _officer("lvl-cache-b")
		self._desk_of(self.d1, (a, self.w1), (b, self.w2))
		self.assertEqual({r["code"] for r in self._as_officer(a)}, {self.w1_code})
		self.assertEqual({r["code"] for r in self._as_officer(b)}, {self.w2_code})
		self.assertEqual({r["code"] for r in self._as_officer(a)}, {self.w1_code})

	def test_the_officer_view_carries_no_case_detail(self):
		officer = _officer("lvl-detail")
		self._desk(officer, department=self.d1)
		for row in self._as_officer(officer):
			self.assertFalse({"ticket_number", "title", "submitter_display"} & set(row))

	def test_a_woreda_desk_does_not_widen_the_other_charts(self):
		"""The daily rollup has no Woreda column: other charts keep reading such a desk as nothing."""
		officer = _officer("lvl-compat")
		self._desk(officer, area=self.w1, department=self.d1)
		frappe.set_user(officer)
		res = charts_api.get_charts(charts="grvKpis,grvMonthlyTrend")
		self.assertEqual(res["status"], "success", res)
		self.assertEqual(res["meta"]["errors"], {})
		total = next(r["value"] for r in res["data"]["grvKpis"] if r["metric"] == "total")
		self.assertEqual(total, 0)

	def test_caller_scope_names_the_level_of_each_desk(self):
		desks = (
			(_officer("sc-none"), None, "federal"),
			(_officer("sc-region"), self.region, "region"),
			(_officer("sc-woreda"), self.w1, "woreda"),
		)
		self._desk_of(self.d1, *((user, area) for user, area, _ in desks))
		for user, _, level in desks:
			(clause,) = dashboard.caller_scope(user)
			self.assertEqual(clause.level, level, user)
		(clause,) = dashboard.caller_scope(desks[2][0])
		self.assertEqual(clause.woredas, (self.w1_code,))
		self.assertIsNone(clause.regions)
