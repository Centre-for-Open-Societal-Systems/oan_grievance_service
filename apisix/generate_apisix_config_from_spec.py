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
import re
from pathlib import Path

import yaml
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
# also accepts OpenG2P IAM tokens, which a gateway jwt-auth check would refuse. Must match
# oan_auth_service/apisix: jwt-auth needs the issuer consumer declared there.
GATEWAY_JWT = False
# Frappe upstreams. Ports come from the environment when the file is generated; hosts are
# placeholders, see above.
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
API_UPSTREAM["nodes"][0]["host"] = "${OAN_GRIEVANCE_UPSTREAM_HOST}"
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
SOCKET_UPSTREAM["nodes"][0]["host"] = "${OAN_GRIEVANCE_SOCKET_UPSTREAM_HOST}"

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
# `${NAME}` placeholders in the ADC document become Helm values in the chart templates.
# Ports stay unquoted so they render as integers.
HELM_VALUES = {
	"OAN_GRIEVANCE_UPSTREAM_HOST": ".Values.web.serviceName",
	"OAN_GRIEVANCE_SOCKET_UPSTREAM_HOST": ".Values.socket.serviceName",
	"FRAPPE_SITE_NAME": ".Values.siteName",
	"APISIX_REDIS_HOST": ".Values.redis.host",
	"INGRESS_CLASS_NAME": ".Values.ingressClassName",
	"WEB_PORT": ".Values.web.port",
	"SOCKET_PORT": ".Values.socket.port",
}
HELM_INT_VALUES = {"WEB_PORT", "SOCKET_PORT"}
HELM_REQUIRED = {
	"OAN_GRIEVANCE_UPSTREAM_HOST",
	"OAN_GRIEVANCE_SOCKET_UPSTREAM_HOST",
	"FRAPPE_SITE_NAME",
	"APISIX_REDIS_HOST",
}
# A one-item list whose item is replaced by a loop over a values list.
HELM_LISTS = {
	"__HOSTS__": ".Values.hosts",
	"__REALTIME_ORIGINS__": ".Values.realtimeAllowedOrigins",
	"__CORS_ORIGIN_REGEXES__": ".Values.corsOriginRegexes",
}
EXPR_OPS = {"~~": "RegexMatch", "~*": "RegexMatchCaseInsensitive", "in": "In", "==": "Equal"}
EXPR_NEGATED = {
	"RegexMatch": "RegexNotMatch",
	"RegexMatchCaseInsensitive": "RegexNotMatchCaseInsensitive",
	"In": "NotIn",
	"Equal": "NotEqual",
}


def to_expr(var: list) -> dict:
	"""An ADC `vars` entry (`[name, op, value]`, `[name, "!", op, value]`) as an ApisixRoute expr."""
	name, *rest = var
	negate = rest[0] == "!"
	op, value = rest[1:] if negate else rest
	op = EXPR_OPS[op]
	if negate:
		op = EXPR_NEGATED[op]
	return {
		"subject": {"scope": "Variable", "name": name},
		"op": op,
		("set" if op in ("In", "NotIn") else "value"): value,
	}


def to_crd_plugins(plugins: dict) -> list[dict]:
	out = []
	for name, config in plugins.items():
		config = copy.deepcopy(config)
		if "redis_host" in config:
			config["redis_host"] = "${APISIX_REDIS_HOST}"
		if name == "cors":
			config["allow_origins_by_regex"] = ["__CORS_ORIGIN_REGEXES__"]
		out.append({"name": name, "enable": True, "config": config})
	return out


def to_crd_route(route: dict, shared_plugins: dict, backend: dict) -> dict:
	match: dict = {"hosts": ["__HOSTS__"], "paths": route["uris"]}
	if "methods" in route:
		match["methods"] = route["methods"]
	if route.get("vars"):
		exprs = [to_expr(v) for v in route["vars"]]
		for e in exprs:
			if e["subject"]["name"] == "http_origin":
				e["set"] = ["__REALTIME_ORIGINS__"]
		match["exprs"] = exprs
	rule = {"name": route["name"]}
	if "priority" in route:
		rule["priority"] = route["priority"]
	if route.get("enable_websocket"):
		rule["websocket"] = True
	rule["match"] = match
	rule["plugins"] = to_crd_plugins({**shared_plugins, **route.get("plugins", {})})
	rule["backends"] = [dict(backend)]
	return rule


def to_crd_upstream(service: dict, service_name: str) -> dict:
	up = service["upstream"]
	spec = {"scheme": up["scheme"], "passHost": up["pass_host"]}
	if "upstream_host" in up:
		spec["upstreamHost"] = up["upstream_host"]
	spec["retries"] = up["retries"]
	spec["timeout"] = {k: f"{v}s" for k, v in up["timeout"].items()}
	return {
		"apiVersion": "apisix.apache.org/v2",
		"kind": "ApisixUpstream",
		"metadata": {"name": service_name, "labels": {"app": APP}},
		"spec": spec,
	}


def helm_render(text: str) -> str:
	"""Swap the `${NAME}` placeholders and list sentinels for Helm expressions."""
	for sentinel, values in HELM_LISTS.items():
		if sentinel == "__HOSTS__":
			text = re.sub(
				rf"^( *)hosts:\n *- {sentinel}$",
				rf"{{{{- if {values} }}}}\n\1hosts:\n{{{{- range {values} }}}}\n\1- {{{{ . | quote }}}}\n{{{{- end }}}}\n{{{{- end }}}}",
				text,
				flags=re.M,
			)
		else:
			text = re.sub(
				rf"^( *)- {sentinel}$",
				rf"{{{{- range {values} }}}}\n\1- {{{{ . | quote }}}}\n{{{{- end }}}}",
				text,
				flags=re.M,
			)
	out = []
	for line in text.split("\n"):
		whole = re.fullmatch(r"(\s*(?:- )?(?:[^\s:][^:]*:\s+)?)\$\{(\w+)\}", line)
		if whole and whole[2] in HELM_VALUES:
			expr = HELM_VALUES[whole[2]]
			if whole[2] in HELM_REQUIRED:
				expr = f'required "{expr.removeprefix(".Values.")} is required" {expr}'
			quote = "" if whole[2] in HELM_INT_VALUES else " | quote"
			line = f"{whole[1]}{{{{ {expr}{quote} }}}}"
		else:
			line = re.sub(
				r"\$\{(\w+)\}",
				lambda m: f"{{{{ {HELM_VALUES[m[1]]} }}}}" if m[1] in HELM_VALUES else m[0],
				line,
			)
		out.append(line)
	return "\n".join(out)


def write_k8s_templates(doc: dict, header_lines: list[str]) -> None:
	"""ApisixRoute and ApisixUpstream templates for the APISIX Ingress Controller.

	One ApisixRoute per ADC service. The ApisixUpstream carries the service's upstream
	settings (rewritten Host, timeouts, retries) and is named after the Kubernetes Service
	it configures. Per-route plugins win over the service-level ones, as in ADC.
	"""
	backends = {
		"web": ("${OAN_GRIEVANCE_UPSTREAM_HOST}", "${WEB_PORT}"),
		"socket": ("${OAN_GRIEVANCE_SOCKET_UPSTREAM_HOST}", "${SOCKET_PORT}"),
	}
	routes, upstreams = [], []
	for service in doc["services"]:
		kind = "socket" if "realtime" in service["name"] else "web"
		svc_name, svc_port = backends[kind]
		backend = {"serviceName": svc_name, "servicePort": svc_port}
		routes.append(
			{
				"apiVersion": "apisix.apache.org/v2",
				"kind": "ApisixRoute",
				"metadata": {"name": service["name"], "labels": {"app": APP}},
				"spec": {
					"ingressClassName": "${INGRESS_CLASS_NAME}",
					"http": [to_crd_route(r, service.get("plugins", {}), backend) for r in service["routes"]],
				},
			}
		)
		upstreams.append(to_crd_upstream(service, svc_name))

	K8S_DIR.mkdir(parents=True, exist_ok=True)
	header = "".join(f"# {line}\n" for line in header_lines)
	for filename, resources in (("apisix-route.yaml", routes), ("apisix-upstream.yaml", upstreams)):
		body = "---\n".join(
			yaml.safe_dump(r, sort_keys=False, default_flow_style=False, width=100) for r in resources
		)
		(K8S_DIR / filename).write_text(header + helm_render(body))


def main():
	spec = gw.load_spec(SPEC_PATH)
	routes = gw.reconcile(gw.spec_routes(spec), tier_overrides())
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
	provenance = [
		f"Source Spec: {spec['info']['title']} v{spec['info']['version']}",
		"Generated by generate_apisix_config_from_spec.py -- do not hand-edit;",
		"change the OpenAPI spec or an endpoint's `tier=` and re-run.",
	]
	gw.write_document(
		doc, OUTPUT_PATH, ["OAN Grievance Service ADC config (adc lint|validate|diff|sync)", *provenance]
	)
	write_k8s_templates(doc, ["OAN Grievance Service APISIX Ingress Controller resources", *provenance])
	gw.report(OUTPUT_PATH, routes, doc, spec)


if __name__ == "__main__":
	main()
