# k3s-rootless overlay

Local, single-node DSpace 7 dev on a **rootless k3s** cluster (a Linux box
without Docker Desktop and with **no root** available — not even `sudo`).

It layers on [`../docker-desktop`](../docker-desktop) and changes only what
rootless k3s requires:

| Delta vs docker-desktop | Why |
|-------------------------|-----|
| `storageClassName: hostpath` → `local-path` | k3s ships `local-path-provisioner`; the `hostpath` class is a Docker Desktop thing and does not exist on k3s. PVCs would hang `Pending`. |
| namespace `dspace-k3s` | Coexist with / not clobber a docker-desktop deploy. |
| URLs carry `:8080` (`local.cfg`, `config.yml`) | Rootless processes can't bind privileged ports, so the ingress is exposed on host **8080**, not 80. DSpace puts the port in redirects + CORS origins, so it must be explicit. |
| host `*.localhost` → `*.127.0.0.1.sslip.io` (`local.cfg`, `config.yml`, ingress patches) | `*.localhost` would need an `/etc/hosts` entry (root). [sslip.io](https://sslip.io) resolves `dspace.127.0.0.1.sslip.io` → `127.0.0.1` via public DNS, so no hosts file and no root. |

**Rootless for daily use, but needs a one-time root bootstrap.** This overlay
removes the root dependencies it *can* (host port 8080 instead of 80, sslip.io
instead of `/etc/hosts`), but a stock hardened Ubuntu 24.04 host still needs a
few one-time root toggles to *permit* rootless containers at all. After step 0,
nothing else needs root, and you visit the app at
**<http://dspace.127.0.0.1.sslip.io:8080>** (only requirement: the browser's
machine can do public DNS lookups).

## Cluster prerequisites

### 0. One-time root bootstrap (host enablement)

Rootless k3s needs three things a minimal/hardened Ubuntu 24.04 box doesn't
give an unprivileged user by default. None can be set rootless; do them once:

| What | Why | Symptom if missing |
|------|-----|--------------------|
| `uidmap`, `slirp4netns`, `fuse-overlayfs` pkgs | setuid `newuidmap`/`newgidmap` map the subuid range; userspace netns + snapshotter | rootless containers collapse to a single UID |
| `net.ipv4.ip_forward=1` | k8s pod/service routing; k3s preflight checks it | `FATA expected sysctl ... net.ipv4.ip_forward to be 1, got 0` |
| `kernel.apparmor_restrict_unprivileged_userns=0` | Ubuntu 24.04 AppArmor blocks unprivileged user-namespace creation; rootlesskit needs it | `FATA failed to start the child: fork/exec /proc/self/exe: operation not permitted` |
| `cpuset` (+`io`) cgroup-v2 delegation to the user slice | default user-slice delegation is only `cpu memory pids`; kubelet needs `cpuset` | `FATA Error: failed to find cpuset cgroup (v2)` |

```bash
sudo sh -c '
  apt-get install -y uidmap slirp4netns fuse-overlayfs

  cat > /etc/sysctl.d/99-k3s-rootless.conf <<EOF
net.ipv4.ip_forward=1
kernel.apparmor_restrict_unprivileged_userns=0
EOF
  sysctl --system

  mkdir -p /etc/systemd/system/user@.service.d
  cat > /etc/systemd/system/user@.service.d/delegate.conf <<EOF
[Service]
Delegate=cpu cpuset io memory pids
EOF
  systemctl daemon-reload'
```

The cgroup delegation only applies after the **user manager restarts** — log
out and back in, or `sudo systemctl restart user@$(id -u).service` (this ends
your current session's user processes; reconnect after).

Verify (all no-root):
```bash
command -v newuidmap
cat /proc/sys/net/ipv4/ip_forward                              # 1
cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns     # 0
cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers  # includes cpuset
```

Everything from step 1 on is no-root.

### 1. Run rootless k3s with Traefik disabled

The manifests are nginx-ingress-specific: `ingressClassName: nginx` plus
`nginx.ingress.kubernetes.io/*` annotations (notably `proxy-body-size: 10g`
for bitstream uploads, buffering-off + 6000s timeouts for long REST/SSE
responses). k3s's bundled **Traefik ignores all of these and won't claim
class `nginx`**, so the Ingress would never be served.

> **Do NOT use the `get.k3s.io` install script** — it always needs root (it
> writes to `/usr/local/bin` and installs a systemd unit; `--rootless` is a
> *runtime* flag, not an installer mode, so the script still prompts for sudo).
> Download the binary into `$HOME` and run it directly instead.

```bash
# 1. Download the k3s binary into a user-writable dir (no root, no script)
mkdir -p ~/.local/bin
curl -sfL https://github.com/k3s-io/k3s/releases/latest/download/k3s \
  -o ~/.local/bin/k3s && chmod +x ~/.local/bin/k3s
export PATH="$HOME/.local/bin:$PATH"

# 2. Start rootless k3s INSIDE the user manager's delegated cgroup tree.
#    Running `k3s` bare from a login/SSH shell puts it in session-N.scope,
#    which does NOT inherit the cpuset delegation (that lives under
#    user@UID.service) -> `FATA failed to find cpuset cgroup (v2)`.
#    `systemd-run --user --scope` launches it under user@UID.service/app.slice
#    where cpuset/cpu/io/memory/pids are delegated. (re-execs into a
#    user+net namespace via built-in rootlesskit.)
systemd-run --user --scope -p Delegate=yes -- \
  k3s server --rootless --disable=traefik --snapshotter=fuse-overlayfs \
  --write-kubeconfig "$HOME/.kube/config" --write-kubeconfig-mode 600

# For a persistent setup, install k3s's ~/.config/systemd/user/k3s-rootless.service
# unit instead and `systemctl --user start k3s-rootless` — user units already
# run under user@UID.service, so they get the delegation automatically.

# 3. In another shell:
export KUBECONFIG="$HOME/.kube/config"
```

### 2. Install ingress-nginx, exposed on host port 8080

ingress-nginx as a `LoadBalancer` Service; k3s's servicelb + the rootless
port-forwarder publish the Service's ports on the host. Setting the Service
ports to **8080 / 8443** (both > 1024) means **no privileged bind, no sysctl**:

```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm install ingress-nginx ingress-nginx/ingress-nginx \
  -n ingress-nginx --create-namespace \
  --set controller.service.ports.http=8080 \
  --set controller.service.ports.https=8443
```

Host 8080 then routes by Host header to both `dspace.127.0.0.1.sslip.io` and
`minio.127.0.0.1.sslip.io`. (If 8080 is already taken on the host, pick
another high port and change it in **three** places consistently: this helm
`--set`, and the `:8080` in `local.cfg` + `config.yml`.)

### 2b. CoreDNS rewrite so SSR can reach the backend

Angular's server-side renderer runs inside the pod and calls the public REST
URL; sslip.io resolves it to `127.0.0.1` (the pod's own loopback), so SSR
can't reach the API and the UI returns **HTTP 500** (`undefined doesn't
contain the link authn`). Browsers are unaffected (client-side rendering still
works), but to get clean 200s, rewrite the hostname in-cluster to the backend
Service:

```bash
kubectl apply -f overlays/k3s-rootless/coredns-ssr-rewrite.yaml
kubectl -n kube-system rollout restart deployment/coredns
kubectl -n dspace-k3s rollout restart deployment/dspace-angular   # clear cached SSR failure
```

### 3. DNS — nothing else to do

`*.127.0.0.1.sslip.io` resolves to `127.0.0.1` via sslip.io's public
resolver. No `/etc/hosts`, no root. (If the box is firewalled off from public
DNS, fall back to an `/etc/hosts` entry — but that needs root.)

## Deploy

```bash
kubectl apply -k overlays/k3s-rootless/

# First boot runs DB migrations + discovery indexing — 5–8 min.
kubectl wait --for=condition=ready pod -l app=dspace-backend \
  -n dspace-k3s --timeout=600s
```

Then open <http://dspace.127.0.0.1.sslip.io:8080> (UI); `/server` routes to
the backend, MinIO console at <http://minio.127.0.0.1.sslip.io:8080>.

> **SSR note:** Angular SSR (`config.yml` `rest.host`) points at
> `dspace.127.0.0.1.sslip.io:8080`. If the SSR pod can't reach that URL
> in-cluster, the UI falls back to client-side rendering in the browser
> (which resolves it fine) — this mirrors inherited docker-desktop behavior,
> not specific to this overlay.

## Validate without a cluster

```bash
kubectl kustomize overlays/k3s-rootless/
```
