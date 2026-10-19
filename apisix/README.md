# APISIX gateway config (ADC)

`adc.yaml` is generated from `openapi/openapi_v1.public.yaml`. Do not edit it.
It is applied to an APISIX running in **etcd mode** with
[ADC](https://github.com/api7/adc) (APISIX Declarative CLI).

```bash
python3 apisix/generate_apisix_config_from_spec.py   # spec + tiers -> apisix/adc.yaml
adc lint     -f apisix/adc.yaml                      # local schema check, no gateway needed
adc validate -f apisix/adc.yaml                      # checked by the gateway, nothing saved
adc diff     -f apisix/adc.yaml --label-selector app=oan-grievance
adc sync     -f apisix/adc.yaml --label-selector app=oan-grievance
```

`oan-grievance` is `oan-grievance`. Every service, route and consumer carries the label
`app: oan-grievance`, so a sync with that selector creates, updates and **deletes only this
app's resources**. Apps sharing one APISIX can sync independently and never remove each
other's routes. Always pass `--label-selector`.

Routes are grouped by throttling tier (`TIERS` in the generator; each endpoint's tier is the `tier=` on its `@route`), not by
URL. To expose a new endpoint: add it to the spec, give its `@route` a `tier=`,
regenerate the spec, then this file. The generator refuses to run while an operation has no tier.
Shared logic lives in `oan_auth_service/gateway_apisix.py`.

Routes match the public spec's `/v1/...` paths. A service-level `proxy-rewrite` sends them
to Frappe as `/api/v1/...`. `/api/v1/...` is not served through the gateway.
See `oan_auth_service/docs/API_CONVENTIONS.md`.

## Kubernetes (APISIX Ingress Controller)

The same generator also writes `chart/oan-grievance-gateway/templates/`: an `ApisixRoute`
per ADC service (same routes, tiers and plugins as `adc.yaml`) and an `ApisixUpstream`
per Frappe Service (rewritten `Host`, timeouts, retries). Hosts, Service names and ports,
the site name, the Redis host and the allowed origins are Helm values, see
`chart/oan-grievance-gateway/values.yaml`. Only `values.yaml` and `Chart.yaml` are hand-edited.

```bash
helm lint chart/oan-grievance-gateway
helm upgrade --install oan-grievance-gateway chart/oan-grievance-gateway -n <ns> \
  --set hosts={grievance.example.com} --set siteName=<frappe site> \
  --set web.serviceName=<frappe web Service> --set socket.serviceName=<socketio Service>
```

Install the release in the namespace of the Frappe Services. The Ingress Controller must
watch that namespace and the chart's `ingressClassName`. Manage a gateway with either the
Ingress Controller or `adc sync`, never both: each overwrites the other's routes. Keep
`adc.yaml` for `adc lint|validate`.

## Gateway requirements

```yaml
# APISIX config.yaml
deployment:
  role: traditional
  role_traditional:
    config_provider: etcd
apisix:
  router:
    http: radixtree_uri_with_parameter   # routes use :param segments
```

ADC connects with `ADC_BACKEND=apisix`, `ADC_SERVER=<admin URL>` and `ADC_TOKEN=<admin key>`.

## Environment values

Hosts are written into `adc.yaml` as `${VAR}` placeholders, which ADC fills in from its
own environment at `adc lint|diff|sync`. Export them before running ADC; an unset one
fails the run. Ports, origins and the cookie prefix are read from the environment **when
the file is generated**, so regenerate with their real values. Checked with ADC 0.31.0:
`adc lint` passes with the four variables set and fails on an empty host when unset.

Read at sync time (`${VAR}`): `OAN_GRIEVANCE_UPSTREAM_HOST`,
`OAN_GRIEVANCE_SOCKET_UPSTREAM_HOST`, `FRAPPE_SITE_NAME`, `APISIX_REDIS_HOST`. The defaults
below apply only to the generation-time variables.

| Variable                                                     | Default                                        | Meaning                                                                                                                       |
| ------------------------------------------------------------ | ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `OAN_GRIEVANCE_UPSTREAM_HOST`, `OAN_GRIEVANCE_UPSTREAM_PORT` | `oan-grievance.internal.svc`, `8000`           | Frappe address                                                                                                                |
| `OAN_GRIEVANCE_SOCKET_UPSTREAM_HOST`, `..._PORT`             | `oan-grievance-websocket.internal.svc`, `9000` | socket.io server                                                                                                              |
| `FRAPPE_SITE_NAME`                                           | `mysite.localhost`                             | Sent as `Host` so Frappe picks the site                                                                                       |
| `FRAPPE_WEB_ORIGIN`                                          | `http://<FRAPPE_SITE_NAME>:8000`               | Frappe web address, reachable from the socket.io server, whose host is the site name. The socket.io server sends tokens there |
| `REALTIME_ALLOWED_ORIGINS`                                   | `http://localhost:3000`                        | Comma-separated browser origins allowed to open a socket with a session cookie                                                |
| `IAM_COOKIE_PREFIX`                                          | empty                                          | IAM cookie prefix, as set in oan_grievance_ui                                                                                 |
| `CORS_ORIGINS_REGEX`                                         | `https?://localhost(:[0-9]+)?`                 | Allowed browser origin regex                                                                                                  |
| `APISIX_REDIS_HOST`, `APISIX_REDIS_PORT`                     | `redis`, `6379`                                | Rate-limit counter store                                                                                                      |

## Token checking

`GATEWAY_JWT = False` in the generator (default): the gateway does not verify tokens.
Frappe's `auth_hooks` middleware does, for both `oan_auth_service` tokens and OpenG2P IAM
tokens. The gateway only rate-limits (by client address), caps body size and handles CORS.
Set `GATEWAY_JWT = True` in both repos only for a deployment that issues
`oan_auth_service` tokens exclusively: it adds `jwt-auth` on bearer routes, keys limits by
token, and emits the `oan-auth-jwt-issuer` consumer (needs `OAN_AUTH_JWT_PUBLIC_KEY`, the
PEM, and `OAN_JWT_ISSUER`), which refuses IAM tokens.

## Known limits

- Rate limits are per client address. Users behind one NAT or proxy share a bucket, and
  limits keyed per user are not possible without a verified token. Forward the real client
  address (`real-ip` plugin or trusted proxy config) when a load balancer sits in front.
- Requests with a missing or invalid token reach Frappe, which rejects them (401, or 503 if
  IAM is down). The gateway cannot return that 401 itself.
- `adc diff` prints only a summary; the detail is written to `diff.yaml` in the working directory.

## Realtime (socket.io)

Browsers connect to `/socket.io/` on this gateway with their session cookie. The gateway
copies `auth_token` (when `last_activity` is also present) or the IAM `X-Access-Token`
cookie into `Authorization: Bearer`, and Frappe verifies the token. Only origins in
`REALTIME_ALLOWED_ORIGINS` can use a cookie. Native clients send the header themselves.
oan_auth_service's `realtime/handlers.js` closes a socket when its token expires. The
cookies are sent only to a host they are scoped to, so serve `/socket.io/` on the UI's
host, or set `IAM_COOKIE_DOMAIN` (and the UI's cookie domain) to cover the gateway host.
See `socket_service` in `oan_auth_service/gateway_apisix.py`.
