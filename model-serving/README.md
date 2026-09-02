# "Try me" model-serving PoC — DSpace metadata → KServe on local k3s

A DSpace item carrying `aimodel.*` metadata gets automatically turned into a
live, OpenAI-compatible chat endpoint by a Python reconciler that renders a
KServe `InferenceService` (`isvc`) + nginx `Ingress` from the item's metadata.
This is the L1 "auto-registration" level: no Angular widget, no LiteLLM
gateway, CPU-only with a small model (`Qwen/Qwen2.5-0.5B-Instruct`), KServe
**RawDeployment ("Standard") mode**, weights served from MinIO via
`storageUri: s3://`.

`reconciler.py` is a **standalone Python script**, not an in-cluster
controller — you run it from a shell that has `kubectl` access to the
cluster and network access to the DSpace REST API. Each cycle it reads
desired state (`aimodel.*` metadata via DSpace's Discovery search API),
reads actual state (`kubectl get isvc -l app.kubernetes.io/managed-by=...`),
diffs the two, and `kubectl apply`/`delete`s the difference. `--watch` just
loops that same diff with a sleep in-process; there's no Job/CronJob/Deployment
for it in `manifests/`.

Runs on the existing local k3s deployment (node `ok-dspace`, namespace
`dspace-k3s`), ingress convention `*.127.0.0.1.sslip.io:8080`.

## Architecture

```
DSpace item (aimodel.* metadata)
        |
        |  anonymous GET /api/discover/search/objects?scope=<collection>
        v
  reconciler.py  --once / --watch
        |
        |  kubectl apply/delete (isvc + ingress, from templates.py)
        v
KServe InferenceService (predictor pod: storage-initializer -> huggingfaceserver)
        |
        |  s3://models/<name>  <-- MinIO (uploaded once via model-upload-job.yaml)
        v
nginx Ingress  model-<uuid[:8]>.127.0.0.1.sslip.io:8080
        |
        v
curl .../openai/v1/chat/completions  -->  real chat completion
```

Conventions: everything in namespace `dspace-k3s`; isvc/ingress name
`model-<uuid[:8]>`; labels `app.kubernetes.io/managed-by=dspace-reconciler` +
`dspace.item-uuid=<uuid>`. **KServe's own ingress creation is disabled**
(`disableIngressCreation: true`) — we ship our own nginx `Ingress` per model.
By default KServe auto-generates an `Ingress`/`VirtualService` per isvc from
`ingressDomain`/`domainTemplate`, assuming an Istio+Knative stack; disabling
that and hand-writing the `Ingress` instead is version-proof, matches every
other service in this repo, and is debuggable with plain `kubectl describe
ingress`. See `install/kserve-ingress-config.md`.

## `aimodel.*` metadata contract

Schema `aimodel`, namespace `http://dspace.org/namespace/aimodel/`. All
fields unqualified (no qualifier).

| field | example | meaning |
|---|---|---|
| `aimodel.serveable` | `true` / `false` | gate: reconciler only creates an endpoint when `true` |
| `aimodel.task` | `chat` | reconciler currently only serves `task=="chat"` items |
| `aimodel.format` | `huggingface` | KServe `modelFormat.name` |
| `aimodel.storageuri` | `s3://models/qwen2.5-0.5b-instruct` | KServe `storageUri`; validated against `^s3://[a-z0-9./_-]+$` before use |
| `aimodel.backend` | `huggingface` | passed as `--backend=<value>` to the huggingfaceserver container |

## Runbook

### Phase 1 — Preflight

```bash
kubectl get nodes                                    # Ready
kubectl -n dspace-k3s get pods | grep -E 'backend|minio'   # Running
kubectl -n ingress-nginx get svc                      # LB on 127.0.0.1:8080
free -m                                                # available >= 10000 MB
df -h /                                                # >= 20G free
curl -so /dev/null -w '%{http_code}\n' http://dspace.127.0.0.1.sslip.io:8080/server/api  # 200
kubectl get crd | grep -icE 'cert-manager|kserve'      # 0 on a fresh install
```

Pre-pull the (multi-GB) serving image before installing KServe, so Phase 4
doesn't stall on a first pull:

```bash
kubectl -n dspace-k3s run prepull-hfserver --image=kserve/huggingfaceserver:v0.19.0 \
  --restart=Never --command -- sleep 5
# wait for Completed, then:
kubectl -n dspace-k3s delete pod prepull-hfserver
```

### Phase 2 — cert-manager + KServe (Standard mode) + ingress wiring

```bash
install/install-kserve.sh
```

This script (idempotent, `helm upgrade --install` throughout):
1. Installs cert-manager (`jetstack/cert-manager`) — automates issuing/
   rotating the TLS cert KServe's admission webhooks need to be called by the
   API server; unrelated to model-traffic TLS (ingress here is plain HTTP).
2. Installs `kserve-crd` and `kserve-resources` (both OCI charts, pinned
   `v0.19.0`), with `kserve.controller.deploymentMode=Standard`.
3. Patches the `inferenceservice-config` configmap's `ingress` JSON (see
   `install/kserve-ingress-config.md` for the exact diff and why) and
   restarts the controller.
4. Applies `manifests/kserve-huggingfaceserver-runtime.yaml` — **required**,
   see "Gotcha: no default runtimes" below.

Verify:
```bash
kubectl -n kserve get cm inferenceservice-config -o jsonpath='{.data.deploy}'
# {"defaultDeploymentMode":"Standard"}
kubectl get clusterservingruntime
# kserve-huggingfaceserver   ...   huggingface   kserve-container
```

### Phase 3 — Weights into MinIO + S3 creds

```bash
kubectl apply -f manifests/minio-models-bucket-job.yaml
kubectl -n dspace-k3s wait --for=condition=complete job/minio-create-models-bucket --timeout=120s

kubectl apply -f manifests/model-upload-job.yaml
# watch: kubectl -n dspace-k3s logs -l job-name=model-upload-qwen -f
# ends with "UPLOAD DONE" (~1GB download+upload, a few minutes)

kubectl apply -f manifests/s3-secret-sa.yaml
```

Verify bucket contents:
```bash
kubectl -n dspace-k3s run mc-ls --rm -i --restart=Never --image=minio/mc:latest \
  --command -- sh -c 'mc alias set m http://minio-service:9000 minioadmin minioadmin && \
  mc ls -r m/models/qwen2.5-0.5b-instruct/'
# config.json, model.safetensors (~940MB), tokenizer.json, tokenizer_config.json,
# generation_config.json, vocab.json, merges.txt
```

### Phase 4 — Smoke InferenceService (the milestone)

```bash
kubectl apply -f manifests/smoke/inferenceservice.yaml
# staged verify:
kubectl -n dspace-k3s logs <pod> -c storage-initializer   # downloads from s3://
kubectl -n dspace-k3s logs <pod> -c kserve-container -f   # uvicorn on 8080
kubectl -n dspace-k3s get svc | grep smoke-qwen            # smoke-qwen-predictor:80
kubectl run curl-test --rm -i --restart=Never --image=curlimages/curl -n dspace-k3s \
  -- -s http://smoke-qwen-predictor/openai/v1/models      # {"id":"smoke-qwen",...}

kubectl apply -f manifests/smoke/ingress.yaml

curl -s --max-time 300 http://smoke-qwen.127.0.0.1.sslip.io:8080/openai/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"smoke-qwen","messages":[{"role":"user","content":"Say hello in one short sentence."}],"max_tokens":40}'
# 200, choices[0].message.content non-empty, usage.completion_tokens > 0
```

On this cluster (image pre-pulled, model already small) this took **well
under a minute** end to end, not the 2–5 min budgeted.

Delete the smoke resources once the reconciler (Phase 6) is proven, or
earlier if you're CPU-constrained (see below).

### Phase 5 — DSpace: `aimodel` schema + demo item

```bash
export DSPACE_USER=$(kubectl -n dspace-k3s get secret dspace-admin-secret -o jsonpath='{.data.username}' | base64 -d)
export DSPACE_PASS=$(kubectl -n dspace-k3s get secret dspace-admin-secret -o jsonpath='{.data.password}' | base64 -d)
cd reconciler
python3 setup_dspace.py schema
python3 setup_dspace.py seed-item     # prints COLLECTION <uuid> and ITEM <uuid>
```

Verify (anonymous):
```bash
curl -s '.../api/core/metadatafields/search/byFieldName?schema=aimodel&size=20' | python3 -m json.tool | grep '"element"'
curl -s '.../api/core/items/<item-uuid>' | python3 -m json.tool | grep 'aimodel\.'
```

Note: `ensure_license()`'s license-*create* branch (as opposed to reusing an
existing one) never ran on this cluster -- a CLARIN license already existed
from prior testing, so `seed-item` always took the reuse path. The create
branch is a direct line-for-line port of `dspace-e2e/seed-archived-item.js`'s
proven `ensureLicense()`, and the adjacent label-create-on-conflict fallback
*was* exercised live (confirmed a `400 "...already exists"` correctly falls
through to the lookup), but the license-create POST body itself is unverified
on a truly empty system.

### Phase 6 — Reconciler

Each huggingfaceserver predictor requests 2 CPU (limit 4). If the Phase 4
smoke isvc is still running, delete it first — running two predictors at
once can exceed this node's allocatable CPU even though RAM is fine
(`1 Insufficient cpu`, pod stuck `Pending`; see troubleshooting table):

```bash
kubectl -n dspace-k3s delete -f manifests/smoke/
```

```bash
python3 reconciler.py --collection <collection-uuid> --once     # CREATE model-<8hex>
# if this prints "OK (no change)" instead of CREATE right after seed-item,
# Discovery/Solr hadn't indexed the item yet -- fetch_desired retries once
# after 30s internally; a manual re-run after ~30s also works.
python3 reconciler.py --collection <collection-uuid> --once     # OK (no change)

python3 setup_dspace.py mark --uuid <item-uuid> --serveable false
python3 reconciler.py --collection <collection-uuid> --once     # DELETE model-<8hex>

python3 setup_dspace.py mark --uuid <item-uuid> --serveable true
python3 reconciler.py --collection <collection-uuid> --once     # CREATE model-<8hex> (final state: serving)
```

`--watch --interval 60` runs the same reconcile loop continuously instead of
once. `--dry-run` prints the planned CREATE/DELETE lines without touching the
cluster.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `no runtime found to support predictor with model type: {huggingface <nil>}` | The `kserve-resources` Helm chart ships **no** `ClusterServingRuntime` at all (confirmed: no runtime templates in the chart). Apply `manifests/kserve-huggingfaceserver-runtime.yaml` (sourced from `kserve/kserve` GitHub `config/runtimes/kserve-huggingfaceserver.yaml` at the matching tag, image pinned instead of the `kustomization.yaml` `:latest` substitution). If the isvc was created *before* the runtime existed, `kubectl delete isvc <name> && kubectl apply -f <isvc.yaml>` — the controller doesn't always re-reconcile an existing `Unknown`-status isvc against a newly-created runtime on its own. |
| `1 Insufficient cpu` / predictor pod stuck `Pending` | Each huggingfaceserver predictor requests 2 CPU (limit 4). Two of them (e.g. `smoke-qwen` + a reconciler-created isvc) can exceed the node's allocatable CPU even though RAM is fine. `kubectl -n dspace-k3s delete -f manifests/smoke/` before running the reconciler's create-path test. |
| DSpace metadata `PATCH` returns generic 422 `Unprocessable or invalid entity` with no detail | This fork's submission-workspaceitem PATCH endpoint appears to accept-and-silently-apply individually-valid ops, but returns 422 for the whole batch if patching **two different sections in one request** leaves either section in an invalid state (e.g. a conditionally-required field newly triggered). Fix: one `PATCH` call **per section** (see `setup_dspace.py`'s `deposit_item`), not one big batch across sections. |
| `dc.type` value rejected | `traditionalpageone`'s `dc.type` is a **closed** controlled vocabulary (`common_types`: `corpus`, `lexicalConceptualResource`, `languageDescription`, `toolService`) — free text like `"text"` (used by `dspace-e2e/seed-archived-item.js`, which targets a different, non-CLARIN-extended collection) is rejected. We use `toolService`. |
| Setting `dc.type=toolService` triggers new required-field errors on `traditionalpagetwo` | CLARIN/META-SHARE conditional fields: `metashare.ResourceInfo#ContentInfo.detailedType` (closed vocab `metashare_detailed_toolService`) and `...ToolServiceInfo.languageDependent` (closed vocab `metashare_languageDependent`, `true`/`false`) become mandatory. `setup_dspace.py` sets both. |
| Clarin-license select via `PATCH .../sections/clarin-license/select` 422s | That's the path the Angular component (`section-license.component.ts`) and `dspace-e2e/seed-archived-item.js` use, but **this backend's routing doesn't accept it** — `ClarinLicenseResourceStep.doPatchProcessing` only handles paths ending in `granted`. The actual endpoint is the top-level `PATCH .../workspaceitems/{id}` op `{"path":"/license","value":"<license name>"}`, routed through `WorkspaceItemRestRepository`'s `license` handler (`ClarinLicenseUtils.updateLicenseForItem`). Confirmed live against this deployment; may be backend-version-specific drift from the Angular/e2e reference. |
| `Warning VirtualServiceCRDNotFound` events on every isvc | Benign — KServe tries Istio VirtualService reconciliation regardless of ingress mode; the Istio CRD isn't installed (we use nginx). Silence by setting `ingress.disableIstioVirtualHost=true` in the same configmap patch if the noise bothers you; does not block `Ready`. |
| CPU first-token latency / hung curls | Use `--max-time 300` client-side and `proxy-read-timeout`/`proxy-send-timeout: "600"` + `proxy-buffering: "off"` on the Ingress (already in `templates.py`/`manifests/smoke/ingress.yaml`). In practice, on this 8-core node with the small model, responses came back in a few seconds, not minutes. |
| storage-initializer can't reach MinIO | The `serving.kserve.io/s3-endpoint` annotation must be bare `host:port`, **no scheme**; `s3-usehttps`/`s3-verifyssl` go on the **Secret**, not the ServiceAccount. |

## GPU migration

Everything above runs CPU-only. To move to GPU infra, change exactly:

1. **Add `nvidia.com/gpu: "1"`** to the predictor's `resources.limits` (in
   `templates.py`'s `inference_service_yaml` and/or the smoke manifest).
2. **Remove `--backend=huggingface`** from the container args — with a GPU
   present, KServe's huggingfaceserver auto-selects the vLLM backend instead
   of forcing the plain-transformers backend.
3. Optionally add `--dtype=bfloat16` and move to a bigger model.

**How the `-gpu` image gets selected** (verified in KServe controller source,
`pkg/controller/v1beta1/inferenceservice/utils/utils.go`, `UpdateImageTag`):
when no explicit `runtimeVersion` is set and the predictor container's
resources indicate a GPU (`utils.IsGPUEnabled`), and the serving runtime is
one of `TFServing`/`TorchServe`/`HuggingFaceServer`, the controller
automatically appends `-gpu` to the image tag it read off the
`ClusterServingRuntime` — i.e. `kserve/huggingfaceserver:v0.19.0` becomes
`kserve/huggingfaceserver:v0.19.0-gpu` at admission time, with **no manifest
change needed** to the `ClusterServingRuntime` itself beyond step 1 above.

**Scale-to-zero** (Knative, or KEDA on KServe ≥0.18) is a GPU-infra follow-up
only — not implemented here; RawDeployment/Standard mode has no native
scale-to-zero.

**Multiple models on one GPU node:** each isvc is still one dedicated pod
(KServe's default 1:1 model-to-pod), and by default a k8s node exposes one
physical GPU as one indivisible `nvidia.com/gpu` unit — a second isvc
requesting `nvidia.com/gpu: "1"` on an already-claimed card sticks `Pending`,
same as the CPU case above. Options, not implemented here:
- **Multiple physical GPUs** — no config change beyond step 1 above.
- **NVIDIA MIG** (Ampere/Hopper only) — hardware-partitions one GPU into
  isolated instances with real memory/fault isolation, fixed partition shapes.
- **GPU time-slicing** (NVIDIA device plugin config) — one GPU advertised as
  N virtual `nvidia.com/gpu` replicas; no memory/fault isolation between
  co-scheduled pods, context-switch overhead, scheduler can't see actual VRAM
  use so a real CUDA OOM is still possible despite "available" resources.
- **KServe ModelMesh** — a different KServe deployment mode purpose-built for
  many-models-few-GPUs: dynamically loads/evicts distinct, unrelated models
  into a shared pool of serving pods, instead of one pod per model.
- **vLLM multi-LoRA** (`--enable-lora`) — not a ModelMesh alternative, a
  different axis: many LoRA adapters share one resident base model in one
  vLLM process. Only applies when the "models" are fine-tunes of the *same*
  base weights, which the current `aimodel.*` schema has no way to express
  (`aimodel.storageuri` is one full model per item, no adapter/base linkage).

## `--watch` usage

```bash
python3 reconciler.py --collection <collection-uuid> --watch --interval 60
```
Runs the same create/delete diff every 60s until killed. Combine with
`setup_dspace.py mark` in another shell to see live create/delete cycles.

## Full cleanup

```bash
kubectl -n dspace-k3s delete isvc,ingress -l app.kubernetes.io/managed-by=dspace-reconciler
kubectl -n dspace-k3s delete -f manifests/smoke/ --ignore-not-found
kubectl delete -f manifests/kserve-huggingfaceserver-runtime.yaml
kubectl -n dspace-k3s delete -f manifests/s3-secret-sa.yaml
kubectl -n dspace-k3s delete job minio-create-models-bucket model-upload-qwen --ignore-not-found
kubectl -n dspace-k3s delete configmap model-upload-script --ignore-not-found
# leaves the "models" MinIO bucket and its contents; delete manually if desired

helm uninstall kserve -n kserve
helm uninstall kserve-crd -n kserve
helm uninstall cert-manager -n cert-manager
kubectl delete namespace kserve cert-manager
```

The `aimodel` metadata schema and the demo item/collection/community in
DSpace are left in place (metadata schemas aren't typically torn down; the
demo item is harmless and can be deleted via the admin UI if desired).
