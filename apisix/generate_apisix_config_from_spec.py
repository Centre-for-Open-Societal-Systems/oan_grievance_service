#!/usr/bin/env python3
"""
generate_apisix_config_from_spec.py

Generates the ADC gateway configuration (adc.yaml) and the APISIX Ingress Controller
resources (k8s/templates, the oan-grievance-gateway Helm chart) for the OAN Grievance
Service directly from openapi_v1.public.yaml. Routes are grouped by
throttling tier, see oan_auth_service/gateway_apisix.py.

Usage:
    python3 apisix/generate_apisix_config_from_spec.py
"""

from pathlib import Path

from oan_auth_service import gateway_apisix as gw

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SPEC_PATH = REPO_ROOT / "openapi" / "openapi_v1.public.yaml"
INTERNAL_SPEC_PATH = REPO_ROOT / "openapi" / "openapi_v1.yaml"
OUTPUT_PATH = SCRIPT_DIR / "adc.yaml"
K8S_DIR = SCRIPT_DIR / "k8s" / "templates"
APP = "oan-grievance"
# Hosts are written as ${VAR} placeholders that ADC fills in from its own environment at
# `adc diff|sync`, so one committed adc.yaml serves every deployment. Ports stay numbers:
# the schema wants integers, and a quoted placeholder is a string.
SITE_NAME = "${FRAPPE_SITE_NAME}"
# False: the gateway does not verify tokens. Frappe's auth_hooks middleware does, and it
# also accepts OpenG2P IAM tokens, which a gateway jwt-auth check would refuse.
GATEWAY_JWT = False


# Frappe upstreams. Ports come from the environment when the file is generated; hosts are
# placeholders, see above.
def _api_upstream():
	try:
		upstream = gw.upstream(
			host_env="OAN_GRIEVANCE_UPSTREAM_HOST",
			port_env="OAN_GRIEVANCE_UPSTREAM_PORT",
			# Frappe picks the site from the Host header, so send the site name, not the gateway's.
			pass_host="rewrite",
			upstream_host=SITE_NAME,
			timeout={"connect": 5, "send": 20, "read": 20},
			retries=2,
		)
		upstream["nodes"][0]["host"] = "${OAN_GRIEVANCE_UPSTREAM_HOST}"
		return upstream
	except gw.GatewayConfigError:
		return {
			"scheme": "http",
			"nodes": [{"host": "${OAN_GRIEVANCE_UPSTREAM_HOST}", "port": 8000, "weight": 1}],
			"pass_host": "rewrite",
			"upstream_host": SITE_NAME,
			"timeout": {"connect": 5, "send": 20, "read": 20},
			"retries": 2,
		}


def _socket_upstream():
	try:
		upstream = gw.upstream(
			host_env="OAN_GRIEVANCE_SOCKET_UPSTREAM_HOST",
			port_env="OAN_GRIEVANCE_SOCKET_UPSTREAM_PORT",
			pass_host="pass",
			# A socket sits idle between events; socket.io pings every 25 seconds, so a live
			# connection never reaches these and a dead one is reaped after an hour.
			timeout={"connect": 5, "send": 3600, "read": 3600},
			retries=0,
		)
		upstream["nodes"][0]["host"] = "${OAN_GRIEVANCE_SOCKET_UPSTREAM_HOST}"
		return upstream
	except gw.GatewayConfigError:
		return {
			"scheme": "http",
			"nodes": [{"host": "${OAN_GRIEVANCE_SOCKET_UPSTREAM_HOST}", "port": 9000, "weight": 1}],
			"pass_host": "pass",
			"timeout": {"connect": 5, "send": 3600, "read": 3600},
			"retries": 0,
		}


API_UPSTREAM = _api_upstream()
SOCKET_UPSTREAM = _socket_upstream()

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
		"note": "Dashboard charts: counts from the 15-minute rollups, read by the OAN dashboards with their Frappe API key; APISIX has no consumer for them, so it limits by address. Same as RATE_LIMIT in api/v1/charts.py.",
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
# Tier assignments
# ---------------------------------------------------------------------------
def tier_overrides() -> dict[tuple[str, str], str]:
	"""(method, public path) -> tier, from the `tier=` set on each endpoint's `@route`.

	Read from the internal spec, which keeps `x-gateway-tier`; the public spec strips it
	so the throttling decision stays out of the client contract.
	"""
	spec = gw.load_spec(INTERNAL_SPEC_PATH)
	return {
		(method.upper(), gw.public_path(path)): op["x-gateway-tier"]
		for path, methods in spec["paths"].items()
		for method, op in methods.items()
		if "x-gateway-tier" in op
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
		browser_origins=[o.strip() for o in gw.env("REALTIME_ALLOWED_ORIGINS").split(",") if o.strip()],
		# Node calls get_user_info at this address, and Frappe picks the site from its host.
		frappe_web_origin=gw.env("FRAPPE_WEB_ORIGIN"),
		site_name=SITE_NAME,
		limit={
			"rules": [
				{"count": 60, "time_window": 60, "key": "$remote_addr:min"},
				{"count": 1000, "time_window": 3600, "key": "$remote_addr:hour"},
			],
			"rejected_code": 429,
			"policy": "redis",
			"redis_host": "${APISIX_REDIS_HOST}",
			"redis_port": int(gw.env("APISIX_REDIS_PORT")),
			"allow_degradation": True,
		},
	)


# ---------------------------------------------------------------------------
# Kubernetes (APISIX Ingress Controller) output
# ---------------------------------------------------------------------------
# `${NAME}` placeholders in the ADC document become these chart values.
HELM_VALUES = {
	"OAN_GRIEVANCE_UPSTREAM_HOST": ".Values.web.serviceName",
	"OAN_GRIEVANCE_SOCKET_UPSTREAM_HOST": ".Values.socket.serviceName",
	"OAN_GRIEVANCE_UPSTREAM_EXTERNAL_HOST": ".Values.web.externalHosts",
	"OAN_GRIEVANCE_SOCKET_UPSTREAM_EXTERNAL_HOST": ".Values.socket.externalHosts",
	"FRAPPE_SITE_NAME": ".Values.siteName",
	"WEB_PORT": ".Values.web.port",
	"SOCKET_PORT": ".Values.socket.port",
}
HELM_REQUIRED = {
	"OAN_GRIEVANCE_UPSTREAM_HOST",
	"OAN_GRIEVANCE_SOCKET_UPSTREAM_HOST",
	"FRAPPE_SITE_NAME",
	"APISIX_REDIS_HOST",
}
# The third placeholder is the address of Frappe in another cluster, for a central gateway.
HELM_BACKENDS = {
	f"{APP}-service-v1": (
		"${OAN_GRIEVANCE_UPSTREAM_HOST}",
		"${WEB_PORT}",
		"${OAN_GRIEVANCE_UPSTREAM_EXTERNAL_HOST}",
	),
	f"{APP}-realtime-v1": (
		"${OAN_GRIEVANCE_SOCKET_UPSTREAM_HOST}",
		"${SOCKET_PORT}",
		"${OAN_GRIEVANCE_SOCKET_UPSTREAM_EXTERNAL_HOST}",
	),
}


def main():
	spec = gw.load_spec(SPEC_PATH)
	# oan_auth_service's endpoints are served by this bench too.
	routes = gw.with_auth_routes(gw.reconcile(gw.spec_routes(spec), tier_overrides()))
	doc = gw.build_document(
		routes,
		app=APP,
		tiers={**TIERS, **gw.AUTH_TIERS},
		api_upstream=API_UPSTREAM,
		service_plugins=gw.service_plugins(
			methods="GET,POST,PATCH,DELETE,OPTIONS", credentials=False, max_body_mb=15
		),
		extra_services=[socket_service()],
		consumers=[gw.jwt_issuer_consumer()] if GATEWAY_JWT else None,
		gateway_jwt=GATEWAY_JWT,
	)
	provenance = [
		f"Source Spec: {spec['info']['title']} v{spec['info']['version']}",
		"Generated by generate_apisix_config_from_spec.py -- do not hand-edit;",
		"change the OpenAPI spec or an endpoint's `tier=` and re-run.",
	]
	gw.write_document(
		doc, OUTPUT_PATH, ["OAN Grievance Service ADC config (adc lint|validate|diff|sync)", *provenance]
	)
	gw.write_helm_templates(
		doc,
		K8S_DIR,
		["OAN Grievance Service APISIX Ingress Controller resources", *provenance],
		app=APP,
		backends=HELM_BACKENDS,
		values=HELM_VALUES,
		required=HELM_REQUIRED,
		ints={"WEB_PORT", "SOCKET_PORT"},
	)
	gw.report(OUTPUT_PATH, routes, doc, spec)


if __name__ == "__main__":
	gw.cli(main)
