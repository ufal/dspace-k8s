# DSpace 7 Kubernetes Deployment - Copilot Instructions

## Repository Overview

This repository contains Kubernetes manifests for deploying DSpace 7 (an open-source repository platform for digital content) on Rancher Cloud with a split architecture. The deployment uses Kustomize for configuration management and supports multiple environments via overlays.

**Type:** Infrastructure as Code (Kubernetes)  
**Size:** ~15 YAML files, 2 shell scripts (~175 lines total code)  
**Tools:** kubectl, kustomize, envsubst, bash  
**No CI/CD:** This repository has no GitHub Actions workflows or automated testing

## Architecture

### Components
1. **Angular Frontend** (`dspace-angular`) - Port 4000, serves UI at `/`
2. **Backend API** (`dspace-backend`) - Port 8080, serves API at `/server`
3. **Solr Search** (`dspace-solr`) - StatefulSet with persistent storage (5Gi)
4. **PostgreSQL Database** (`dspace-postgres`) - CloudNativePG cluster with 3 replicas (20Gi each)
5. **Ingress** - NGINX with Let's Encrypt TLS, routes to frontend and backend
6. **CronJobs** - 7 scheduled maintenance tasks (index-discovery, health-report, subscriptions, cleanup, etc.)

### Storage
- PostgreSQL: `csi-ceph-rbd-du` (block storage)
- Solr: `csi-ceph-rbd-du` (block storage)
- Assets: `nfs-csi` (shared storage)
- Bitstreams: S3-compatible storage (CESNET S3)

## Project Structure

```
/
├── k8s/                          # Main Kubernetes manifests
│   ├── kustomization.yaml        # Kustomize config (namespace: clarin-dspace-ns)
│   ├── backend-deployment.yaml   # DSpace backend (image: dataquest/dspace:dspace-7_x)
│   ├── angular-deployment.yaml   # Angular frontend (image: dataquest/dspace-angular:dspace-7_x)
│   ├── solr-statefulset.yaml    # Solr search engine
│   ├── postgres-cnpg-cluster.yaml # PostgreSQL cluster (CloudNativePG)
│   ├── dspace-ingress.yaml      # NGINX ingress with TLS
│   ├── dspace-configmap.yaml    # DSpace config (local.cfg, config.yml)
│   ├── dspace-cronjobs.yaml     # Scheduled maintenance jobs
│   ├── sealed-secrets.yaml      # Encrypted secrets (S3, DB passwords)
│   └── *-service.yaml, *-pvc.yaml
├── overlays/
│   └── template/                # Templates for creating new environments
│       ├── kustomization.yaml.template
│       ├── angular-deployment.yaml.template
│       └── backend-deployment.yaml.template
├── init_overlay.sh              # Creates new overlay from templates
├── deploy.bat                   # Windows deployment script
├── kubeconfig.yaml              # Rancher cluster config (gitignored credentials)
└── README.md                    # Comprehensive deployment guide
```

## Required Tools & Versions

- **kubectl**: v1.35+ (tested with v1.35.0)
- **kustomize**: v5.7+ (built into kubectl, or standalone)
- **envsubst**: From GNU gettext package (for overlay creation)
- **bash**: POSIX-compatible shell (for init_overlay.sh)
- **kubeseal**: For creating sealed secrets (optional, only if managing secrets)

## Build & Deployment Commands

### Validation Commands (Always Run First)

Before making changes, validate the Kubernetes manifests:

```bash
# Validate main k8s directory
kubectl kustomize k8s/

# Validate specific overlay
kubectl kustomize overlays/<overlay-name>/

# Dry-run apply (does not create resources)
kubectl apply -k k8s/ --dry-run=client
```

**Important:** These commands do NOT require cluster connectivity and should ALWAYS succeed. If they fail, you have a YAML syntax error or invalid Kustomize configuration.

### Creating a New Overlay (Multi-Environment Support)

To create a deployment for a new environment/namespace:

```bash
# Run the interactive script
./init_overlay.sh

# Prompts:
# 1. Enter namespace: my-namespace
# 2. Enter hostname: dspace.example.com
# 3. Enter overlay name: my-overlay

# Validates automatically and creates overlays/my-overlay/
```

**What it does:**
- Substitutes `${NAMESPACE}`, `${NEW_HOST}`, `${SECRET_NAME}` in templates
- Creates 3 files: `kustomization.yaml`, `angular-deployment.yaml`, `backend-deployment.yaml`
- Uses `envsubst` to replace environment variables

**Common Issues:**
- If overlay directory already exists, script will fail with error
- Requires `envsubst` installed (from gettext package)
- Templates must be in `overlays/template/` directory

### Deployment to Kubernetes

**Windows (using deploy.bat):**
```powershell
# Set kubeconfig
$env:KUBECONFIG = "kubeconfig.yaml"

# Deploy (includes wait for all pods)
.\deploy.bat

# Expected time: 5-8 minutes
```

**Linux/Mac (manual):**
```bash
# Set kubeconfig
export KUBECONFIG=./kubeconfig.yaml

# Deploy with kustomize
kubectl apply -k k8s/
# OR with overlay
kubectl apply -k overlays/my-overlay/

# Wait for components (manual check)
kubectl wait --for=condition=ready pod -l cnpg.io/cluster=dspace-postgres -n clarin-dspace-ns --timeout=300s
kubectl wait --for=condition=ready pod -l app=dspace-solr -n clarin-dspace-ns --timeout=300s
kubectl wait --for=condition=ready pod -l app=dspace-backend -n clarin-dspace-ns --timeout=600s  # Longer for DB init
kubectl wait --for=condition=ready pod -l app=dspace-angular -n clarin-dspace-ns --timeout=300s
```

**Deployment Order (automatic via Kubernetes, no manual ordering needed):**
1. PostgreSQL cluster (takes ~2-3 minutes to bootstrap)
2. Solr (depends on PVC, ~1 minute)
3. Backend (waits for DB readiness, ~3-5 minutes including DB migration)
4. Angular frontend (~1-2 minutes)

### Verification Commands

```bash
# Check all resources
kubectl get all -n clarin-dspace-ns

# Check specific components
kubectl get pods -n clarin-dspace-ns
kubectl get services -n clarin-dspace-ns
kubectl get pvc -n clarin-dspace-ns
kubectl get ingress -n clarin-dspace-ns
kubectl get cronjobs -n clarin-dspace-ns

# Check cluster status
kubectl get cluster.postgresql.cnpg.io -n clarin-dspace-ns

# View logs
kubectl logs -n clarin-dspace-ns -l app=dspace-backend -f
kubectl logs -n clarin-dspace-ns -l app=dspace-angular -f
kubectl logs -n clarin-dspace-ns dspace-postgres-1 -f
kubectl logs -n clarin-dspace-ns dspace-solr-0 -f
```

### Scaling Components

```bash
# Scale frontend
kubectl scale deployment dspace-angular -n clarin-dspace-ns --replicas=3

# Scale backend
kubectl scale deployment dspace-backend -n clarin-dspace-ns --replicas=2

# Scale PostgreSQL (edit yaml and reapply)
# Edit postgres-cnpg-cluster.yaml: instances: 3
kubectl apply -f k8s/postgres-cnpg-cluster.yaml
```

### Updates & Rollouts

```bash
# Apply configuration changes
kubectl apply -k k8s/

# Restart deployments (e.g., after config changes)
kubectl rollout restart deployment/dspace-backend -n clarin-dspace-ns
kubectl rollout restart deployment/dspace-angular -n clarin-dspace-ns

# Check rollout status
kubectl rollout status deployment/dspace-backend -n clarin-dspace-ns
```

## Configuration Files to Modify

When adapting this deployment for a new environment, you MUST update these files:

1. **k8s/kustomization.yaml** - Set `namespace:`
2. **k8s/dspace-ingress.yaml** - Set hostname and TLS secret name
3. **k8s/dspace-configmap.yaml** - Set `dspace.hostname`, `rest.host`, and `proxies.trusted.ipranges`
4. **k8s/sealed-secrets.yaml** - Create secrets for S3 and database
5. **k8s/dspace-cronjobs.yaml** - Set admin email in health-report job (~line 92)
6. **k8s/backend-deployment.yaml** - Set `DSPACE_AUTO_CREATE_ADMIN` to `"false"` for production

**OR** use `init_overlay.sh` to generate an overlay that patches these values automatically.

## Secrets Management

Secrets are stored using Sealed Secrets (encrypted at rest, safe to commit):

```bash
# Example secrets.yaml (DO NOT COMMIT, .gitignored)
---
apiVersion: v1
kind: Secret
metadata:
  name: s3-assetstore-secret
stringData:
  AWS_ACCESS_KEY_ID: "your-key"
  AWS_SECRET_ACCESS_KEY: "your-secret"
  S3_ENDPOINT: "https://s3.cl4.du.cesnet.cz"
  S3_BUCKET_NAME: "bucket-name"
  S3_REGION: "eu-central-1"
---
apiVersion: v1
kind: Secret
metadata:
  name: dspace-postgres-superuser
type: kubernetes.io/basic-auth
stringData:
  username: "dspace"
  password: "secure-password"

# Seal and commit
cd k8s/
kubeseal --controller-namespace sealed-secrets-operator \
  --namespace clarin-dspace-ns \
  --format yaml < secrets.yaml | grep -v namespace > sealed-secrets.yaml
```

## Known Issues & Workarounds

### Issue #1: Handle Server Disabled
**Location:** `k8s/backend-deployment.yaml` line 52  
**TODO:** Handle server is commented out - see https://github.com/ufal/dspace-k8s/issues/25

### Issue #2: Auto-Admin Creation in Production
**Location:** `k8s/backend-deployment.yaml` line 70  
**Warning:** `DSPACE_AUTO_CREATE_ADMIN` is set to `"false"` by default. For testing, set to `"true"` to auto-create admin@admin.sk:admin. For production, ALWAYS keep `"false"` and manually create admin:

```bash
kubectl exec -it <backend-pod> -n clarin-dspace-ns -- \
  /dspace/bin/dspace create-administrator \
  -e your@email.com -f First -l Last -p SecurePass123 -c en
```

### Issue #3: Database Connection Pool Sizing
**Location:** `k8s/dspace-configmap.yaml` line 58  
`db.maxconnections = 100` must be less than PostgreSQL `max_connections: "300"` (set in postgres-cnpg-cluster.yaml)

### Issue #4: Ingress Timeout for Large Uploads
**Location:** `k8s/dspace-ingress.yaml` lines 12-14  
Timeouts are set to 300s. If you experience timeouts with large file uploads, increase:
```yaml
nginx.ingress.kubernetes.io/proxy-read-timeout: "600"
```

### Issue #5: Solr Volume Permissions
**Location:** `k8s/solr-statefulset.yaml` line 19-21  
`fsGroup: 8983` is REQUIRED for Ceph RBD volumes to work. Without it, Solr cannot write to `/var/solr/data`.

## Testing & Validation

**No automated tests exist in this repository.** Manual testing:

1. Deploy to test namespace using overlay
2. Wait for all pods to be Ready (5-8 minutes)
3. Access frontend: `https://<hostname>/`
4. Access backend API: `https://<hostname>/server/api`
5. Check logs for errors: `kubectl logs -n <namespace> -l app=dspace-backend`
6. Verify CronJobs are scheduled: `kubectl get cronjobs -n <namespace>`
7. Manually trigger a job: `kubectl create job --from=cronjob/dspace-index-discovery test-job -n <namespace>`

## Common Commands Reference

```bash
# Delete all resources (DESTRUCTIVE)
kubectl delete -k k8s/ -n clarin-dspace-ns

# Delete PVCs (DELETES DATA PERMANENTLY)
kubectl delete pvc assetstore-pv-claim dspace-postgres-1 solr-data-pvc -n clarin-dspace-ns

# Manual CronJob trigger
kubectl create job --from=cronjob/dspace-index-discovery manual-index -n clarin-dspace-ns

# Connect to PostgreSQL
kubectl exec -it dspace-postgres-1 -n clarin-dspace-ns -- psql -U postgres -d dspace

# Describe resource for debugging
kubectl describe pod <pod-name> -n clarin-dspace-ns
kubectl describe ingress dspace-ingress -n clarin-dspace-ns
```

## Important Notes for Coding Agents

1. **No CI/CD:** This repository has NO GitHub Actions, NO automated tests, NO linting. You cannot run automated validations.

2. **Validation Strategy:** ALWAYS run `kubectl kustomize k8s/` or `kubectl apply --dry-run=client -k k8s/` to validate YAML syntax before committing.

3. **Namespace is hardcoded:** The default namespace `clarin-dspace-ns` appears in many places (README examples, deploy.bat). When creating new environments, use overlays instead of editing base files.

4. **Secrets are gitignored:** Never commit `secrets.yaml` files. Only commit `sealed-secrets.yaml` after encrypting with kubeseal.

5. **No local build/run:** This is pure Kubernetes config. You cannot "run" this locally without a Kubernetes cluster. Use `kubectl kustomize` to validate only.

6. **Windows-specific scripts:** `deploy.bat` is PowerShell/CMD specific. Linux/Mac users should use manual kubectl commands from README.

7. **Image versions:** Container images use `dataquest/dspace:dspace-7_x` and `dataquest/dspace-angular:dspace-7_x` tags. These are NOT in this repository.

8. **Trust these instructions:** Do NOT waste time searching for build scripts, test frameworks, or CI pipelines - they don't exist. Focus on YAML validation only.
