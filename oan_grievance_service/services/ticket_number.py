"""Grievance ticket number generation.

Nine characters, case-insensitive, Crockford Base32:

	R CCC SSSS Y      3 001 002A 0   ->  3001002A0
	| |   |    |
	| |   |    +- year, 1 char
	| |   +------ sequence, 4 chars, counted within region + category + year
	| +---------- category, 3 chars
	+------------ region, 1 char

Agreed in the standup of 16 September 2026 and superseding FSD 3.2.3, which
specified REGION-WOREDA-CATEGORY-SEQUENCE. The woreda is deliberately absent:
grievances are handled by regional offices, so the woreda earned no place in
the identifier, and encoding it would have required hand-assigning short codes
to 1,378 woredas.

The first four characters decode to region and category without a database
lookup, which is what lets a ticket be routed and counted from the number
alone.

Nothing here is secret. The sequence is a plain counter, and protection against
reading another submitter's grievance comes from the authorisation check on the
lookup, not from the ticket being hard to guess.

Capacity: 32 regions, 32,768 categories, 1,048,576 tickets per region per
category per year, and 32 years before the year character repeats.
"""

import datetime
from dataclasses import dataclass

import frappe
from frappe import _
from frappe.model.naming import getseries

# Crockford Base32. I, L, O and U are absent: the first three are misread as 1,
# 1 and 0, and U is dropped to avoid accidental obscenities. Chosen over a
# bespoke 31-character set because the decode below can then forgive the
# misreadings the alphabet is designed around.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
BASE = len(ALPHABET)
ZERO = ALPHABET[0]

# Characters a reader may substitute, mapped back on decode.
DECODE_ALIASES = {"I": "1", "L": "1", "O": "0"}

REGION_WIDTH = 1
CATEGORY_WIDTH = 3
SEQUENCE_WIDTH = 4
YEAR_WIDTH = 1
TICKET_WIDTH = REGION_WIDTH + CATEGORY_WIDTH + SEQUENCE_WIDTH + YEAR_WIDTH  # 9

# Which calendar the year character counts in, and the year that maps to "0".
#
# Gregorian is the default because every other date in the system is Gregorian.
# Ethiopia's own calendar is supported because the manual complaint form this
# module digitises numbers its references by the Ethiopian year
# (LK/GR/2018/0147 was filed in November 2025), so an officer holding both
# documents would otherwise see two different years for one case. Switch this
# to "ethiopian" if the annual grievance reports are cut on the Ethiopian year.
CALENDAR = "gregorian"
YEAR_EPOCH = {"gregorian": 2026, "ethiopian": 2018}

AREA_DOCTYPE = "Grievance Administrative Area"
CATEGORY_DOCTYPE = "Grievance Service Category"
REGION_LEVEL = "Region"


# --------------------------------------------------------------------------
# Base32
# --------------------------------------------------------------------------


def encode(value: int, width: int) -> str:
	"""A non-negative integer as a fixed-width Base32 string."""
	if value < 0:
		frappe.throw(_("Ticket sequence cannot be negative."))

	digits = []
	remaining = value
	while remaining:
		remaining, position = divmod(remaining, BASE)
		digits.append(ALPHABET[position])

	encoded = "".join(reversed(digits)) or ZERO
	if len(encoded) > width:
		frappe.throw(
			_("Ticket sequence {0} exceeds the {1} characters reserved for it.").format(value, width),
			title=_("Ticket Numbering Exhausted"),
		)
	return encoded.rjust(width, ZERO)


def clean(text: str) -> str:
	"""Strip the display formatting and fold the characters a reader substitutes.

	The alphabet excludes I, L, O and U precisely because they are misread, so
	mapping them back can never collide with a legitimate ticket character.
	"""
	stripped = (text or "").replace("-", "").replace(" ", "").strip().upper()
	return "".join(DECODE_ALIASES.get(char, char) for char in stripped)


def normalize(ticket: str) -> str:
	"""A ticket number as it is stored, from however a person typed it.

	`3-001-002a-0`, `3 001 002A 0` and `3OO1OO2AO` all resolve to `3001002A0`.
	Every lookup goes through this, so a submitter reading a number off an SMS
	or back over a phone line is not defeated by the hyphens we printed or by
	the characters the alphabet already assumes they will get wrong.

	Returns the input unchanged when it does not look like a ticket number, so
	a caller searching by something else still gets its own not-found error
	rather than one about the alphabet.
	"""
	cleaned = clean(ticket)
	if len(cleaned) != TICKET_WIDTH:
		return (ticket or "").strip()
	if any(char not in ALPHABET for char in cleaned):
		return (ticket or "").strip()
	return cleaned


def decode(text: str) -> int:
	"""Read a Base32 string back to an integer, forgiving common misreadings."""
	value = 0
	for char in clean(text):
		position = ALPHABET.find(char)
		if position < 0:
			frappe.throw(_("{0} is not a valid ticket number character.").format(char))
		value = value * BASE + position
	return value


# --------------------------------------------------------------------------
# Segments
# --------------------------------------------------------------------------


def region_of(area_name: str | None) -> dict | None:
	"""The Region-level ancestor of an area, or the area itself if it is one.

	Resolved from the nested-set bounds rather than by climbing
	`parent_administrative_area`: an ancestor is any node enclosing this one, so
	one indexed range query replaces a round trip per level. Over 200 kebeles in
	the seeded tree that is 0.35 ms against 0.49 ms, and EXPLAIN reports five
	candidate rows on the lft index rather than a scan.

	Grievances attach to a kebele or a woreda depending on how the tree is
	loaded, so the area itself is considered before its ancestors.
	"""
	if not area_name:
		return None

	fields = ["name", "area_name", "ticket_code", "level_name"]
	own = frappe.db.get_value(AREA_DOCTYPE, area_name, [*fields, "lft", "rgt"], as_dict=True)
	if not own:
		return None
	if own.get("level_name") == REGION_LEVEL:
		return dict(own)

	region = frappe.db.get_value(
		AREA_DOCTYPE,
		{
			"lft": ["<", own.get("lft")],
			"rgt": [">", own.get("rgt")],
			"level_name": REGION_LEVEL,
		},
		fields,
		as_dict=True,
		# Deepest first, so a nested region would win over a broader one.
		order_by="lft desc",
	)
	return dict(region) if region else None


def region_segment(area_name: str | None) -> str:
	"""The region's ticket character.

	Deliberately strict. There are only 14 regions and they are seeded on
	install, so a missing character means the masters are misconfigured — and a
	ticket issued under a guessed region would be wrong for the life of the
	case.
	"""
	region = region_of(area_name)
	if not region:
		frappe.throw(
			_("No region found above administrative area {0}; cannot build a ticket number.").format(
				frappe.bold(area_name or "")
			),
			title=_("Region Not Resolved"),
		)

	code = (region.get("ticket_code") or "").strip().upper()
	if not code:
		frappe.throw(
			_("Region {0} has no Ticket Code. Set one before grievances can be filed there.").format(
				frappe.bold(region.get("area_name") or region.get("name"))
			),
			title=_("Region Ticket Code Missing"),
		)
	return _validate(code, REGION_WIDTH, _("Region {0}").format(region.get("area_name")))


def category_segment(category: str | None) -> str:
	"""The service category's ticket code."""
	if not category:
		frappe.throw(_("A service category is required to build a ticket number."))

	# Cached: categories are static configuration read on every submission, and
	# Frappe clears the entry when the record is saved.
	code = (frappe.db.get_value(CATEGORY_DOCTYPE, category, "code", cache=True) or "").strip().upper()
	if not code:
		frappe.throw(
			_("Service category {0} has no code. Set one before grievances can be filed under it.").format(
				frappe.bold(category)
			),
			title=_("Category Code Missing"),
		)
	return _validate(code, CATEGORY_WIDTH, _("Service category {0}").format(category))


def _validate(code: str, width: int, subject: str) -> str:
	"""Reject a configured code that cannot appear in a ticket number."""
	if len(code) != width:
		frappe.throw(
			_("{0} has code {1}; it must be exactly {2} character(s).").format(
				subject, frappe.bold(code), width
			),
			title=_("Invalid Ticket Code"),
		)
	invalid = [c for c in code if c not in ALPHABET]
	if invalid:
		frappe.throw(
			_("{0} has code {1}, which uses {2}. Ticket codes exclude I, L, O and U.").format(
				subject, frappe.bold(code), ", ".join(invalid)
			),
			title=_("Invalid Ticket Code"),
		)
	return code


# --------------------------------------------------------------------------
# Year
# --------------------------------------------------------------------------


def _is_gregorian_leap(year: int) -> bool:
	return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def ethiopian_year(on: datetime.date) -> int:
	"""The Ethiopian year containing a Gregorian date.

	The Ethiopian year turns on 11 September, or 12 September when the next
	Gregorian year is a leap year, and runs seven to eight years behind. Only
	the year is derived — the month and day are not needed here.
	"""
	new_year_day = 12 if _is_gregorian_leap(on.year + 1) else 11
	if (on.month, on.day) >= (9, new_year_day):
		return on.year - 7
	return on.year - 8


def year_value(on: datetime.date | None = None) -> int:
	"""Years elapsed since the epoch, wrapping at the width of the field."""
	on = on or frappe.utils.getdate(frappe.utils.nowdate())
	year = ethiopian_year(on) if CALENDAR == "ethiopian" else on.year
	return (year - YEAR_EPOCH[CALENDAR]) % (BASE**YEAR_WIDTH)


def year_segment(on: datetime.date | None = None) -> str:
	return encode(year_value(on), YEAR_WIDTH)


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def scope_key(region: str, category: str, year: str) -> str:
	"""The counter this ticket draws from.

	One counter per region, category and year. Many small counters rather than
	one national one: concurrent submissions contend only with others in the
	same region, category and year, and each year starts the count again.
	"""
	return f"GRV-{region}{category}{year}-"


@dataclass(frozen=True, slots=True)
class TicketSegments:
	"""The static prefix segments of a ticket before sequence generation."""

	region: str
	category: str
	year: str

	def __getitem__(self, item):
		return getattr(self, item)


@dataclass(frozen=True, slots=True)
class ParsedTicket:
	"""A parsed grievance ticket representation."""

	region: str
	category: str
	sequence: str
	year: str
	raw: str
	formatted: str


def segments(
	administrative_area: str | None,
	service_category: str | None,
	on: datetime.date | None = None,
) -> TicketSegments:
	"""The three characters known before a sequence is drawn.

	Read-only, so a caller can show what a ticket will look like without
	consuming a number.
	"""
	return TicketSegments(
		region=region_segment(administrative_area),
		category=category_segment(service_category),
		year=year_segment(on),
	)


def generate(
	administrative_area: str | None,
	service_category: str | None,
	on: datetime.date | None = None,
) -> str:
	"""The full ticket number, drawing the next sequence for its scope.

	`getseries` increments the counter inside the caller's transaction, so two
	submissions in the same region, category and year serialise on one row and
	cannot be issued the same number. Every call consumes a number, so this
	belongs in `autoname()` and nowhere else.
	"""
	parts = segments(administrative_area, service_category, on)
	key = scope_key(parts.region, parts.category, parts.year)
	sequence = encode(int(getseries(key, 1)), SEQUENCE_WIDTH)
	return f"{parts.region}{parts.category}{sequence}{parts.year}"


def parse(ticket: str) -> ParsedTicket:
	"""Parse a raw ticket number into its constituent components."""
	normalized = normalize(ticket)
	if len(normalized) != TICKET_WIDTH:
		frappe.throw(_("Invalid ticket number format: {0}").format(ticket), title=_("Invalid Ticket"))
	region = normalized[:REGION_WIDTH]
	category = normalized[REGION_WIDTH : REGION_WIDTH + CATEGORY_WIDTH]
	sequence = normalized[REGION_WIDTH + CATEGORY_WIDTH : -YEAR_WIDTH]
	year = normalized[-YEAR_WIDTH:]
	return ParsedTicket(
		region=region,
		category=category,
		sequence=sequence,
		year=year,
		raw=normalized,
		formatted=f"{region}-{category}-{sequence}-{year}",
	)


def display(ticket: str) -> str:
	"""The ticket grouped for reading aloud: 3001002A0 -> 3-001-002A-0.

	Stored flat; this is presentation only, so the grouping can change without
	touching a single stored value.
	"""
	if not ticket or len(ticket) != TICKET_WIDTH:
		return ticket or ""
	region = ticket[:REGION_WIDTH]
	category = ticket[REGION_WIDTH : REGION_WIDTH + CATEGORY_WIDTH]
	sequence = ticket[REGION_WIDTH + CATEGORY_WIDTH : -YEAR_WIDTH]
	year = ticket[-YEAR_WIDTH:]
	return f"{region}-{category}-{sequence}-{year}"
