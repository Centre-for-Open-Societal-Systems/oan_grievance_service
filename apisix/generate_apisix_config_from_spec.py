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

import copy
import os
from pathlib import Path

from oan_auth_service import gateway_apisix as gw
from oan_auth_service.openapi_generator import write_gateway

# Development and generator baseline defaults (see apisix/README.md).
# Deployments override these by setting the environment variables directly.
os.environ.setdefault("APISIX_REDIS_HOST", "redis")
os.environ.setdefault("APISIX_REDIS_PORT", "6379")
os.environ.setdefault("CORS_ORIGINS_REGEX", r"https?://localhost(:[0-9]+)?")
os.environ.setdefault("REALTIME_ALLOWED_ORIGINS", "http://localhost:3000")
os.environ.setdefault("FRAPPE_WEB_ORIGIN", "http://${FRAPPE_SITE_NAME}:8000")

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


# The realtime socket.io upstream. Ports come from the environment when the file is generated;
# hosts are placeholders, see above.
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
		"note": "Dashboard charts: counts from the dashboard rollups, read by the OAN dashboards with their Frappe API key; APISIX has no consumer for them, so it limits by address. Same as RATE_LIMIT in api/v1/charts.py.",
	},
	"ivr-webhooks": {
		"limit_by": "ip",
		"minute": 120,
		"hour": 3000,
		"policy": "redis",
		"note": "Inbound IVR webhook, called with the gateway key of the IVR platform consumer; not rate limited.",
	},
	"officer-core": {
		"limit_by": "consumer",
		"minute": 300,
		"hour": 10000,
		"policy": "redis",
		"note": "Back-office and staff triage operations: bulk listing, notes, assignment, rejection, and officer options.",
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
			"redis_host": "${APISIX_REDIS_HOST}",
			"redis_port": int(gw.env("APISIX_REDIS_PORT", "6379")),
			"allow_degradation": True,
		},
	)


# ---------------------------------------------------------------------------
# Kubernetes (APISIX Ingress Controller) output
# ---------------------------------------------------------------------------
# `${NAME}` placeholders in the ADC document become these chart values.
HELM_VALUES = {
	"OAN_GRIEVANCE_SOCKET_UPSTREAM_HOST": ".Values.socket.serviceName",
	"OAN_GRIEVANCE_SOCKET_UPSTREAM_EXTERNAL_HOST": ".Values.socket.externalHosts",
	"SOCKET_PORT": ".Values.socket.port",
}
HELM_REQUIRED = {"OAN_GRIEVANCE_SOCKET_UPSTREAM_HOST", "APISIX_REDIS_HOST"}
# The third placeholder is the address of Frappe in another cluster, for a central gateway.
HELM_BACKENDS = {
	f"{APP}-realtime-v1": (
		"${OAN_GRIEVANCE_SOCKET_UPSTREAM_HOST}",
		"${SOCKET_PORT}",
		"${OAN_GRIEVANCE_SOCKET_UPSTREAM_EXTERNAL_HOST}",
	),
}


# The IVR webhook is called with the IVR vendor's gateway key, the same key it uses for A2C.
# The gateway checks it and calls Frappe with the API key and secret of the Grievance IVR
# Integration user, which APISIX reads from its environment, so the vendor never holds the
# Frappe secret and can be cut off at the gateway alone.
IVR_ROUTE = f"{APP}-ivr-webhooks-custom-post"
IVR_FRAPPE_AUTHORIZATION = "$env://OAN_GRIEVANCE_IVR_FRAPPE_AUTHORIZATION"


def add_ivr_webhook_auth(doc):
	service = doc["services"][0]
	route = next(r for r in service["routes"] if r["name"] == IVR_ROUTE)
	# A route's proxy-rewrite replaces the service's whole, so the /v1 -> /api/v1 rewrite is repeated.
	rewrite = copy.deepcopy(service["plugins"]["proxy-rewrite"])
	rewrite["headers"] = {"set": {"Authorization": IVR_FRAPPE_AUTHORIZATION}}
	# The one caller is the IVR vendor, already limited by its gateway key, so no rate limit.
	route["plugins"].pop("limit-count", None)
	route["plugins"].update(
		{
			"key-auth": {"header": "apikey", "hide_credentials": True},
			"consumer-restriction": {"type": "consumer_name", "whitelist": [gw.IVR_VENDOR_CONSUMER]},
			"proxy-rewrite": rewrite,
		}
	)
	# The vendor is a server, so its path needs no CORS preflight route.
	preflight = next(r for r in service["routes"] if r["name"] == f"{APP}-cors-preflight")
	preflight["uris"] = [u for u in preflight["uris"] if u not in ("/v1/webhooks", "/v1/webhooks/*")]
	doc["consumers"] = [*doc.get("consumers", []), gw.shared_consumer(gw.IVR_VENDOR_CONSUMER, APP)]


def main():
	internal = gw.load_spec(INTERNAL_SPEC_PATH)
	public = gw.load_spec(SPEC_PATH)
	write_gateway(
		app_name=APP,
		internal_spec=internal,
		public_spec=public,
		tiers=TIERS,
		upstream_host_env="OAN_GRIEVANCE_UPSTREAM_HOST",
		upstream_port_env="OAN_GRIEVANCE_UPSTREAM_PORT",
		gateway_dir=SCRIPT_DIR,
		title="OAN Grievance Service",
		extra_services=[socket_service()],
		customize_document=add_ivr_webhook_auth,
		gateway_jwt=GATEWAY_JWT,
		helm_values=HELM_VALUES,
		helm_required=HELM_REQUIRED,
		helm_backends=HELM_BACKENDS,
		helm_ints={"SOCKET_PORT"},
	)


if __name__ == "__main__":
	gw.cli(main)
