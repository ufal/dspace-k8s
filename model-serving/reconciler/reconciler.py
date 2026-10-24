"""Reconciles DSpace items with aimodel.serveable=true against KServe
InferenceServices + Ingresses.

Desired set: anonymous GET /api/discover/search/objects?scope=<collection>
             &dsoType=item, then per-item GET /api/core/items/{uuid} for full
             metadata, filtered client-side on aimodel.serveable=="true" and
             aimodel.task=="chat". No auth needed -- this script never logs in.
Actual set:  kubectl get inferenceservice -l app.kubernetes.io/managed-by=
             dspace-reconciler -o json, keyed by dspace.item-uuid label.
"""

import argparse
import json
import subprocess
import sys
import time

import requests

import templates


def _search_collection(api_base, collection_uuid):
    res = requests.get(
        f"{api_base}/discover/search/objects",
        params={"scope": collection_uuid, "dsoType": "item", "size": 100},
    )
    res.raise_for_status()
    return res.json().get("_embedded", {}).get("searchResult", {}).get("_embedded", {}).get("objects", [])


def fetch_desired(api_base, collection_uuid):
    desired = {}
    # Discovery (Solr) indexing is async relative to archiving: a just-created
    # item can be briefly missing from search results. Retry once after a
    # short wait rather than silently reconciling against an empty set.
    objects = _search_collection(api_base, collection_uuid)
    if not objects:
        time.sleep(30)
        objects = _search_collection(api_base, collection_uuid)
    for obj in objects:
        uuid = obj.get("_embedded", {}).get("indexableObject", {}).get("uuid")
        if not uuid:
            continue
        item_res = requests.get(f"{api_base}/core/items/{uuid}")
        item_res.raise_for_status()
        md = item_res.json().get("metadata", {})

        def first(field):
            vals = md.get(field)
            return vals[0]["value"] if vals else None

        if first("aimodel.serveable") != "true" or first("aimodel.task") != "chat":
            continue

        storage_uri = first("aimodel.storageuri")
        backend = first("aimodel.backend") or "huggingface"
        err = templates.validate(uuid, storage_uri or "")
        if err:
            print(f"SKIP {uuid}: {err}", file=sys.stderr)
            continue
        desired[uuid] = {"storage_uri": storage_uri, "backend": backend}
    return desired


def fetch_actual(namespace):
    out = subprocess.run(
        [
            "kubectl",
            "-n",
            namespace,
            "get",
            "inferenceservice",
            "-l",
            f"{templates.MANAGED_BY_LABEL}={templates.MANAGED_BY_VALUE}",
            "-o",
            "json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(out.stdout)
    actual = {}
    for item in data.get("items", []):
        labels = item.get("metadata", {}).get("labels", {})
        uuid = labels.get(templates.ITEM_UUID_LABEL)
        if uuid:
            actual[uuid] = item["metadata"]["name"]
    return actual


def kubectl_apply(yaml_text):
    subprocess.run(["kubectl", "apply", "-f", "-"], input=yaml_text, text=True, check=True)


def kubectl_delete(namespace, name):
    subprocess.run(
        [
            "kubectl",
            "-n",
            namespace,
            "delete",
            "isvc,ingress",
            name,
            "--ignore-not-found",
        ],
        check=True,
    )


def reconcile(api_base, collection_uuid, namespace, dry_run):
    desired = fetch_desired(api_base, collection_uuid)
    actual = fetch_actual(namespace)

    desired_uuids = set(desired.keys())
    actual_uuids = set(actual.keys())

    for uuid in sorted(desired_uuids - actual_uuids):
        name = templates.model_name(uuid)
        print(f"CREATE {name} (item {uuid})")
        if not dry_run:
            yaml_text = templates.inference_service_yaml(
                uuid, desired[uuid]["storage_uri"], desired[uuid]["backend"]
            ) + "---\n" + templates.ingress_yaml(uuid)
            kubectl_apply(yaml_text)

    for uuid in sorted(actual_uuids - desired_uuids):
        name = actual[uuid]
        print(f"DELETE {name} (item {uuid})")
        if not dry_run:
            kubectl_delete(namespace, name)

    unchanged = desired_uuids & actual_uuids
    if unchanged and not (desired_uuids - actual_uuids) and not (actual_uuids - desired_uuids):
        print("OK (no change)")
    elif unchanged:
        for uuid in sorted(unchanged):
            print(f"OK (no change) {actual[uuid]} (item {uuid})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dspace-api", default="http://dspace.127.0.0.1.sslip.io:8080/server/api")
    parser.add_argument("--collection", required=True)
    parser.add_argument("--namespace", default="dspace-k3s")
    parser.add_argument("--dry-run", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()

    if not args.once and not args.watch:
        args.once = True

    try:
        reconcile(args.dspace_api, args.collection, args.namespace, args.dry_run)
        if args.watch:
            while True:
                time.sleep(args.interval)
                reconcile(args.dspace_api, args.collection, args.namespace, args.dry_run)
    except subprocess.CalledProcessError as e:
        print(f"kubectl failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
