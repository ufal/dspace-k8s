# CERIT Logging - Quick Reference

## Pod Annotations for Fluent Bit

All DSpace deployments are configured with Fluent Bit annotations for automatic log collection:

### Backend (DSpace API)
```yaml
fluentbit.io/parser: "multi_line"
fluentbit.io/path: "/dspace/log/*.log"
fluentbit.io/exclude-path: "/dspace/log/*.tmp"
```

### Frontend (Angular)
```yaml
fluentbit.io/parser: "json"
fluentbit.io/exclude-path: "*.tmp"
```

### Solr (Search)
```yaml
fluentbit.io/parser: "multiline"
fluentbit.io/exclude-path: "*.tmp"
```

## Pod Labels

All pods include logging labels for easy filtering in centralized logging:

```yaml
labels:
  logging: enabled
  component: backend|frontend|search
  tier: api|ui|data
```

## Query Logs in Centralized System

**Kibana (Elasticsearch):**
```
kubernetes.labels.app: "dspace-backend"
kubernetes.labels.logging: "enabled"
kubernetes.namespace_name: "clarin-dspace-ns"
```

**Grafana (Loki):**
```
{app="dspace-backend", logging="enabled", namespace="clarin-dspace-ns"}
```

## Log Locations

1. **Container logs** (stdout/stderr): `kubectl logs -f <pod-name>`
2. **Persistent volume**: `/dspace/log/*.log`
3. **Centralized system**: Kibana/Grafana (via Fluent Bit)
4. **S3 backups**: `s3://bucket/dspace-logs-backup/YYYY-MM-DD_HH-MM-SS/`

## See Also

- [LOGGING.md](LOGGING.md) - Complete logging documentation
- [CERIT Docs](https://docs.cerit.io/en/docs/operators/logging) - CERIT logging infrastructure
