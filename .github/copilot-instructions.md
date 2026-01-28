# DSpace 7 Kubernetes Deployment - Copilot Instructions

## Repository Overview

**Purpose:** Kubernetes manifests for deploying DSpace 7 (digital repository platform) on Rancher Cloud  
**Type:** Infrastructure as Code (Kubernetes/Kustomize)  
**Size:** 15 YAML files, 2 scripts  
**Tools:** kubectl v1.35+, kustomize v5.7+, envsubst (GNU gettext), bash  
**CI/CD:** NONE - No GitHub Actions, no automated tests, no linting  

**Components:**
- Angular Frontend (dspace-angular) → Port 4000 → Ingress path `/`
- Backend API (dspace-backend) → Port 8080 → Ingress path `/server`  
- Solr StatefulSet → 5Gi PVC on csi-ceph-rbd-du
- PostgreSQL CloudNativePG → 3 replicas, 20Gi each on csi-ceph-rbd-du
- NGINX Ingress → Let's Encrypt TLS
- 7 CronJobs for maintenance

## Project Structure

```
k8s/                           # Main manifests (namespace: clarin-dspace-ns)
├── kustomization.yaml         # Defines namespace and resource list
├── backend-deployment.yaml    # DSpace backend (dataquest/dspace:dspace-7_x)
├── angular-deployment.yaml    # Frontend (dataquest/dspace-angular:dspace-7_x)
├── dspace-configmap.yaml      # Config: local.cfg, config.yml (CRITICAL)
├── dspace-ingress.yaml        # Hostname, TLS, routes
├── postgres-cnpg-cluster.yaml # CloudNativePG cluster (3 instances)
├── solr-statefulset.yaml      # Solr with fsGroup: 8983 (REQUIRED)
├── dspace-cronjobs.yaml       # 7 scheduled jobs
├── sealed-secrets.yaml        # Encrypted S3 + DB credentials
└── *-service.yaml, *-pvc.yaml

overlays/template/             # Environment templates (use init_overlay.sh)
init_overlay.sh                # Creates overlays from templates
deploy.bat                     # Windows deployment script
```

## Validation & Deployment

### ALWAYS Validate Before Committing
```bash
# NO cluster required - MUST always succeed
kubectl kustomize k8s/
kubectl kustomize overlays/<name>/

# With cluster (optional)
kubectl apply -k k8s/ --dry-run=client --validate=false
```

### Create New Environment
```bash
./init_overlay.sh
# Prompts: namespace, hostname, overlay-name
# Creates overlays/<name>/ with patches
# Uses envsubst for ${NAMESPACE}, ${NEW_HOST}, ${SECRET_NAME}
```

### Deploy
```bash
# Windows
$env:KUBECONFIG="kubeconfig.yaml"; .\deploy.bat

# Linux/Mac
export KUBECONFIG=./kubeconfig.yaml
kubectl apply -k k8s/  # or overlays/<name>/

# Wait 5-8 minutes for all pods
kubectl wait --for=condition=ready pod -l app=dspace-backend -n clarin-dspace-ns --timeout=600s
```

### Verify
```bash
kubectl get all -n clarin-dspace-ns
kubectl logs -n clarin-dspace-ns -l app=dspace-backend -f
```

## Configuration Checklist

Must update for new deployments (or use init_overlay.sh):

1. **k8s/kustomization.yaml**: `namespace: your-namespace`
2. **k8s/dspace-ingress.yaml**: hostname, TLS secretName  
3. **k8s/dspace-configmap.yaml**: `dspace.hostname`, `rest.host`, `proxies.trusted.ipranges`
4. **k8s/sealed-secrets.yaml**: S3 credentials, DB password (use kubeseal)
5. **k8s/dspace-cronjobs.yaml**: Admin email ~line 92
6. **k8s/backend-deployment.yaml**: `DSPACE_AUTO_CREATE_ADMIN="false"` for production

## Critical Issues & Workarounds

**1. Handle Server Disabled** (backend-deployment.yaml:52)  
   TODO tracked at github.com/ufal/dspace-k8s/issues/25

**2. Admin User Creation**  
   - Testing: Set `DSPACE_AUTO_CREATE_ADMIN="true"` (creates admin@admin.sk:admin)
   - Production: Keep `"false"`, create manually:
   ```bash
   kubectl exec -it <pod> -n <ns> -- /dspace/bin/dspace create-administrator \
     -e your@email.com -f First -l Last -p SecurePass -c en
   ```

**3. DB Connection Pool** (configmap:58)  
   `db.maxconnections=100` < PostgreSQL `max_connections=300` (postgres-cnpg-cluster.yaml)

**4. Solr Volume Permissions** (solr-statefulset.yaml:19-21)  
   `fsGroup: 8983` REQUIRED for Ceph RBD. DO NOT remove.

**5. Ingress Timeouts** (dspace-ingress.yaml:12-14)  
   Default 300s. Increase for large uploads: `proxy-read-timeout: "600"`

## Secrets Management
```bash
# Create secrets.yaml (DO NOT COMMIT - gitignored)
# Seal with kubeseal:
cd k8s/
kubeseal --controller-namespace sealed-secrets-operator \
  --namespace <your-namespace> --format yaml \
  < secrets.yaml | grep -v namespace > sealed-secrets.yaml
# Commit sealed-secrets.yaml only
```

## Common Commands
```bash
# Scale
kubectl scale deployment dspace-angular -n <ns> --replicas=3
kubectl scale deployment dspace-backend -n <ns> --replicas=2

# Update/Restart
kubectl apply -k k8s/
kubectl rollout restart deployment/dspace-backend -n <ns>

# Logs
kubectl logs -n <ns> -l app=dspace-backend -f

# CronJob trigger
kubectl create job --from=cronjob/dspace-index-discovery test -n <ns>

# Connect to DB
kubectl exec -it dspace-postgres-1 -n <ns> -- psql -U postgres -d dspace

# Delete (DESTRUCTIVE)
kubectl delete -k k8s/ -n <ns>
kubectl delete pvc assetstore-pv-claim dspace-postgres-1 solr-data-pvc -n <ns>
```

## Notes for Agents

1. **No CI/CD** - No tests, linting, or automation. Use `kubectl kustomize` only.
2. **Namespace hardcoded** - Use overlays, don't edit base k8s/ files.
3. **No local run** - Pure Kubernetes config, cannot run without cluster.
4. **Secrets gitignored** - Never commit secrets.yaml, only sealed-secrets.yaml.
5. **Windows scripts** - deploy.bat is PowerShell. Linux: use kubectl commands.
6. **Images external** - dataquest/dspace:dspace-7_x not in this repo.
7. **Deployment time** - Backend takes 5-8 min (DB init). Frontend 1-2 min.
8. **Trust instructions** - No build scripts, test frameworks, or CI exist here.

