# KServe `inferenceservice-config` ingress patch

Applied to configmap `kserve/inferenceservice-config`, key `data.ingress`
(a JSON string, not a nested object -- must be rewritten whole, not merged).

## Why

KServe's chart defaults assume Istio + Knative (`ingressClassName: istio`,
`ingressDomain: example.com`, gateway-based routing). This cluster runs
nginx ingress (Traefik disabled) on host port 8080 with the `*.127.0.0.1.sslip.io`
convention used by every other service in this repo, and **KServe's own
ingress creation is disabled** (`disableIngressCreation: true`) — we ship our
own nginx `Ingress` per InferenceService instead (version-proof, matches repo
conventions, debuggable with plain `kubectl describe ingress`).

## Fields changed (4 of 15)

| key | default | applied |
|---|---|---|
| `ingressClassName` | `istio` | `nginx` |
| `ingressDomain` | `example.com` | `127.0.0.1.sslip.io` |
| `disableIngressCreation` | `false` | `true` |
| `urlScheme` | `http` | `http` (unchanged, confirmed not empty) |

## Full JSON applied

```json
{
  "enableGatewayApi": false,
  "kserveIngressGateway": "kserve/kserve-ingress-gateway",
  "ingressGateway": "knative-serving/knative-ingress-gateway",
  "knativeLocalGatewayService": "",
  "localGateway": "knative-serving/knative-local-gateway",
  "localGatewayService": "knative-local-gateway.istio-system.svc.cluster.local",
  "ingressClassName": "nginx",
  "ingressDomain": "127.0.0.1.sslip.io",
  "additionalIngressDomains": [],
  "domainTemplate": "{{ .Name }}-{{ .Namespace }}.{{ .IngressDomain }}",
  "pathTemplate": "",
  "urlScheme": "http",
  "disableIstioVirtualHost": false,
  "disableIngressCreation": true,
  "disableHTTPRouteTimeout": false
}
```

## `deploy` key (unchanged, confirmed on install)

```json
{ "defaultDeploymentMode": "Standard" }
```

Note: KServe v0.19.0's chart uses `Knative` / `Standard` as the
`kserve.controller.deploymentMode` Helm value (not the older
`Serverless` / `RawDeployment` naming) — confirmed via
`helm show values oci://ghcr.io/kserve/charts/kserve-resources --version v0.19.0`.
The `inferenceservice-config` configmap's `deploy.defaultDeploymentMode` key
itself is still named `Standard`, matching what this doc assumes.

After patching, the controller deployment was restarted
(`kubectl -n kserve rollout restart deployment kserve-controller-manager`)
so it picks up the new configmap on next InferenceService reconcile.
