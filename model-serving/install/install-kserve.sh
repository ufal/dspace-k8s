#!/usr/bin/env bash
# Idempotent install of cert-manager + KServe (Standard/RawDeployment mode) on
# the local k3s cluster, wired to the repo's nginx/sslip.io ingress convention.
#
# Verified against: cert-manager v1.20.3 chart default, KServe v0.19.0.
# Re-run safely: helm upgrade --install is idempotent; the configmap patch is
# applied every run (harmless if values already match).
set -euo pipefail

helm repo add jetstack https://charts.jetstack.io >/dev/null 2>&1 || true
helm repo update

helm upgrade --install cert-manager jetstack/cert-manager \
  -n cert-manager --create-namespace \
  --set crds.enabled=true \
  --wait --timeout 120s

helm upgrade --install kserve-crd oci://ghcr.io/kserve/charts/kserve-crd \
  --version v0.19.0 -n kserve --create-namespace

# kserve.controller.deploymentMode: confirmed via
#   helm show values oci://ghcr.io/kserve/charts/kserve-resources --version v0.19.0
# valid values in this chart are "Knative" (default) / "Standard" (RawDeployment
# equivalent) -- NOT "Serverless"/"RawDeployment" as in older KServe docs.
helm upgrade --install kserve oci://ghcr.io/kserve/charts/kserve-resources \
  --version v0.19.0 -n kserve \
  --set kserve.controller.deploymentMode=Standard \
  --wait --timeout 180s

# Patch the ingress section of inferenceservice-config. The value is a JSON
# *string*, so a partial `kubectl patch --type merge` on inner keys would
# clobber the rest -- we read the full current JSON, edit only our four keys,
# and write the whole string back. See kserve-ingress-config.md.
python3 - <<'PYEOF'
import json, subprocess

current = subprocess.run(
    ["kubectl", "-n", "kserve", "get", "cm", "inferenceservice-config", "-o", "jsonpath={.data.ingress}"],
    capture_output=True, text=True, check=True,
).stdout
cfg = json.loads(current)
cfg["ingressClassName"] = "nginx"
cfg["ingressDomain"] = "127.0.0.1.sslip.io"
cfg["disableIngressCreation"] = True
cfg["urlScheme"] = "http"

patch = json.dumps({"data": {"ingress": json.dumps(cfg)}})
subprocess.run(
    ["kubectl", "-n", "kserve", "patch", "cm", "inferenceservice-config", "--type", "merge", "-p", patch],
    check=True,
)
print("inferenceservice-config ingress patched")
PYEOF

kubectl -n kserve rollout restart deployment kserve-controller-manager
kubectl -n kserve rollout status deployment kserve-controller-manager --timeout=120s

# The kserve-resources chart ships NO ClusterServingRuntimes (confirmed: no
# "runtime" templates anywhere in the chart) -- they live only in the kserve
# GitHub repo's config/runtimes/*.yaml, applied separately. Without this, every
# InferenceService fails with "no runtime found to support predictor with
# model type: {huggingface <nil>}".
kubectl apply -f "$(dirname "$0")/../manifests/kserve-huggingfaceserver-runtime.yaml"

echo "KServe install complete. Verify:"
echo "  kubectl -n kserve get cm inferenceservice-config -o jsonpath='{.data.deploy}'"
echo "  kubectl -n kserve get cm inferenceservice-config -o jsonpath='{.data.ingress}' | python3 -m json.tool"
