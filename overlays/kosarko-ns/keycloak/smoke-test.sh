#!/usr/bin/env bash
# Post-deploy check of the test realm, without DSpace: runs the authorization
# code flow (PKCE S256) by hand for each test user and prints the UserInfo
# claims. The redirect to ok-dspace is NOT followed -- the code is taken from
# the Location header -- so nothing is logged in to DSpace.
#
# Usage (reads the credentials from the cluster Secret):
#   overlays/kosarko-ns/keycloak/smoke-test.sh [user ...]     # default: all four
# Needs: kubectl (valid kubeconfig), curl, python3.
set -euo pipefail

NS=kosarko-ns
ISSUER=https://ok-keycloak.dyn.cloud.e-infra.cz/realms/dspace-test
REDIRECT=https://ok-dspace.dyn.cloud.e-infra.cz/server/api/authn/oidc/callback/clarin-oidc
CLIENT=dspace

secret() { kubectl get secret keycloak-secrets -n "$NS" -o "jsonpath={.data.$1}" | base64 -d; }
CLIENT_SECRET=$(secret dspace-client-secret)
PASSWORD=$(secret test-users-password)

echo "== discovery"
disc=$(curl -sSf "$ISSUER/.well-known/openid-configuration")
python3 -c 'import json,sys; d=json.loads(sys.argv[1]); i=d["issuer"]; print("issuer:", i); sys.exit(i != sys.argv[2])' "$disc" "$ISSUER" \
  || { echo "FAIL: issuer differs from $ISSUER"; exit 1; }
ep() { python3 -c 'import json,sys; print(json.loads(sys.argv[1])[sys.argv[2]])' "$disc" "$1"; }
AUTH=$(ep authorization_endpoint); TOKEN=$(ep token_endpoint); USERINFO=$(ep userinfo_endpoint)

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

for user in "${@:-alice bob carol dave}"; do
  for u in $user; do
    echo "== $u"
    jar="$tmp/$u.jar"
    read -r verifier challenge < <(python3 -c '
import base64, hashlib, secrets
v = secrets.token_urlsafe(48)
c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
print(v, c)')
    page=$(curl -sSf -c "$jar" -b "$jar" -G "$AUTH" \
      --data-urlencode response_type=code --data-urlencode client_id=$CLIENT \
      --data-urlencode "redirect_uri=$REDIRECT" --data-urlencode "scope=openid email profile" \
      --data-urlencode state=smoke --data-urlencode nonce=smoke \
      --data-urlencode "code_challenge=$challenge" --data-urlencode code_challenge_method=S256)
    action=$(python3 -c 'import html,re,sys; print(html.unescape(re.search(r"<form[^>]*id=\"kc-form-login\"[^>]*action=\"([^\"]+)\"", sys.stdin.read()).group(1)))' <<<"$page")
    location=$(curl -sS -o /dev/null -w '%{redirect_url}' -c "$jar" -b "$jar" "$action" \
      --data-urlencode "username=$u" --data-urlencode "password=$PASSWORD" --data-urlencode credentialId=)
    case "$location" in "$REDIRECT"*code=*) ;; *) echo "FAIL: no code in redirect: ${location:-<none>}"; continue;; esac
    code=$(python3 -c 'import sys,urllib.parse as p; print(p.parse_qs(p.urlparse(sys.argv[1]).query)["code"][0])' "$location")
    at=$(curl -sSf -u "$CLIENT:$CLIENT_SECRET" "$TOKEN" \
      --data-urlencode grant_type=authorization_code --data-urlencode "code=$code" \
      --data-urlencode "redirect_uri=$REDIRECT" --data-urlencode "code_verifier=$verifier" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
    curl -sSf -H "Authorization: Bearer $at" "$USERINFO" | python3 -m json.tool
  done
done
