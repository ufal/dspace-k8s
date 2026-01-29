# DSpace Logging on CERIT Infrastructure

This document describes the logging setup for DSpace on CERIT/Metacentrum Kubernetes infrastructure.

## Overview

DSpace logging is configured with three complementary approaches:

1. **Standard Kubernetes Logs** - Container stdout/stderr logs (kubectl logs)
2. **Persistent Volume Logs** - Application logs stored on PVC at `/dspace/log`
3. **Centralized Log Collection** - Integration with CERIT's Fluent Bit infrastructure
4. **S3 Backup** - Daily backups of application logs to S3 storage

## Logging Layers

### 1. Container Logs (stdout/stderr)

Standard Kubernetes container logs are automatically collected and can be viewed with:

```bash
# Backend logs
kubectl logs -n clarin-dspace-ns -l app=dspace-backend -f

# Angular frontend logs
kubectl logs -n clarin-dspace-ns -l app=dspace-angular -f

# Specific pod
kubectl logs -n clarin-dspace-ns <pod-name> -f
```

**Retention**: Managed by Kubernetes (typically 1-7 days)

### 2. Persistent Application Logs

DSpace application logs are written to `/dspace/log` which is mounted from a persistent volume:

- **Volume**: `dspace-logs-pv-claim` (10Gi NFS)
- **Location**: `/dspace/log/*.log`
- **Files**:
  - `dspace.log` - Main application log
  - `dspace.log.YYYY-MM-DD` - Rotated logs
  - `solr.log` - Solr-specific logs
  - Other component logs

**Access logs from volume:**

```bash
# Enter backend pod
kubectl exec -it <backend-pod> -n clarin-dspace-ns -- /bin/bash

# View logs
tail -f /dspace/log/dspace.log
ls -lh /dspace/log/
```

**Retention**: Persistent until manually deleted or cleaned up

### 3. CERIT Centralized Logging (Fluent Bit)

CERIT infrastructure provides cluster-level log collection via Fluent Bit. Our deployments are configured with appropriate annotations for automatic log collection:

#### Pod Annotations

Backend deployment includes:
```yaml
annotations:
  fluentbit.io/parser: "multi_line"
  fluentbit.io/path: "/dspace/log/*.log"
  fluentbit.io/exclude-path: "/dspace/log/*.tmp"
```

Angular deployment includes:
```yaml
annotations:
  fluentbit.io/parser: "json"
  fluentbit.io/exclude-path: "*.tmp"
```

#### How It Works

1. CERIT's Fluent Bit DaemonSet runs on each node
2. Fluent Bit reads container logs and files from pods with annotations
3. Logs are parsed according to the specified parser
4. Logs are forwarded to the centralized logging system (typically Elasticsearch/Loki)

#### Accessing Centralized Logs

Depending on CERIT's logging infrastructure:

- **Kibana** (Elasticsearch): `https://kibana.cerit.io` (if available)
- **Grafana Loki**: `https://grafana.cerit.io` (if available)
- Contact CERIT support for access to the logging interface

**Search by pod labels:**
```
kubernetes.labels.app: "dspace-backend"
kubernetes.namespace_name: "clarin-dspace-ns"
```

### 4. S3 Backup

Daily backups of `/dspace/log` directory to S3 storage:

- **CronJob**: `dspace-logs-backup`
- **Schedule**: Daily at 2:00 AM UTC
- **Location**: `s3://bucket/dspace-logs-backup/YYYY-MM-DD_HH-MM-SS/`
- **Retention**: Depends on S3 lifecycle policies

**Access S3 backups:**

```bash
# List backups
aws s3 ls s3://<bucket>/dspace-logs-backup/ --endpoint-url <endpoint>

# Download specific backup
aws s3 sync s3://<bucket>/dspace-logs-backup/2026-01-29_02-00-00/ ./logs/ --endpoint-url <endpoint>
```

## Optional: FluentBit Sidecar

For advanced use cases requiring custom log routing or processing, a FluentBit sidecar can be deployed alongside the DSpace backend.

See `k8s/logging-sidecar-patch.yaml` for configuration.

**When to use sidecar:**
- Need to send logs to multiple destinations
- Require custom log parsing or filtering
- Want to enrich logs with additional metadata
- CERIT's cluster-level Fluent Bit doesn't meet requirements

**When NOT to use sidecar:**
- CERIT's cluster-level Fluent Bit is sufficient (most cases)
- Want to minimize resource usage
- Simple logging requirements

## Log Rotation

DSpace handles log rotation internally using Log4j2 configuration.

**Default rotation policy:**
- Daily rotation at midnight
- Keep last 30 days of logs
- Compress old logs

**Customize rotation:**
Edit `k8s/dspace-configmap.yaml` and add Log4j2 configuration to `local.cfg`.

## Troubleshooting

### Logs not appearing in centralized system

1. Check pod annotations:
   ```bash
   kubectl get pod <pod-name> -n clarin-dspace-ns -o yaml | grep -A 5 annotations
   ```

2. Verify Fluent Bit is running:
   ```bash
   kubectl get pods -n fluent-bit -l app=fluent-bit
   ```

3. Check Fluent Bit logs for errors:
   ```bash
   kubectl logs -n fluent-bit <fluent-bit-pod> | grep dspace
   ```

### High disk usage on log volume

1. Check current log size:
   ```bash
   kubectl exec -it <backend-pod> -n clarin-dspace-ns -- du -sh /dspace/log
   ```

2. Enable automatic cleanup in `k8s/logs-backup-cronjob.yaml`:
   ```yaml
   # Uncomment these lines:
   echo "Cleaning up old log files (older than 7 days)..."
   find /dspace/log -type f -name "*.log*" -mtime +7 -delete
   ```

3. Manually clean old logs:
   ```bash
   kubectl exec -it <backend-pod> -n clarin-dspace-ns -- find /dspace/log -name "*.log.*" -mtime +30 -delete
   ```

### S3 backup failures

Check the backup job logs:
```bash
kubectl get jobs -n clarin-dspace-ns | grep logs-backup
kubectl logs -n clarin-dspace-ns job/dspace-logs-backup-<id>
```

Common issues:
- S3 credentials expired or incorrect
- Network connectivity to S3 endpoint
- Insufficient permissions on S3 bucket

## Best Practices

1. **Use centralized logging for querying** - Search and analyze logs via Kibana/Grafana
2. **Use persistent volume for debugging** - Quick access to recent logs
3. **Use S3 backups for archival** - Long-term retention and compliance
4. **Monitor log volume size** - Set up alerts for disk usage
5. **Configure appropriate log levels** - INFO for production, DEBUG for troubleshooting

## Additional Resources

- [CERIT Logging Documentation](https://docs.cerit.io/en/docs/operators/logging)
- [Fluent Bit Documentation](https://docs.fluentbit.io/)
- [DSpace Logging Configuration](https://wiki.lyrasis.org/display/DSDOC7x/Logging)
- [Kubernetes Logging Architecture](https://kubernetes.io/docs/concepts/cluster-administration/logging/)
