#!/usr/bin/env bash
# Set up grievance officer placement in a Keycloak realm. Safe to re-run.
#
#   KC_URL=http://localhost:8080 KC_REALM=staff KC_ADMIN_PASSWORD=... \
#   ROLES_CLIENT_ID=<the OAN IAM Service row's application> ./keycloak/apply.sh
#
# ROLES_CLIENT_ID is the client that holds the grievance client roles.
# TOKEN_CLIENT_ID is the client whose tokens reach oan_auth_service (IAM's client);
# it defaults to ROLES_CLIENT_ID.
set -euo pipefail

: "${KC_URL:?}" "${KC_REALM:?}" "${KC_ADMIN_PASSWORD:?}" "${ROLES_CLIENT_ID:?}"
KC_ADMIN_USER="${KC_ADMIN_USER:-admin}"
TOKEN_CLIENT_ID="${TOKEN_CLIENT_ID:-$ROLES_CLIENT_ID}"
DIR="$(cd "$(dirname "$0")" && pwd)"
ADMIN="$KC_URL/admin/realms/$KC_REALM"

TOKEN="$(curl -sf "$KC_URL/realms/master/protocol/openid-connect/token" \
	-d grant_type=password -d client_id=admin-cli \
	--data-urlencode "username=$KC_ADMIN_USER" --data-urlencode "password=$KC_ADMIN_PASSWORD" | jq -r .access_token)"

kc() {
	local method="$1" path="$2"
	shift 2
	curl -sf -X "$method" "$ADMIN$path" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" "$@"
}

client_uuid() {
	kc GET "/clients?clientId=$1" | jq -er '.[0].id' || { echo "client $1 not found in $KC_REALM" >&2; exit 1; }
}

# 1. User profile: merge the grievance attributes and group into the realm's profile. A PUT
# replaces the whole profile, so attributes other apps declared (livestock's) are kept.
kc GET /users/profile | jq --slurpfile ours "$DIR/grievance-user-profile.json" '
	($ours[0].attributes | map(.name)) as $names
	| ($ours[0].groups | map(.name)) as $groups
	| .attributes = ([.attributes[] | select(.name as $n | $names | index($n) | not)] + $ours[0].attributes)
	| .groups = ([(.groups // [])[] | select(.name as $n | $groups | index($n) | not)] + $ours[0].groups)
' | kc PUT /users/profile --data-binary @- >/dev/null
echo "user profile: grievance attributes set"

# 2. Client scope with the userinfo mappers.
SCOPE_ID="$(kc GET /client-scopes | jq -r '.[] | select(.name == "grievance-placement") | .id')"
if [ -z "$SCOPE_ID" ]; then
	kc POST /client-scopes --data-binary "@$DIR/grievance-placement-client-scope.json" >/dev/null
	SCOPE_ID="$(kc GET /client-scopes | jq -r '.[] | select(.name == "grievance-placement") | .id')"
	echo "client scope: created"
else
	jq --arg id "$SCOPE_ID" '. + {id: $id} | del(.protocolMappers)' "$DIR/grievance-placement-client-scope.json" |
		kc PUT "/client-scopes/$SCOPE_ID" --data-binary @- >/dev/null
	echo "client scope: exists, mappers left as they are"
fi

# 3. Attach it as a default scope of the client whose tokens reach oan_auth_service.
kc PUT "/clients/$(client_uuid "$TOKEN_CLIENT_ID")/default-client-scopes/$SCOPE_ID" >/dev/null
echo "client scope: default on $TOKEN_CLIENT_ID"

# 4. Client roles. Names must equal the Frappe role names.
ROLES_UUID="$(client_uuid "$ROLES_CLIENT_ID")"
jq -c '.[]' "$DIR/grievance-client-roles.json" | while read -r role; do
	name="$(jq -r .name <<<"$role")"
	if kc GET "/clients/$ROLES_UUID/roles/$(jq -rn --arg n "$name" '$n|@uri')" >/dev/null 2>&1; then
		echo "role: $name exists"
	else
		kc POST "/clients/$ROLES_UUID/roles" --data-binary "$role" >/dev/null
		echo "role: $name created"
	fi
done

echo
echo "Set userinfo_url on the OAN IAM Service row to:"
echo "  $KC_URL/realms/$KC_REALM/protocol/openid-connect/userinfo"
