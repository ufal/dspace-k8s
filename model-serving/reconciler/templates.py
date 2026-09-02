"""YAML templates for reconciler-managed InferenceServices + Ingresses.

Copies of manifests/smoke/{inferenceservice,ingress}.yaml, proven working in
Phase 4, parameterized by item uuid/storage_uri/backend. GPU-migration
comments are kept identical to the smoke manifests.
"""

import re

NAMESPACE = "dspace-k3s"
MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
MANAGED_BY_VALUE = "dspace-reconciler"
ITEM_UUID_LABEL = "dspace.item-uuid"

_STORAGE_URI_RE = re.compile(r"^s3://[a-z0-9./_-]+$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def model_name(item_uuid):
    return f"model-{item_uuid[:8]}"


def validate(item_uuid, storage_uri):
    """Never inject unvalidated metadata into YAML. Returns error string or None."""
    if not _UUID_RE.match(item_uuid):
        return f"invalid item uuid: {item_uuid!r}"
    if not _STORAGE_URI_RE.match(storage_uri):
        return f"invalid storage_uri: {storage_uri!r}"
    return None


def inference_service_yaml(item_uuid, storage_uri, backend="huggingface"):
    name = model_name(item_uuid)
    return f"""\
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  name: {name}
  namespace: {NAMESPACE}
  labels:
    {MANAGED_BY_LABEL}: {MANAGED_BY_VALUE}
    {ITEM_UUID_LABEL}: {item_uuid}
spec:
  predictor:
    serviceAccountName: models-s3-sa
    model:
      modelFormat:
        name: huggingface
      storageUri: {storage_uri}
      args:
        - --model_name={name}
        - --backend={backend}   # GPU infra: remove (vLLM auto-selects); see README
      # GPU infra: add nvidia.com/gpu: "1" to limits; runtime flips to -gpu image
      resources:
        requests:
          cpu: "2"
          memory: 3Gi
        limits:
          cpu: "4"
          memory: 8Gi
"""


def ingress_yaml(item_uuid):
    name = model_name(item_uuid)
    return f"""\
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {name}
  namespace: {NAMESPACE}
  labels:
    {MANAGED_BY_LABEL}: {MANAGED_BY_VALUE}
    {ITEM_UUID_LABEL}: {item_uuid}
  annotations:
    nginx.ingress.kubernetes.io/proxy-read-timeout: "600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "600"
    nginx.ingress.kubernetes.io/proxy-buffering: "off"
spec:
  ingressClassName: nginx
  rules:
    - host: {name}.127.0.0.1.sslip.io
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: {name}-predictor
                port:
                  number: 80
"""
