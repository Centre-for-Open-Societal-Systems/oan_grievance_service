#!/usr/bin/env python3
"""
generate_apisix_config_from_spec.py

Generates the ADC gateway configuration (adc.yaml) for the OAN Grievance Service
directly from openapi_v1.public.yaml. Routes are grouped by throttling tier, see
oan_auth_service/gateway_apisix.py.

Usage:
    python3 apisix/generate_apisix_config_from_spec.py
"""

from pathlib import Path

from oan_auth_service import gateway_apisix as gw

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SPEC_PATH = REPO_ROOT / "openapi" / "openapi_v1.public.yaml"
OUTPUT_PATH = SCRIPT_DIR / "adc.yaml"
APP = "oan-grievance"
SITE_NAME = gw.env("FRAPPE_SITE_NAME", "mysite.localhost")
# False: the gateway does not verify tokens. Frappe's auth_hooks middleware does, and it
# also accepts OpenG2P IAM tokens, which a gateway jwt-auth check would refuse. Must match
# oan_auth_service/apisix: jwt-auth needs the issuer consumer declared there.
GATEWAY_JWT = False
# Frappe upstreams. The environment overrides these when the file is generated for a
# deployment; the defaults are for local development.
API_UPSTREAM = gw.upstream(
	host_env="OAN_GRIEVANCE_UPSTREAM_HOST",
	host_default="oan-grievance.internal.svc",
	port_env="OAN_GRIEVANCE_UPSTREAM_PORT",
	port_default=8000,
	# Frappe picks the site from the Host header, so send the site name, not the gateway's.
	pass_host="rewrite",
	upstream_host=SITE_NAME,
	timeout={"connect": 5, "send": 20, "read": 20},
	retries=2,
)
# Frappe's Node socket.io server (`bench socketio`). Its events are described in
# openapi/asyncapi_v1.yaml, not the OpenAPI spec, so its service is declared here.
SOCKET_UPSTREAM = gw.upstream(
	host_env="OAN_GRIEVANCE_SOCKET_UPSTREAM_HOST",
	host_default="oan-grievance-websocket.internal.svc",
	port_env="OAN_GRIEVANCE_SOCKET_UPSTREAM_PORT",
	port_default=9000,
	pass_host="pass",
	# A socket sits idle between events; socket.io pings every 25 seconds, so a live
	# connection never reaches these and a dead one is reaped after an hour.
	timeout={"connect": 5, "send": 3600, "read": 3600},
	retries=0,
)

# ---------------------------------------------------------------------------
# Throttling tiers
# ---------------------------------------------------------------------------
TIERS = {
	"public-reference": {
		"limit_by": "ip",
		"minute": 120,
		"hour": 3000,
		"policy": "redis",
		"note": "Public unauthenticated reference data: areas, dropdown options, health probes.",
	},
	"citizen-intake": {
		"limit_by": "consumer",
		"minute": 60,
		"hour": 1000,
		"policy": "redis",
		"note": "Citizen self-service operations: grievance lodging, tracking, replies, messaging, reopen.",
	},
	"public-dashboards": {
		"limit_by": "ip",
		"minute": 120,
		"hour": 3000,
		"policy": "redis",
		"note": "Dashboard charts: counts from the 15-minute rollups, read by the OAN dashboards with their Frappe API key; Kong has no consumer for them, so it limits by address. Same as RATE_LIMIT in api/v1/charts.py.",
	},
	"officer-core": {
		"limit_by": "consumer",
		"minute": 300,
		"hour": 10000,
		"policy": "redis",
		"note": "Back-office and staff triage operations: bulk listing, notes, assignment, rejection, and officer options.",
	},
}

# ---------------------------------------------------------------------------
# Explicit Tier assignments per route
# ---------------------------------------------------------------------------
TIER_OVERRIDES = {
	("GET", "/v1/grievances/health"): "public-reference",
	("GET", "/v1/grievances/ping"): "public-reference",
	("GET", "/v1/submitters/options"): "public-reference",
	("POST", "/v1/submitters"): "citizen-intake",
	("POST", "/v1/submitters/register"): "citizen-intake",
	("POST", "/v1/submitters/{profile_id}/block"): "officer-core",
	("POST", "/v1/submitters/{profile_id}/unblock"): "officer-core",
	("GET", "/v1/administrative-areas"): "public-reference",
	("GET", "/v1/administrative-areas/{area_id_or_path}/ancestors"): "public-reference",
	("GET", "/v1/grievances"): "officer-core",
	("POST", "/v1/grievances"): "citizen-intake",
	("GET", "/v1/grievances/summary"): "officer-core",
	("GET", "/v1/grievances/options"): "officer-core",
	("POST", "/v1/grievances/{ticket_number}/action"): "citizen-intake",
	("POST", "/v1/grievances/{ticket_number}/feedback"): "citizen-intake",
	("POST", "/v1/grievances/{ticket_number}/message"): "citizen-intake",
	("POST", "/v1/grievances/{ticket_number}/reassign"): "officer-core",
	("POST", "/v1/grievances/{ticket_number}/reassign/decide"): "officer-core",
	("POST", "/v1/grievances/{ticket_number}/defer-sla"): "officer-core",
	("POST", "/v1/grievances/{ticket_number}/defer-sla/decide"): "officer-core",
	("GET", "/v1/grievances/{ticket_number}/timeline"): "citizen-intake",
	("GET", "/v1/grievances/{ticket_number}/response-templates"): "officer-core",
	("POST", "/v1/grievances/{ticket_number}/attachments"): "citizen-intake",
	("GET", "/v1/grievances/{ticket_number}/attachments"): "citizen-intake",
	("GET", "/v1/attachments"): "officer-core",
	("POST", "/v1/attachments"): "citizen-intake",
	("POST", "/v1/attachments/{attachment_id}"): "citizen-intake",
	("GET", "/v1/attachments/{attachment_id}/download"): "citizen-intake",
	("GET", "/v1/attachments/{attachment_id}/view"): "citizen-intake",
	("DELETE", "/v1/attachments/{attachment_id}"): "citizen-intake",
	("GET", "/v1/change-requests"): "officer-core",
	("GET", "/v1/change-requests/{name}"): "officer-core",
	("POST", "/v1/change-requests/{name}/decide"): "officer-core",
	("GET", "/v1/category-assignments"): "officer-core",
	("POST", "/v1/category-assignments"): "officer-core",
	("GET", "/v1/category-assignments/{assignment}"): "officer-core",
	("PATCH", "/v1/category-assignments/{assignment}"): "officer-core",
	("DELETE", "/v1/category-assignments/{assignment}"): "officer-core",
	("POST", "/v1/orgs"): "citizen-intake",
	("GET", "/v1/orgs/me"): "citizen-intake",
	("PATCH", "/v1/orgs/me"): "citizen-intake",
	("GET", "/v1/orgs"): "officer-core",
	("GET", "/v1/orgs/{org_id}"): "officer-core",
	("PATCH", "/v1/orgs/{org_id}/status"): "officer-core",
	("GET", "/v1/officers"): "officer-core",
	("POST", "/v1/officers"): "officer-core",
	("GET", "/v1/officers/status-counts"): "officer-core",
	("GET", "/v1/officers/{officer}"): "officer-core",
	("PATCH", "/v1/officers/{officer}"): "officer-core",
	("POST", "/v1/officers/{officer}/password-resets"): "officer-core",
	("GET", "/v1/response-templates"): "officer-core",
	("POST", "/v1/response-templates"): "officer-core",
	("GET", "/v1/response-templates/{template}"): "officer-core",
	("PATCH", "/v1/response-templates/{template}"): "officer-core",
	("DELETE", "/v1/response-templates/{template}"): "officer-core",
	("GET", "/v1/notification-templates"): "officer-core",
	("GET", "/v1/notification-templates/options"): "officer-core",
	("GET", "/v1/notification-templates/placeholders"): "officer-core",
	("GET", "/v1/notification-templates/{template}"): "officer-core",
	("PATCH", "/v1/notification-templates/{template}"): "officer-core",
	("GET", "/v1/service-categories"): "officer-core",
	("POST", "/v1/service-categories"): "officer-core",
	("GET", "/v1/service-categories/{category}"): "officer-core",
	("PATCH", "/v1/service-categories/{category}"): "officer-core",
	("DELETE", "/v1/service-categories/{category}"): "officer-core",
	("GET", "/v1/grievance-types"): "officer-core",
	("POST", "/v1/grievance-types"): "officer-core",
	("GET", "/v1/grievance-types/{grievance_type}"): "officer-core",
	("PATCH", "/v1/grievance-types/{grievance_type}"): "officer-core",
	("DELETE", "/v1/grievance-types/{grievance_type}"): "officer-core",
	("GET", "/v1/sla-policy"): "officer-core",
	("PATCH", "/v1/sla-policy"): "officer-core",
	("GET", "/v1/sla-configurations"): "officer-core",
	("PATCH", "/v1/sla-configurations/{config}"): "officer-core",
	("GET", "/v1/officers/statistics"): "officer-core",
	("GET", "/v1/charts"): "officer-core",
	**{
		("GET", f"/v1/charts/{chart_id}"): "public-dashboards"
		for chart_id in (
			"grvKpis",
			"grvPerformanceKpis",
			"grvMonthlyTrend",
			"grvWeeklyTrend",
			"grvNetBacklogTrend",
			"grvStatusDistribution",
			"grvByCategory",
			"grvCategoryResolution",
			"grvResolutionRateByRegion",
			"grvSlaRisk",
			"grvPendingDuplicates",
			"grvOldestOpen",
			"grvFilterRegions",
			"grvFilterCategories",
		)
	},
}


def socket_service():
	"""The realtime socket.io service, see openapi/asyncapi_v1.yaml and
	oan_auth_service/gateway_apisix.py `socket_service` for how each route works.

	Browsers connect with oan_grievance_ui's session cookies: a local session's
	`auth_token` (only while `last_activity` shows the session is not idle), or else
	a staff session's IAM access token. Native clients send `Authorization: Bearer`.
	"""
	iam_prefix = gw.env("IAM_COOKIE_PREFIX", "")
	return gw.socket_service(
		app=APP,
		upstream=SOCKET_UPSTREAM,
		session_cookies=[
			{"name": "local", "cookie": "auth_token", "require": ["last_activity"]},
			{"name": "iam", "cookie": f"{iam_prefix}X-Access-Token"},
		],
		browser_origins=[
			o.strip()
			for o in gw.env("REALTIME_ALLOWED_ORIGINS", "http://localhost:3000").split(",")
			if o.strip()
		],
		# Node calls get_user_info at this address, and Frappe picks the site from its host.
		frappe_web_origin=gw.env("FRAPPE_WEB_ORIGIN", f"http://{SITE_NAME}:8000"),
		site_name=SITE_NAME,
		limit={
			"rules": [
				{"count": 60, "time_window": 60, "key": "$remote_addr:min"},
				{"count": 1000, "time_window": 3600, "key": "$remote_addr:hour"},
			],
			"rejected_code": 429,
			"policy": "redis",
			"redis_host": gw.env("APISIX_REDIS_HOST", "redis"),
			"redis_port": int(gw.env("APISIX_REDIS_PORT", "6379")),
			"allow_degradation": True,
		},
	)


def main():
	spec = gw.load_spec(SPEC_PATH)
	routes = gw.reconcile(gw.spec_routes(spec), TIER_OVERRIDES)
	doc = gw.build_document(
		routes,
		app=APP,
		tiers=TIERS,
		api_upstream=API_UPSTREAM,
		service_plugins=gw.service_plugins(
			methods="GET,POST,PATCH,DELETE,OPTIONS", credentials=False, max_body_mb=15
		),
		extra_services=[socket_service()],
		gateway_jwt=GATEWAY_JWT,
		# The oan-auth-jwt-issuer consumer, needed only when GATEWAY_JWT is on, is declared
		# in oan_auth_service/apisix, which holds the issuer key.
	)
	gw.write_document(
		doc,
		OUTPUT_PATH,
		[
			"OAN Grievance Service ADC config (adc lint|validate|diff|sync)",
			f"Source Spec: {spec['info']['title']} v{spec['info']['version']}",
			"Generated by generate_apisix_config_from_spec.py -- do not hand-edit;",
			"change the OpenAPI spec or TIER_OVERRIDES and re-run.",
		],
	)
	gw.report(OUTPUT_PATH, routes, doc, spec)


if __name__ == "__main__":
	main()
