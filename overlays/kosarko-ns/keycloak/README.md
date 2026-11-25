# Test OIDC provider for ok-dspace (Keycloak)

A throwaway Keycloak at `https://ok-keycloak.dyn.cloud.e-infra.cz` so the CLARIN
OIDC login (`org.dspace.authenticate.clarin.ClarinOidcAuthentication`,
ufal/clarin-dspace) can be tried end to end on ok-dspace.

**Stateless by design:** `start-dev`, H2 inside the container, no PVC. Every pod
restart re-imports `realm.json`, so git is the whole state; nothing clicked
together in the admin console survives. User `id`s (= `sub`) are pinned in
`realm.json`, so identities stored by DSpace stay valid across restarts. Never
change them, and never change the hostname: the CLARIN plugin stores identities
under the issuer by default.

## Values for DSpace

| | |
|---|---|
| Issuer (`authentication-oidc.issuer`) | `https://ok-keycloak.dyn.cloud.e-infra.cz/realms/dspace-test` (no trailing slash) |
| Discovery | `<issuer>/.well-known/openid-configuration` |
| Client ID (`authentication-oidc.client-id`) | `dspace` (confidential, client_secret_basic, PKCE S256 required) |
| Client secret (`authentication-oidc.client-secret`) | key `dspace-client-secret` of Secret `keycloak-secrets` |
| Redirect URI (registered, exact) | `https://ok-dspace.dyn.cloud.e-infra.cz/server/api/authn/oidc-callback`. The pre-c4577c7e63 `…/api/authn/oidc/callback/clarin-oidc` is also still registered, for images built before the callback moved (its cookie path broke the UI login); drop it once no such image is deployed. |
| Scopes | `openid email profile` are enough; the claims below are sent regardless of scope. Do NOT request `voperson_external_id` as a scope: Keycloak answers an unknown scope with `invalid_scope`. |
| Signing | RS256 (Keycloak's default) |

UserInfo claims besides the standard ones: `groups` (group names, also in the
ID token), `voperson_external_id` and `voperson_external_affiliation` (JSON
arrays, UserInfo only, like e-INFRA CZ).

Suggested config once the backend image contains the plugin (it does NOT yet:
the pinned image is built from clarin-v7, so do not add this before the image
bump, or login breaks):

```properties
plugin.sequence.org.dspace.authenticate.AuthenticationMethod = org.dspace.authenticate.clarin.ClarinOidcAuthentication
authentication-oidc.issuer = https://ok-keycloak.dyn.cloud.e-infra.cz/realms/dspace-test
authentication-oidc.client-id = dspace
authentication-oidc.scopes = openid,email,profile
authentication-oidc.user-info.external-ids = voperson_external_id
authentication-oidc.user-info.organization = voperson_external_affiliation
authentication-oidc.user-info.groups = groups
```

Keep the client secret out of `local.d/extra.cfg`, because this repo is public. Pass it
to the backend as an env var in `../backend-deployment.yaml` (`.` → `__P__`,
`-` → `__D__`, like `dspace__P__hostname` there):

```yaml
- name: authentication__D__oidc__P__client__D__secret
  valueFrom:
    secretKeyRef: {name: keycloak-secrets, key: dspace-client-secret}
```

Pinning trap: the backend image with the plugin has to be pinned by hand, and
`.github/workflows/pin-ok-dspace.yml` overwrites a hand pin on the next
clarin-v7 build. Pause or coordinate that workflow for the test window,
otherwise `plugin.sequence` names a class the running image doesn't have.

## Test users

All share one password: key `test-users-password` of Secret `keycloak-secrets`.

| user | sub | email | what it exercises |
|---|---|---|---|
| `alice` | `9349e643-d00d-4bc3-8616-12f700693035` | alice@example.org, verified | happy path; groups `testers`; two affiliations |
| `bob` | `3fba68da-c68d-4898-a588-cb2be27e5fff` | bob@example.org, **unverified** | `require-verified-email`; groups `testers`, `editors` |
| `carol` | `360edd23-cb96-47cd-b314-54b0b9f9ffdd` | carol@example.org, verified | `external-ids` linking: `voperson_external_id = carol@idp-a.example.org`. To test the link, first give an EPerson the netid `carol@idp-a.example.org[<any IdP entityID>]` |
| `dave` | `b71257d2-df96-4b01-a3a9-4627b749f2ee` | **none** | login without an email claim |

## One-time setup (by hand)

The kubeconfig in the repo root must be a valid Rancher token first.

1. Create the Secret. It is deliberately not in git, because the reconciler has no
   `secrets` access, and the admin password must not be public: whoever is
   master admin can mint any `sub`.

   ```bash
   kubectl create secret generic keycloak-secrets -n kosarko-ns \
     --from-literal=admin-password="$(openssl rand -base64 24)" \
     --from-literal=dspace-client-secret="$(openssl rand -hex 32)" \
     --from-literal=test-users-password="$(openssl rand -base64 12)"
   ```

2. After merge, apply once by hand: `kubectl apply -k overlays/kosarko-ns/`.
   The reconciler's Role has no `create` on Deployments, so every 5-minute
   reconcile fails until the `keycloak` Deployment exists. After that, CI
   maintains it.

3. Run `overlays/kosarko-ns/keycloak/smoke-test.sh`. It checks the issuer,
   runs the code flow (PKCE) for each user without following the redirect to
   DSpace, and prints each user's UserInfo. Compare the output with the table
   above before debugging DSpace against this realm. **The realm import has
   not been run anywhere yet**, so this is its first test: default scopes
   delivering `sub`/`email`/`email_verified`, the user-profile component, the
   pinned ids, and the `groups`/`voperson_*` mappers.
   The DNS name should appear once the Ingress exists (the same mechanism as
   `ok-dspace.dyn.cloud.e-infra.cz`). The TLS certificate comes from
   cert-manager over HTTP-01.

## Admin console

Not published: the Ingress only routes `/realms/dspace-test/` and `/resources/`.
Use `kubectl port-forward -n kosarko-ns svc/keycloak 8080:8080`, then open
`http://localhost:8080/admin/` (user `admin`). `KC_HOSTNAME_ADMIN` points
there. That port-forward login is untested; it isn't needed for the OIDC tests,
since the realm comes from git.

## Network policies

* `keycloak-from-ingress-controller`: lets public traffic in.
* `dspace-backend-to-ingress-controller`: the backend reaches Keycloak through
  the public hostname (Spring requires the configured issuer to equal the
  discovered one), which loops back to the ingress controller on a private IP.
  Without this policy, logins time out.

## Removing it

Drop `keycloak` from `components:` in `../kustomization.yaml`, then delete by hand
(the reconciler has no `delete`): Deployment and Service `keycloak`, Ingress
`keycloak-ingress`, NetworkPolicies `keycloak-from-ingress-controller` and
`dspace-backend-to-ingress-controller`, ConfigMaps `keycloak-realm-*`, Secrets
`keycloak-secrets` and `ok-keycloak-dyn-cloud-e-infra-cz-tls`.
