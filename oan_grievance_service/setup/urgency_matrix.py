"""Sensitive and urgent configuration of the Routing Matrix D2 (STG-514), as data.

Read from `OAN_Grievance_Routing_Matrix_D2.xlsx`: the Sensitive Flag, Urgent Flag and Urgent
Season columns of sheet "Grievance Routing", and the "Seasonal Calendar" of sheet
"Configuration related". The same values are seeded for a new site from `openg2p-data`
(`oan-seed/grievance`); `patches/apply_sensitive_urgent_matrix.py` applies them to a site that
already holds the types and SLA configurations.

What the matrix says, and where it stops:

- Sensitive is Yes on exactly C13-01..04, C15-01..03 and C16-01. C16-02 (wrong personal data)
  is No. No C04 sub-type is flagged: "C04 if coercion" is free text in the Confidential path,
  not a flag, so it is an open point (a coercion case is filed as C13-03).
- Urgent is Yes on every sub-type of C01, C02, C07 and C17, so each is one rule for the whole
  category on its SLA configuration. C09 is not Urgent: its rule only raises priority to High.
- The windows are the Gregorian ones of the Seasonal Calendar, as recurring month and day.
  Whole months run from the first to the last day of the month.
- The matrix gives no in-season turnaround, so no rule sets `sla_days`: the category's own SLA
  days apply until the owners set one on the rule.
- C01's Belg window (February to April) is for Belg woredas only, and no official list of them
  exists, so it is not seeded: it is a rule with that window for each woreda's area. The same
  holds for a woreda's own C02 or C07 window.
"""

from oan_grievance_service.services import constants as C

SENSITIVE_TYPE_CODES = (
	"C13-01",
	"C13-02",
	"C13-03",
	"C13-04",
	"C15-01",
	"C15-02",
	"C15-03",
	"C16-01",
)


def _rule(label, start=None, end=None, *, urgent=False, priority=None):
	"""A rule for the whole category; `start` and `end` are (month, day), None for all year."""
	return {
		"rule_label": label,
		"start_month": start[0] if start else None,
		"start_day": start[1] if start else None,
		"end_month": end[0] if end else None,
		"end_day": end[1] if end else None,
		"is_urgent": 1 if urgent else 0,
		"priority": priority,
	}


# category code -> its rules
SLA_RULES = {
	"C01": (_rule("Meher input season", (5, 15), (8, 31), urgent=True),),
	"C02": (_rule("Irrigation season (dry season)", (10, 1), (4, 30), urgent=True),),
	"C07": (_rule("Dry / irrigation season", (10, 1), (4, 30), urgent=True),),
	"C09": (_rule("Harvest marketing peak", (10, 1), (12, 31), priority=C.PRIORITY_HIGH),),
	"C17": (_rule("Pest and disease outbreak (all year)", urgent=True),),
}

URGENT_CATEGORY_CODES = tuple(
	code for code, rules in SLA_RULES.items() if any(rule["is_urgent"] for rule in rules)
)
