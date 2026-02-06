# DSpace 7 – Kubernetes Deployment for Rancher Cloud

This directory contains a DSpace 7 Kubernetes deployment configuration for Rancher Cloud with a scalable split architecture.

## Current Architecture

Split architecture with independent, scalable components:

1. **Angular Frontend** (`dspace-angular` Deployment)
   - Container port: 4000
   - Service: `dspace-angular-service` (ClusterIP)
   - Path: `/`

2. **Backend API** (`dspace-backend` Deployment)
   - Container port: 8080
   - Service: `dspace-backend-service` (ClusterIP)
   - Path: `/server`

3. **Solr Search** (`dspace-solr` StatefulSet)
   - Container port: 8983
   - Service: `dspace-solr-service` (ClusterIP)
   - Persistent storage: 5Gi PVC

4. **PostgreSQL Database** (CloudNativePG Cluster - **Managed Database**)
   - Container port: 5432
   - Services:
     - `dspace-postgres-rw` (read-write, primary)
     - `dspace-postgres-ro` (read-only replicas)
     - `dspace-postgres-r` (any instance)
   - Persistent storage: 20Gi PVC per instance

External access via Ingress:
- Host: `hello-clarin-dspace.dyn.cloud.e-infra.cz`
- TLS: Let's Encrypt certificate
- Routes: `/` → Angular, `/server` → Backend API
- The client_max_body is set to *10G*
All services use ClusterIP. Only HTTPS (443) is exposed externally.

### **Storage Configuration**

**Current Configuration (Optimized for Performance):**

| Component | Storage Class | Size | Reason |
|-----------|--------------|------|--------|
| **PostgreSQL** | `csi-ceph-rbd-du` | 20Gi | Fast block storage for database operations |
| **PostgreSQL Backups** | **S3 Storage** | Unlimited | Automated daily backups with 30-day retention |
| **Solr** | `csi-ceph-rbd-du` | 5Gi | Better performance for search indexing |
| **Bitstreams** | **S3 Storage** | Unlimited | Uploaded files stored exclusively in S3 (no local assetstore) |

**S3 Configuration:**

This deployment uses **S3-only storage** for bitstreams - no local assetstore is configured. All uploaded files are stored directly in S3 object storage.

- **Storage mode**: S3-only (no local assetstore, no synchronization)
- **Primary store**: S3 (store index 1)
- **Direct downloads**: Enabled via presigned URLs for better performance
- For detailed information, see the [S3 Storage Integration wiki](https://github.com/ufal/clarin-dspace/wiki/S3-Storage-Integration)

**Setting up S3 Secrets:**

For information on how to securely configure S3 credentials, see [issue #23](https://github.com/ufal/dspace-k8s/issues/23).

- Credentials example `k8s/secrets.yaml` below (this file is gitignored by default — do NOT commit real secrets).
- S3 settings in `k8s/dspace-configmap.yaml` - `local.cfg` (reads endpoint, bucket, region from the env)
- Sealing secrets: create a safe-to-commit sealed secret from `k8s/secrets.yaml`:

```yaml
---
apiVersion: v1
kind: Secret
metadata:
  name: s3-assetstore-secret
type: Opaque
stringData:
  AWS_ACCESS_KEY_ID: "YOUR_ACCESS_KEY"
  AWS_SECRET_ACCESS_KEY: "YOUR_SECRET_KEY"
  S3_ENDPOINT: "https://s3.cl4.du.cesnet.cz"
  S3_BUCKET_NAME: "testbucket"
  S3_REGION: "eu-central-1"
---
apiVersion: v1
kind: Secret
metadata:
  name: dspace-postgres-superuser
type: kubernetes.io/basic-auth
stringData:
  # Change password before deploying to production!
  username: "dspace"
  password: "PASSWORD"
---
apiVersion: v1
kind: Secret
metadata:
  name: dspace-admin-secret
type: kubernetes.io/basic-auth
stringData:
  username: "admin@admin.sk"
  password: "admin"

```

```bash
pushd k8s
# you need to specify the correct namespace so the secreat can be unsealed, but to work with overlays don't keep the namespace in the yaml
kubeseal --controller-namespace sealed-secrets-operator --namespace clarin-dspace-ns --format yaml < secrets.yaml | grep -v namespace > sealed-secrets.yaml
# or
# kubeseal --controller-namespace sealed-secrets-operator --namespace $(sed -ne 's/^namespace:\s*//p' kustomization.yaml) --format yaml < secrets.yaml | grep -v namespace > sealed-secrets.yaml
popd
```

Then commit `k8s/sealed-secrets.yaml` and apply it with `kubectl apply -f k8s/sealed-secrets.yaml` or `kubectl apply -k k8s` (the controller will decrypt it in-cluster).

> **Note:** The file `k8s/assets-pvc.yaml` still exists in the repository for backwards compatibility but is **not used** in the current S3-only configuration. It is not included in `k8s/kustomization.yaml` and will not be deployed. If you need local assetstore storage in the future, you can add it back to the kustomization resources.

**S3 Bucket Requirements for PostgreSQL Backups:**
- The S3 credentials must have read/write permissions to the backup destination path
- PostgreSQL backups are stored at: `s3://dspace-backups/postgresql-backups/` by default
- **For production deployments:** Use overlays to customize the backup bucket and endpoint:
  - The `init_overlay.sh` script will prompt for S3 backup bucket name and endpoint URL
  - These values should match your `S3_BUCKET_NAME` and `S3_ENDPOINT` from the secret
  - Alternatively, manually patch `k8s/postgres-cnpg-cluster.yaml` in your overlay
- You can use the same S3 endpoint and credentials as the asset store, or configure a separate bucket for backups
- Ensure the backup bucket has adequate retention policies and versioning if required
- Recommended: Use a separate S3 bucket or path for database backups to isolate them from application data

### **Resource Requirements**

**Production-Tuned CPU and Memory Configuration:**

Resource limits and requests have been calibrated based on production metrics (docker stats after 2 weeks of uptime). All main workload CPU requests are set to ≥1000m as recommended.

| Component                     | CPU Request | CPU Limit | Memory Request | Memory Limit | Notes                                                                                       |
|-------------------------------|-------------|-----------|----------------|--------------|---------------------------------------------------------------------------------------------|
| **Angular Frontend**          | 1           | 2         | 4Gi            | 8Gi          | pm2 runs 7 instances; actual prod usage ~6.3GiB                                             |
| **Backend API**               | 1           | 4         | 3Gi            | 6Gi          | Actual prod usage ~4.76GiB                                                                  |
| **Solr**                      | 1           | 2         | 2Gi            | 4Gi          | Actual prod usage ~3.75GiB                                                                  |
| **PostgreSQL** (per instance) | 1           | 4         | 1Gi            | 4Gi          | |
| **CronJobs** (all 7)          | 1           | 2         | 1Gi            | 2Gi          | Batch maintenance tasks (run periodically)                                                  |

**Total Resource Requirements (approx, live components + DB instances):**
- **CPU Requests:** ~6 CPU cores (3000m main workloads + 3000m for 3 PostgreSQL instances)
- **CPU Limits:** ~20 CPU cores (8 main workloads + 12 for PostgreSQL replicas)
- **Memory Requests:** ~12Gi (9Gi main workloads + 3Gi for PostgreSQL instances)
- **Memory Limits:** ~30Gi (18Gi main workloads + 12Gi for PostgreSQL instances)

**Notes:**
- Limits must sum to less than namespace/project quota
- PostgreSQL runs 3 instances (1 primary + 2 replicas for HA); resource settings are per-instance
- CronJobs run maintenance tasks (index-discovery, health-report, OAI import, subscriptions, cleanup) and use 1000m CPU request and 1Gi memory request


## Pre-Deployment Configuration using overlays

The repository ships with a small helper script **`init_overlay.sh`** that automates the creation of a Kustomize overlay from a set of `*.yaml.template` files located in `overlays/template`.
It also ships with a sample overlay setup (inited by the script) in `overlays/kosarko-ns`.

#### Using the newly‑created overlay

Once the overlay exists you can apply the whole stack with a single `kubectl apply -k` command, pointing at the overlay directory:

```bash
# Make sure your KUBECONFIG points to the Rancher cluster
export KUBECONFIG=./kubeconfig.yaml    # or set it in your shell

# Apply the overlay
kubectl apply -k overlays/kosarko-ns
```

> **Tip:** The overlay directory name (`kosarko-ns` in the example) can be any name you prefer – just keep the path consistent with the one you passed to `init_overlay.sh`.

#### When to re‑run the script

If you need to change **only** the namespace, hostname, or TLS secret name you can simply re‑run `init_overlay.sh` with a **different overlay name** (e.g. `kosarko‑staging`).
If you modify the original template files, delete the existing overlay directory (or rename it) and run the script again to regenerate fresh manifests.

*The `init_overlay.sh` script is intentionally lightweight and has **no external dependencies** beyond Bash, `envsubst` (part of the GNU `gettext` package), and standard Unix utilities. It is safe to run on any POSIX‑compatible shell.*

## Pre-Deployment Configuration (when not using overlays)

**IMPORTANT: Review and update these files before deploying to production!**

### 1. **Rancher Kubeconfig Token** - `kubeconfig.yaml`
   ```yaml
   users:
   - name: kuba-cluster
     user:
       token: YOUR_RANCHER_TOKEN_HERE
   ```

### 2. **Database Credentials**

see `sealed-secrets.yaml` above

### 3. **S3 Storage Credentials**

see `sealed-secrets.yaml` above

### 4. **Domain/Hostname Configuration** - `k8s/dspace-ingress.yaml`
   ```yaml
   spec:
     tls:
       - hosts:
           - YOUR-DOMAIN.EXAMPLE.COM
         secretName: YOUR-DOMAIN-EXAMPLE-COM-TLS
     rules:
       - host: YOUR-DOMAIN.EXAMPLE.COM
   ```

### 5. **DSpace Configuration** - `k8s/dspace-configmap.yaml`

   **What to set:**
   - `dspace.hostname`: YOUR-DOMAIN.EXAMPLE.COM (must match Ingress host)
   - `proxies.trusted.ipranges`: Your cluster's Pod CIDR (default: `10.42.0.0/16`)

   - **Angular config (`config.yml`)**:
     - `rest.host`: YOUR-DOMAIN.EXAMPLE.COM (for Angular SSR)

   ```yaml
   # In local.cfg section:
   dspace.hostname = YOUR-DOMAIN.EXAMPLE.COM

   # Optional - Email configuration for CronJob notifications
   mail.server = smtp.your-provider.com
   mail.server.port = 587
   mail.server.username = your-smtp-user
   mail.server.password = your-smtp-password
   mail.from.address = noreply@your-domain.com
   ```

   ```yaml
   # In config.yml section (Angular SSR):
   rest:
     ssl: true
     host: YOUR-DOMAIN.EXAMPLE.COM
     port: 443
   ```

### 6. **CronJob Email Configuration** - `k8s/dspace-cronjobs.yaml`

   **Health Report Email:**
   The `dspace-health-report` CronJob sends daily health reports to a specified email address.
   - Replace `YOUR.EMAIL@DOMAIN.COM` with your actual admin email address
   
   ```yaml
   # Find the `dspace-health-report` CronJob (line ~92)
   - /dspace/bin/dspace health-report -e admin@your-domain.com
   ```

### 7. **Namespace** - `k8s/kustomization.yaml`

   ```yaml
   namespace: clarin-dspace-ns
   ```

### 8. **Backend entrypoint - `k8s/backend-deployment.yaml`

   ```yaml
   command: ['/bin/bash', '-c']
   args:
      # modify entry point according to your needs !!!
      # possibly remove index discovery when not testing
   ```

## Verify Kubeconfig Setup

1. Set your KUBECONFIG environment variable:
   ```powershell
   set KUBECONFIG=kubeconfig.yaml
   kubectl config view --minify
   ```

2. Verify cluster connectivity:
   ```powershell
   kubectl get nodes
   ```

## Deploy to Rancher Cloud

### Using Deployment Script (Recommended)

```powershell
.\deploy.bat
```

### Manual
```powershell
kubectl apply -k k8s/
# or kubectl apply -k overlays/my-overlays
# wait for a 5-8 minutes and verify deployment
kubectl get pods -n clarin-dspace-ns
kubectl get services -n clarin-dspace-ns
kubectl get pvc -n clarin-dspace-ns
```

Wait until all pods show `Running`:
- `dspace-postgres-1` (CloudNativePG primary)
- `dspace-solr-0`
- `dspace-backend-xxxxx`
- `dspace-angular-xxxxx`

## Access

- Frontend: https://hello-clarin-dspace.dyn.cloud.e-infra.cz/
- Backend API: https://hello-clarin-dspace.dyn.cloud.e-infra.cz/server

## Admin User

### **Test/Development Environment**

**ONLY for testing**, an admin user is auto-created on first deployment. The auto-creation is controlled by the `DSPACE_AUTO_CREATE_ADMIN` environment variable in `k8s/backend-deployment.yaml`.

admin credentials:
```
Email: admin@admin.sk
Password: admin
```
### **Production Environment**

**IMPORTANT**: For production, you MUST disable auto-admin creation and create a secure admin manually.

**Step 1: Disable Auto-Creation**

Edit `k8s/backend-deployment.yaml` and change:
```yaml
- name: DSPACE_AUTO_CREATE_ADMIN
  value: "false"
```

**Step 2: Deploy Without Default Admin**

```powershell
kubectl apply -f k8s/backend-deployment.yaml
kubectl rollout restart deployment dspace-backend -n clarin-dspace-ns
```

**Step 3: Create Admin Manually**

```powershell
# Get the backend pod name
kubectl get pods -n clarin-dspace-ns -l app=dspace-backend

# Create admin with YOUR credentials
kubectl exec -it <backend-pod-name> -n clarin-dspace-ns -- /dspace/bin/dspace create-administrator -e your-email@example.com -f YourFirstName -l YourLastName -p YourSecurePassword123 -c en

```

## Scaling

```powershell
# Scale frontend
kubectl scale deployment dspace-angular -n clarin-dspace-ns --replicas=3
```

```powershell
# Scale backend
kubectl scale deployment dspace-backend -n clarin-dspace-ns --replicas=2
```

```powershell
# Scale PostgreSQL database (CloudNativePG)
# set number of instances in yaml config
kubectl apply -f k8s/postgres-cnpg-cluster.yaml
```

## CloudNativePG PostgreSQL Management

### CNPG Cluster Status
```powershell
# Check cluster health
kubectl get cluster.postgresql.cnpg.io -n clarin-dspace-ns

# Check pods
kubectl get pods -n clarin-dspace-ns -l cnpg.io/cluster=dspace-postgres

# Check logs
kubectl logs -n clarin-dspace-ns dspace-postgres-1 -f

# Connect to database
kubectl exec -it dspace-postgres-1 -n clarin-dspace-ns -- psql -U postgres -d dspace
```

### PostgreSQL Backups

The PostgreSQL database is configured with automated backups to S3-compatible storage using CloudNativePG's built-in backup functionality.

**Backup Configuration:**
- **Storage:** S3-compatible object storage (same S3 endpoint used for DSpace assets)
- **Schedule:** Daily at 2:00 AM UTC
- **Retention:** 30 days
- **Compression:** gzip (both WAL and data)
- **Location:** `s3://dspace-backups/postgresql/`

**Verify Backup Status:**
```powershell
# List all backups
kubectl get backup -n clarin-dspace-ns

# Check scheduled backup status
kubectl get scheduledbackup -n clarin-dspace-ns

# View backup details
kubectl describe backup <backup-name> -n clarin-dspace-ns

# Check cluster backup status
kubectl get cluster dspace-postgres -n clarin-dspace-ns -o jsonpath='{.status.lastSuccessfulBackup}'
```

**Manual Backup:**
```powershell
# Trigger an immediate backup using kubectl cnpg plugin
kubectl cnpg backup dspace-postgres -n clarin-dspace-ns
```

Alternatively, create a Backup resource from a YAML file:

```powershell
# Create backup.yaml file first, then apply it
kubectl apply -f backup.yaml
```

Example `backup.yaml`:
```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata:
  name: dspace-postgres-manual-backup
  namespace: clarin-dspace-ns
spec:
  cluster:
    name: dspace-postgres
```

**Restore from Backup:**

To restore from a backup, you need to create a new cluster with bootstrap recovery configuration:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: dspace-postgres-restored
spec:
  instances: 3
  
  bootstrap:
    recovery:
      source: dspace-postgres
      
  externalClusters:
    - name: dspace-postgres
      barmanObjectStore:
        destinationPath: s3://dspace-backups/postgresql/
        s3Credentials:
          accessKeyId:
            name: s3-assetstore-secret
            key: AWS_ACCESS_KEY_ID
          secretAccessKey:
            name: s3-assetstore-secret
            key: AWS_SECRET_ACCESS_KEY
          region:
            name: s3-assetstore-secret
            key: S3_REGION
        endpointURL: https://s3.cl4.du.cesnet.cz
        wal:
          compression: gzip
```

**Point-in-Time Recovery (PITR):**

CloudNativePG supports point-in-time recovery using WAL archives:

```yaml
bootstrap:
  recovery:
    source: dspace-postgres
    recoveryTarget:
      targetTime: "2024-01-15 10:00:00.00000+00"
```

**Important Notes:**
- Backups include both base backups and continuous WAL archiving
- WAL files are compressed and continuously archived to S3
- The S3 bucket path must be accessible and have sufficient space
- Ensure S3 credentials in `s3-assetstore-secret` have write permissions to the backup destination
- For production, consider using a separate S3 bucket for backups with appropriate lifecycle policies

## Updates

```powershell
kubectl apply -k k8s/
kubectl rollout restart deployment/dspace-backend -n clarin-dspace-ns
kubectl rollout restart deployment/dspace-angular -n clarin-dspace-ns
```

## DSpace CronJobs on Kubernetes

The following DSpace maintenance tasks have been converted to Kubernetes CronJobs:

| Job Name | Schedule | Description |
|----------|----------|-------------|
| `dspace-oai-import` | Daily at 23:00 | Import OAI metadata |
| `dspace-index-discovery` | Daily at 00:00 | Rebuild search indexes |
| `dspace-subscription-daily` | Daily at 03:01 | Send daily subscription emails |
| `dspace-subscription-weekly` | Sundays at 03:02 | Send weekly subscription emails |
| `dspace-subscription-monthly` | 1st of month at 03:03 | Send monthly subscription emails |
| `dspace-cleanup` | 1st of month at 04:00 | Clean up old data |
| `dspace-health-report` | Daily at 00:00 | Send health report email |

## Important Notes

- **Concurrency**: Jobs set to `Forbid` - won't run if previous job still running
- **History**: Keeps last 3 successful and 3 failed jobs
- **Timezone**: All times are in **UTC** (add 1 hour for CET, 2 for CEST)
- **Restart**: Jobs will retry on failure (`restartPolicy: OnFailure`)

## Deployment

### 1. Apply the CronJobs

```powershell
$env:KUBECONFIG="kubeconfig.yaml"

kubectl apply -f k8s/dspace-cronjobs.yaml -n clarin-dspace-ns
# OR
kubectl apply -k k8s
```

### 2. Verify CronJobs are Created

```powershell
kubectl get cronjobs -n clarin-dspace-ns
```

## Management

### Manually Trigger a Job

```powershell
kubectl create job --from=cronjob/<CRONJOB-NAME> <JOB-RUN-NAME> -n clarin-dspace-ns
```

### View Job Status

```powershell
kubectl get jobs -n clarin-dspace-ns -w
```

## View Logs

```powershell
kubectl logs <POD_NAME> -n clarin-dspace-ns
```

### Suspend/Resume CronJobs

```powershell
# Suspend (stop scheduling)
kubectl patch cronjob <CRONJOB-NAME> -n clarin-dspace-ns -p '{"spec":{"suspend":true}}'

# Resume
kubectl patch cronjob <CRONJOB-NAME> -n clarin-dspace-ns -p '{"spec":{"suspend":false}}'
```

### Describe CronJob
```powershell
kubectl describe cronjob <CRONJOB-NAME> -n clarin-dspace-ns
```

### Delete CronJobs

```powershell
# Delete specific CronJob
kubectl delete cronjob <CRONJOB-NAME> -n clarin-dspace-ns

# Delete all DSpace CronJobs
kubectl delete -f k8s/dspace-cronjobs.yaml -n clarin-dspace-ns
```

## Troubleshooting

### Access Logs

```powershell
kubectl logs -n clarin-dspace-ns -l app=dspace-backend -f
kubectl logs -n clarin-dspace-ns -l app=dspace-angular -f
kubectl logs -n clarin-dspace-ns -l app=dspace-solr-0 -f
kubectl logs -n clarin-dspace-ns dspace-postgres-1 -f
```

### DSpace backend file logs (/dspace/log)

The backend writes important logs as files under `/dspace/log/` (for example `dspace.log`, `warn.log`). This deployment makes them:

- **Kubernetes-standard**: a sidecar (`dspace-log-tailer`) tails the files to stdout, so they are visible via `kubectl logs` and picked up by cluster logging.
- **Persistent**: `/dspace/log` is mounted from a PVC (`dspace-logs-pvc-rwx`).
- **Long-term**: rotated log files are periodically synced to S3 by the `dspace-log-s3-sync` cronjob.

View the logs:

```powershell
# File logs streamed to stdout
kubectl logs -n clarin-dspace-ns -l app=dspace-backend -c dspace-log-tailer -f
```

S3 destination format:

- `s3://"${S3_BUCKET_NAME}"/log`

> Retention should be enforced via an **S3 lifecycle policy** (recommended: 1y+ expiration/transition rules).
### Common Issues

1. Pod not starting:
   ```powershell
   kubectl describe pod -n clarin-dspace-ns <pod-name>
   ```

2. Database connection issues:
   ```powershell
   # Check CloudNativePG cluster status
   kubectl get cluster.postgresql.cnpg.io -n clarin-dspace-ns
   kubectl logs -n clarin-dspace-ns dspace-postgres-1 -f
   ```

3. Storage issues:
   ```powershell
   kubectl get pvc -n clarin-dspace-ns
   # Note: No assetstore PVC in S3-only configuration
   kubectl describe pvc -n clarin-dspace-ns solr-data-pvc
   ```

4. Ingress issues:
   ```powershell
   kubectl describe ingress dspace-ingress -n clarin-dspace-ns
   ```

## Cleanup

```powershell
kubectl delete -f k8s/ -n clarin-dspace-ns
# if needed delete the PVCs too (DATA WILL BE LOST !!!)
# Note: No assetstore-pv-claim in S3-only configuration
kubectl delete pvc dspace-postgres-1 solr-data-pvc -n clarin-dspace-ns
```

## Performance

### Load Testing Results

**Test Configuration:**
- 8 Angular frontend replicas (1 core each)
- 1 Backend replica (4 cores)
- 1 Solr replica (2 cores)
- 3 PostgreSQL replicas (4 cores each)
- Database connection pool: 100 connections

### Quick Load Test

Using Apache Benchmark in Docker:

```powershell
# Test with 50 concurrent connections, 500 requests
docker run --rm httpd:alpine ab -n 500 -c 50 https://hello-clarin-dspace.dyn.cloud.e-infra.cz/home

# Test with 150 concurrent connections, 1000 requests
docker run --rm httpd:alpine ab -n 1000 -c 150 https://hello-clarin-dspace.dyn.cloud.e-infra.cz/home
```

**Expected Results (50 concurrent):**
- Requests per second: ~75 RPS
- Mean response time: ~770ms
- 99th percentile: ~1,500ms

**Expected Results (150 concurrent):**
- Requests per second: ~65 RPS
- Mean response time: ~1,500ms
- 99th percentile: ~4,500ms
