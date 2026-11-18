"""Sensitive and urgent configuration of the Routing Matrix D2 (STG-514), as data.

Read from `OAN_Grievance_Routing_Matrix_D2.xlsx`: the Sensitive Flag, Urgent Flag and Urgent
Season columns of sheet "Grievance Routing", and the "Seasonal Calendar" of sheet
"Configuration related". The same values are seeded for a new site from `openg2p-data`
(`oan-seed/grievance`); `patches/apply_sensitive_urgent_matrix.py` applies them to a site that
already holds the types.

What the matrix says, and where it stops:

- Sensitive is Yes on exactly C13-01..04, C15-01..03 and C16-01. C16-02 (wrong personal data)
  is No. No C04 sub-type is flagged: "C04 if coercion" is free text in the Confidential path,
  not a flag, so it is an open point (a coercion case is filed as C13-03).
- Urgent is Yes on C01-01..08, C02-01..05, C07-01..03 and C17-01..04. C09 is not Urgent: its
  season only raises priority.
- A window is whole months, so the day is the first and the last of the month. C01 is the only
  window with days, and its Gregorian and Ethiopian boundaries agree (15 May is Ginbot 7, 31
  August is Nehase 25). The others do not: 'Oct - Apr' starts about ten days before Tikimt.
  The Gregorian window decides; the Ethiopian one is recorded beside it.
- No official list of Belg woredas exists, so none is seeded: they are marked on the area.
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


def _window(applies_to, start, end, eth_start, eth_end):
	"""A window from (month, day) pairs; the Ethiopian ones are (month name, day)."""
	return {
		"applies_to": applies_to,
		"start_month": start[0],
		"start_day": start[1],
		"end_month": end[0],
		"end_day": end[1],
		"eth_start_month": eth_start[0],
		"eth_start_day": eth_start[1],
		"eth_end_month": eth_end[0],
		"eth_end_day": eth_end[1],
	}


SEASONS = (
	{
		"season_name": "Meher input season",
		"all_year": 0,
		"handling_class": C.HANDLING_URGENT,
		"priority": C.PRIORITY_URGENT,
		"description": "C01 agricultural inputs. Belg woredas use February to April instead.",
		"windows": (
			_window(C.WINDOW_DEFAULT, (5, 15), (8, 31), ("Ginbot", 7), ("Nehase", 25)),
			_window(C.WINDOW_BELG, (2, 1), (4, 30), ("Tir", 1), ("Miyazya", 30)),
		),
	},
	{
		"season_name": "Irrigation season (dry season)",
		"all_year": 0,
		"handling_class": C.HANDLING_URGENT,
		"priority": C.PRIORITY_URGENT,
		"description": "C02 mechanization, farm technology and fuel. A woreda may override the window.",
		"windows": (_window(C.WINDOW_DEFAULT, (10, 1), (4, 30), ("Tikimt", 1), ("Miyazya", 30)),),
	},
	{
		"season_name": "Dry / irrigation season",
		"all_year": 0,
		"handling_class": C.HANDLING_URGENT,
		"priority": C.PRIORITY_URGENT,
		"description": "C07 irrigation and water. A woreda may override the window.",
		"windows": (_window(C.WINDOW_DEFAULT, (10, 1), (4, 30), ("Tikimt", 1), ("Miyazya", 30)),),
	},
	{
		"season_name": "Harvest marketing peak",
		"all_year": 0,
		"handling_class": C.HANDLING_STANDARD,
		"priority": C.PRIORITY_HIGH,
		"description": "C09 markets and buyer-seller disputes. Standard handling, High priority in the window.",
		"windows": (_window(C.WINDOW_DEFAULT, (10, 1), (12, 31), ("Tikimt", 1), ("Tahsas", 30)),),
	},
	{
		"season_name": "Pest and disease outbreak (all year)",
		"all_year": 1,
		"handling_class": C.HANDLING_URGENT,
		"priority": C.PRIORITY_URGENT,
		"description": "C17 pest and disease outbreak response. Urgent every day of the year.",
		"windows": (),
	},
)

# type code -> (is_urgent, season). Every type listed follows its season; only the Urgent ones
# are made Urgent by it.
TYPE_SEASONS = {
	**{f"C01-0{n}": (1, "Meher input season") for n in range(1, 9)},
	**{f"C02-0{n}": (1, "Irrigation season (dry season)") for n in range(1, 6)},
	**{f"C07-0{n}": (1, "Dry / irrigation season") for n in range(1, 4)},
	**{f"C09-0{n}": (0, "Harvest marketing peak") for n in range(1, 6)},
	**{f"C17-0{n}": (1, "Pest and disease outbreak (all year)") for n in range(1, 5)},
}

URGENT_TYPE_CODES = tuple(code for code, (urgent, _season) in TYPE_SEASONS.items() if urgent)
