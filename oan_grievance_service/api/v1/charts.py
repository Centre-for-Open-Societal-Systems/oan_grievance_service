"""Dashboard charts over the analytics rollups.

    GET /api/v1/charts?charts=<id,...>&<filters>     Grievance Admin: any chart, several per call.
                                                     Grievance Officer: the public charts, narrowed
                                                     to the caller's RBAC desks.
    GET /api/v1/charts/<chart_id>?<filters>          Grievance Dashboard Reader; public charts only

Both are answered by services.dashboard from the rollups the scheduler refreshes
every 15 minutes, never from Grievance on the request; see that module for what
each chart returns. The public form is the contract the OAN dashboards read
(`GET <base>/api/v1/charts/<id>` returning `{data: [...]}`), and serves counts
only.

`grvByAdminLevel` counts grievances at Federal, Region and Woreda level (`level=` picks some).
An officer reads their own level and the ones below it, over the cases their desks cover.

The OAN dashboards call the public form as a Frappe user holding Grievance Dashboard
Reader, with that user's API key and secret (`Authorization: token <key>:<secret>`).
Frappe checks the key before the middleware runs; the middleware then accepts it
because these routes declare `auth=("jwt", "api_key")`. Both chart routes take a JWT
or an API key for now, as before per-route auth existed; the plan is to move them to
JWT only.

Each public chart is its own literal route rather than one `<chart_id>` route, so an
unknown or admin-only id never reaches this code at all.
"""

import re
from datetime import timedelta
from typing import Literal

import frappe
from frappe import _
from frappe.utils import getdate, now_datetime
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	check_rate_limit,
	error_response,
	handle_api_errors,
	parse_multi_value,
	require_role,
	success_response,
)
from pydantic import BaseModel, ConfigDict, Field

from oan_grievance_service.services import constants as C
from oan_grievance_service.services import dashboard

route = prefixed("/api/v1")

ADMIN_ROLES = [C.ROLE_ADMIN, "System Manager", "Administrator"]
PUBLIC_CHART_ROLES = [C.ROLE_DASHBOARD_READER, *ADMIN_ROLES]
# The JWT form: admins see everything, an officer sees the public charts for their desks.
CHARTS_ROLES = [C.ROLE_OFFICER, *ADMIN_ROLES]
MAX_CHARTS = 20
MAX_SPAN_DAYS = 731
LIMITS = {"region": 100, "service_category": 50, "assigned_dept": 100}
RATE_LIMIT = 120  # requests per minute, per user

# Parameters only the admin view takes. The public view ignores them rather than
# rejecting them, so a caller cannot tell an admin-only parameter from a typo.
ADMIN_ONLY_PARAMS = ("assigned_dept", "limit")


class InvalidParam(Exception):
	def __init__(self, field, message):
		super().__init__(message)
		self.field = field
		self.message = message


class ChartsQuery(BaseModel):
	model_config = ConfigDict(extra="ignore", populate_by_name=True)

	region: str | list[str] | None = None
	service_category: str | list[str] | None = None
	assigned_dept: str | list[str] | None = None
	from_date: str | None = Field(None, alias="from")
	to_date: str | None = Field(None, alias="to")
	month: str | None = None
	as_of_month: str | None = None
	granularity: Literal["month", "week"] = "month"
	level: str | list[str] | None = None
	limit: int = Field(10, ge=1, le=50)


def _parse_date(field, value):
	if not value:
		return None
	if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
		raise InvalidParam(field, _("Expected a date as YYYY-MM-DD"))
	try:
		return getdate(value)
	except Exception:
		raise InvalidParam(field, _("Not a valid date")) from None


def _parse_month(field, value, today):
	if not value:
		return None
	if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
		raise InvalidParam(field, _("Expected a month as YYYY-MM"))
	if value > today.strftime("%Y-%m"):
		raise InvalidParam(field, _("{0} must not be in the future").format(field))
	return value


def _known(doctype, fieldname, values, field, extra_filters=None):
	if not values:
		return ()
	if len(values) > LIMITS[field]:
		raise InvalidParam(field, _("At most {0} values").format(LIMITS[field]))
	filters = {fieldname: ["in", values], **(extra_filters or {})}
	known = set(frappe.get_all(doctype, filters=filters, pluck=fieldname))
	unknown = [v for v in values if v not in known]
	if unknown:
		raise InvalidParam(field, _("Unknown value(s): {0}").format(", ".join(unknown)))
	return tuple(values)


def _levels(values):
	values = tuple(dict.fromkeys(v.strip().lower() for v in values))
	unknown = [v for v in values if v not in dashboard.LEVELS]
	if unknown:
		raise InvalidParam(
			"level",
			_("Unknown value(s): {0}; expected {1}").format(", ".join(unknown), ", ".join(dashboard.LEVELS)),
		)
	return values


def parse_params(raw, admin):
	"""Validate the query string into dashboard.Params. Raises InvalidParam or pydantic's error."""
	raw = {k: v for k, v in raw.items() if k not in ("cmd", "charts")}
	if not admin:
		for name in ADMIN_ONLY_PARAMS:
			raw.pop(name, None)
		# The OAN dashboards name this filter `category`.
		if "category" in raw and "service_category" not in raw:
			raw["service_category"] = raw["category"]
	query = ChartsQuery.model_validate(raw)

	today = getdate(now_datetime())
	from_date = _parse_date("from", query.from_date)
	to_date = _parse_date("to", query.to_date)
	if from_date and to_date and from_date > to_date:
		raise InvalidParam("from", _("from must not be after to"))
	if from_date and (to_date or today) - from_date > timedelta(days=MAX_SPAN_DAYS):
		raise InvalidParam("from", _("The period may span at most two years"))

	month = _parse_month("month", query.month, today)
	as_of_month = _parse_month("as_of_month", query.as_of_month, today)

	return dashboard.Params(
		region=_known(
			"Grievance Administrative Area",
			"code",
			parse_multi_value(query.region),
			"region",
			{"level_name": "Region", "is_active": 1},
		),
		service_category=_known(
			"Grievance Service Category",
			"name",
			parse_multi_value(query.service_category),
			"service_category",
		),
		assigned_dept=_known(
			"Grievance Department", "name", parse_multi_value(query.assigned_dept), "assigned_dept"
		),
		from_date=from_date,
		to_date=to_date,
		month=month,
		as_of_month=as_of_month,
		granularity=query.granularity,
		limit=query.limit,
		level=_levels(parse_multi_value(query.level)),
	)


def _echo(params, admin):
	echo = {
		"region": list(params.region),
		"service_category": list(params.service_category),
		"level": list(params.level),
		"from": params.from_date.isoformat() if params.from_date else None,
		"to": (params.to_date or getdate(now_datetime())).isoformat(),
	}
	if admin:
		echo["assigned_dept"] = list(params.assigned_dept)
	return echo


def _invalid(e):
	frappe.response["http_status_code"] = 400
	return error_response(
		message=_("Validation failed"), code="VALIDATION_ERROR", details={e.field: e.message}
	)


def _is_admin():
	return bool(set(frappe.get_roles()) & set(ADMIN_ROLES))


@route(
	"/charts",
	methods=("GET",),
	summary="Dashboard charts (Grievance Admin, Grievance Officer)",
	tier="officer-core",
	# Transitional: JWT only once API-key callers have moved.
	auth=("jwt", "api_key"),
	roles=CHARTS_ROLES,
)
@handle_api_errors
@require_role(CHARTS_ROLES)
def get_charts(charts: str | None = None, **kwargs):
	"""Several charts in one round trip, each built and cached on its own.

	An admin may ask for any chart. An officer gets the public (counts only) charts, narrowed
	to the cases inside their RBAC desks; an admin-only chart id is as unknown to them as a
	mistyped one. An officer on no usable desk reads zeros.

	A chart that fails comes back as `data[<id>] = null` with its error in
	`meta.errors`, so one broken tile does not blank the dashboard.
	"""
	check_rate_limit(f"grievance_charts:user:{frappe.session.user}", RATE_LIMIT, 60)

	admin = _is_admin()
	available = dashboard.CHARTS if admin else dashboard.PUBLIC_CHARTS
	requested = parse_multi_value(charts) or list(available)
	if len(requested) > MAX_CHARTS:
		frappe.response["http_status_code"] = 400
		return error_response(
			message=_("Validation failed"),
			code="VALIDATION_ERROR",
			details={"charts": _("At most {0} charts per request").format(MAX_CHARTS)},
		)
	unknown = [c for c in requested if c not in available]
	if unknown:
		frappe.response["http_status_code"] = 404
		return error_response(
			message=_("Unknown chart(s)"), code="NOT_FOUND", details={"unknown_charts": unknown}
		)

	try:
		params = parse_params(kwargs, admin=admin)
	except InvalidParam as e:
		return _invalid(e)

	scope = None if admin else dashboard.caller_scope()
	data, as_of, errors, unavailable = {}, {}, {}, {}
	for chart_id in requested:
		try:
			result = dashboard.get_chart(chart_id, params, admin=admin, scope=scope)
			data[chart_id] = result["rows"]
			as_of[chart_id] = result["as_of"]
			if result.get("unavailable"):
				unavailable[chart_id] = result["unavailable"]
		except Exception:
			frappe.log_error(title=f"Dashboard chart {chart_id} failed")
			data[chart_id] = None
			errors[chart_id] = _("This chart could not be built")

	return success_response(
		data=data,
		message=_("Charts fetched successfully"),
		meta={
			"as_of": as_of,
			"filters": _echo(params, admin=admin),
			"errors": errors,
			"unavailable": unavailable,
		},
	)


def _public_chart(chart_id, raw):
	check_rate_limit(f"grievance_charts:user:{frappe.session.user}", RATE_LIMIT, 60)
	try:
		params = parse_params(raw, admin=False)
	except InvalidParam as e:
		return _invalid(e)

	result = dashboard.get_chart(chart_id, params, admin=False)
	meta = {"as_of": result["as_of"], "filters": _echo(params, admin=False)}
	if result.get("unavailable"):
		meta["unavailable"] = result["unavailable"]
	return success_response(data=result["rows"], message=_("Chart fetched successfully"), meta=meta)


def _public_endpoint(chart_id):
	def get_public_chart(**kwargs):
		return _public_chart(chart_id, kwargs)

	get_public_chart.__name__ = get_public_chart.__qualname__ = f"get_public_chart_{chart_id}"
	return handle_api_errors(require_role(PUBLIC_CHART_ROLES)(get_public_chart))


for _chart_id in dashboard.PUBLIC_CHARTS:
	route(
		f"/charts/{_chart_id}",
		methods=("GET",),
		summary=f"Public dashboard chart {_chart_id} (counts only)",
		tier="public-dashboards",
		# Transitional: JWT only once the dashboards hold JWTs.
		auth=("jwt", "api_key"),
		roles=PUBLIC_CHART_ROLES,
	)(_public_endpoint(_chart_id))
