# ok-dspace reconciler

A CronJob inside the `kosarko-ns` namespace that pulls `ufal/dspace-k8s` `main` every five minutes
and applies `overlays/kosarko-ns` to the ok-dspace test environment
(`https://ok-dspace.dyn.cloud.e-infra.cz`).

It is one half of a deliberate split:

| Where | What | Credentials it holds |
|---|---|---|
| GitHub, `ubuntu-latest` | [`pin-ok-dspace.yml`](../.github/workflows/pin-ok-dspace.yml) resolves an image tag, checks it exists on Docker Hub, writes it to `main` | `GITHUB_TOKEN` only |
| Cluster, this CronJob | notices the commit and applies it | its own ServiceAccount token |
| GitHub, `ubuntu-latest` | [`verify-ok-dspace.yml`](../.github/workflows/verify-ok-dspace.yml) polls the public URL until the new commit is served | none |

**No cluster credential exists in GitHub, and no GitHub credential exists in the cluster.** The
repository is public, so the reconciler fetches it anonymously; the apply uses the pod's projected
`dspace-deployer` token, which the kubelet rotates.

## Why pull rather than push

The obvious alternative — a CI job that runs `kubectl apply` — needs a cluster credential wherever it
runs. Putting it on a GitHub-hosted runner means storing a Rancher token as a repository secret: it
expires, and it is a standing credential in a system you do not control. Putting it on a
**self-hosted runner in this namespace** avoids that, but a self-hosted runner attached to a *public*
repository is a known footgun — it executes whatever the workflow says on hardware holding namespace
credentials, and it needs a GitHub PAT with `Administration: write` in-cluster just to register
itself. This environment was built that way first and then replaced; see HANDOFF for the full
reasoning.

Pulling removes the whole class of problem. Nothing outside the cluster can execute code inside it;
the only inputs are the contents of `main` and the pinned reconciler image.

Two further consequences worth knowing:

- **Drift is self-correcting.** A hand-patched Deployment is reverted within one interval. The
  push-based design only applied on dispatch, so hand edits survived indefinitely.
- **The dangerous failure ordering is gone.** Tags are committed *before* anything reaches the
  cluster, so the worst case is "git ahead of cluster", which the next tick closes by itself. The
  push-based design committed only after a green rollout, so a failed push left the *cluster* ahead
  of git — the state that lets the next deploy of the other component silently roll it back.

## What gets created

| Resource | Name | Purpose |
|---|---|---|
| ServiceAccount | `dspace-deployer` | identity the reconciler uses against the API server |
| Role | `dspace-deployer` | tightly scoped deploy permissions (see below) |
| RoleBinding | `dspace-deployer` | binds the two |
| CronJob | `ok-dspace-reconcile` | fetch `main`, `kubectl apply -k`, wait for rollout |

## Bootstrap

Prerequisites were confirmed for `kosarko-ns` and only need re-checking somewhere new: `create` on
serviceaccounts/roles/rolebindings, quota headroom, no NetworkPolicy, egress to GitHub.

```bash
# Guard: this MUST print https://rancher.cloud.e-infra.cz/...
kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}'

kubectl apply -k ci-reconcile/

# Run it once immediately rather than waiting for the next tick.
kubectl create job -n kosarko-ns --from=cronjob/ok-dspace-reconcile ok-dspace-reconcile-bootstrap
kubectl logs -n kosarko-ns -l app=ok-dspace-reconcile --tail=50 -f
```

Then confirm the permission boundary is what it claims:

```bash
kubectl auth can-i --as=system:serviceaccount:kosarko-ns:dspace-deployer \
  -n kosarko-ns patch deployments     # yes
kubectl auth can-i --as=system:serviceaccount:kosarko-ns:dspace-deployer \
  -n kosarko-ns create deployments    # no
kubectl auth can-i --as=system:serviceaccount:kosarko-ns:dspace-deployer \
  -n kosarko-ns delete deployments    # no
kubectl auth can-i --as=system:serviceaccount:kosarko-ns:dspace-deployer \
  -n kosarko-ns get secrets           # no
```

There is **no secret to create and no PAT to mint** — that is the point.

### Keeping the Role in step with the overlay

The Role is scoped to the *kinds the overlay actually contains*, so **adding a new kind to
`overlays/kosarko-ns` breaks the reconcile on every tick until the Role is extended.** The failure is
quiet from GitHub's side: the job fails, nothing is applied, and `verify` simply times out. Check
coverage after changing the overlay:

```bash
kubectl kustomize overlays/kosarko-ns | grep -E '^(apiVersion|kind):'
```

Every (apiGroup, kind) pair must have a matching rule in `role.yaml`. Verified for the current
overlay — ConfigMap, PersistentVolumeClaim, Service, Deployment, StatefulSet, CronJob, Ingress, CNPG
Cluster + ScheduledBackup, barman-cloud ObjectStore, SealedSecret — all covered, with no
cluster-scoped objects (a namespaced Role could never authorize a `Namespace`) and no raw `Secret`.

## Permissions

`role.yaml` grants exactly what `kubectl apply -k overlays/kosarko-ns` plus `kubectl rollout status`
need, and nothing else.

### What RBAC here cannot do

The reconciler needs `patch`/`update` on Deployments and StatefulSets — that is how an image tag
change is applied — and **patching a workload rewrites its pod template.** So anything holding this
token can run an arbitrary image, and mount any Secret in the namespace into it, by patching an
existing Deployment. It never needs `create`. PodSecurity `restricted` does not help either: a
compliant pod still mounts Secrets and still runs any non-root image.

**RBAC therefore cannot bound whatever holds this token.** What makes that acceptable here is that
nothing arbitrary runs in this pod — the only inputs are `main` and the pinned image. Protect those
two and the Role never has to be the last line of defence. Bounding it properly would take a
ValidatingAdmissionPolicy restricting this ServiceAccount's Deployment patches to the image field.

### What it does buy

- **No `delete` on anything.** The deploy path cannot destroy the environment or its PVCs. This holds
  even against a compromised reconciler. A reset/wipe capability is a conscious, separate grant.
- **No `resourcequotas`, `roles` or `rolebindings`.** Blast radius stays pinned inside `kosarko-ns`.
- **No `create` on pod-spawning controllers** — least-privilege hygiene, since steady-state apply
  does not need it. **Not** a barrier to arbitrary workloads; see above.
- **No `secrets` access.** Stops API reads; does not stop a mount.

*Known costs:* with no `delete`, the hashed ConfigMaps from `configMapGenerator` accumulate — prune
occasionally. With no `create` on workloads, a **brand-new** Deployment/StatefulSet/CronJob/Cluster
in the overlay makes the reconcile fail; apply that one by hand once, and it is maintained from then
on.

## Operating it

**Deploy now instead of waiting for the tick:**

```bash
kubectl create job -n kosarko-ns --from=cronjob/ok-dspace-reconcile \
  ok-dspace-reconcile-$(date +%s)
```

**Pause reconciliation** — required if you want hand edits to survive, e.g. while debugging.
Otherwise they are reverted within five minutes:

```bash
kubectl patch cronjob/ok-dspace-reconcile -n kosarko-ns -p '{"spec":{"suspend":true}}'
# ... and afterwards, do not forget:
kubectl patch cronjob/ok-dspace-reconcile -n kosarko-ns -p '{"spec":{"suspend":false}}'
```

**Force a restart at the same version.** `apply` is a no-op when nothing changed, so re-pinning an
identical tag restarts nothing:

```bash
kubectl rollout restart deployment/dspace-backend -n kosarko-ns
```

**See what it did:**

```bash
kubectl get jobs -n kosarko-ns -l app=ok-dspace-reconcile
kubectl logs -n kosarko-ns -l app=ok-dspace-reconcile --tail=50
```

**Deploy by hand** (the reconciler is not required for this):

```bash
kubectl apply -k overlays/kosarko-ns
kubectl rollout status deployment/dspace-backend -n kosarko-ns --timeout=20m
```

A manual apply does not update the pinned tags in `overlays/kosarko-ns/kustomization.yaml` — and the
next tick reverts anything that contradicts `main`.

## Reading the logs: `configured` does not mean something changed

Every reconcile reports `configured` for the two Deployments, the StatefulSet, the nine app CronJobs
and the CNPG Cluster — thirteen objects — even when nothing has changed. **This is cosmetic. Do not
go looking for drift.**

Kubernetes resource quantities are strings, but the manifests in `k8s/` write `cpu: 1` as a YAML
*integer*. That lands in `last-applied-configuration` as `1` while the API server stores the
canonical `"1"`, so `kubectl apply`'s three-way merge computes a non-empty patch every time. The
server parses both to the same Quantity, treats it as a no-op, and does not even bump
`resourceVersion`. Only the thirteen objects carrying a pod template are affected; Services, PVCs,
Ingress, SealedSecrets and the ObjectStore all report `unchanged`.

The tell that it is harmless: `kubectl diff -k overlays/kosarko-ns` reports **no differences** at the
same moment, and `metadata.generation` does not move. If you ever need to confirm that on a live
object:

```bash
kubectl get deploy/dspace-backend -n kosarko-ns -o jsonpath='{.metadata.generation}{"\n"}'
```

Stable generation across ticks means no spec is being rewritten. The fix, if the log noise ever
matters enough — it costs you the ability to tell a real deploy from a no-op tick by reading the
logs — is to quote the quantities in `k8s/` (`cpu: "1"`). That is a change to the base manifests,
not to this directory.

## Failure modes

**A tag that does not exist does not take the site down.** The reconciler applies it, the new pods
sit in `ImagePullBackOff`, and RollingUpdate keeps the old ReplicaSet serving — so the damage is "no
new version", not "broken environment". `rollout status` fails, the reconcile job fails, and
`verify` goes red. The pin workflow's Docker Hub check catches most of these ~30 minutes earlier,
but it is a convenience, not the guarantee.

**A manifest the Role cannot apply** fails the whole `kubectl apply -k`, every tick, so *nothing*
in the overlay updates — not just the offending object. See "Keeping the Role in step with the
overlay" above.

### Known gap: failures between pins are invisible

`verify-ok-dspace.yml` catches a broken deploy, but it only runs when something is *pinned*. If the
reconciler starts failing on its own — the tarball fetch breaks, `main` acquires a manifest the Role
cannot apply, the image tag falls out of kubectl's supported skew — **nothing announces it.** The
environment simply stops tracking `main`, and the next pin is the first thing to notice, up to 35
minutes later.

`failedJobsHistoryLimit: 3` keeps the evidence, but it is a place to look, not a signal. Until
something watches it, `kubectl get jobs -n kosarko-ns -l app=ok-dspace-reconcile` is the manual
check. This is the one thing a real GitOps controller (Flux, Argo) would give you for free, and the
reason to revisit that choice if this environment ever matters more than it does today.

## Maintenance

**Bump `alpine/k8s`** in `cronjob.yaml` periodically. kubectl must stay within one minor of the API
server: patch bumps are safe, a minor bump wants the cluster version checked first
(`kubectl version`).

**There is no credential to rotate.**
