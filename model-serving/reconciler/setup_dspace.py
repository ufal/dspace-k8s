"""DSpace-side setup for the model-serving PoC.

Subcommands:
  schema       register the aimodel.* metadata schema + fields (idempotent)
  seed-item    create/reuse the "AI Models" collection and deposit one demo
               item (Qwen2.5-0.5B-Instruct) with aimodel.* metadata
  mark         flip aimodel.serveable true/false on an existing item

Auth: DSPACE_USER / DSPACE_PASS env vars (see dspace_api.client_from_env).
"""

import argparse
import sys
import time

from dspace_api import DspaceApiError, client_from_env

AIMODEL_NAMESPACE = "http://dspace.org/namespace/aimodel/"
AIMODEL_FIELDS = ["serveable", "task", "format", "storageuri", "backend"]

COMMUNITY_TITLE = "AI Models"
COLLECTION_TITLE = "AI Models"

ITEM_TITLE = "Qwen2.5-0.5B-Instruct"
ITEM_META = {
    "traditionalpageone": {
        "dc.title": ITEM_TITLE,
        "dc.type": "toolService",  # closed vocabulary "common_types"; see README
        "dc.date.issued": "2024",
        "dc.publisher": "Qwen Team, Alibaba Cloud",
        "dc.contributor.author": "Qwen Team",
        "local.contact.person": "AI Model Serving PoC",
    },
    "traditionalpagetwo": {
        "dc.description": "Qwen2.5-0.5B-Instruct demo item for the KServe model-serving PoC.",
        "dc.subject": "language model",
        # Conditionally required once dc.type=toolService (metashare/CMDI profile
        # fields); closed vocabularies "metashare_detailed_toolService" /
        # "metashare_languageDependent" confirmed live against this deployment.
        "metashare.ResourceInfo#ContentInfo.detailedType": "service",
        "metashare.ResourceInfo#ResourceComponentType#ToolServiceInfo.languageDependent": "true",
    },
}
DEMO_STORAGE_URI = "s3://models/qwen2.5-0.5b-instruct"
DEMO_AIMODEL_VALUES = {
    "serveable": "true",
    "task": "chat",
    "format": "huggingface",
    "storageuri": DEMO_STORAGE_URI,
    "backend": "huggingface",
}


def find_by_title(client, endpoint, key, title):
    data = client.get_json(f"{endpoint}?size=100")
    entries = data.get("_embedded", {}).get(key, [])
    for entry in entries:
        titles = entry.get("metadata", {}).get("dc.title", [])
        if titles and titles[0].get("value") == title:
            return entry
    return None


def ensure_community(client, title):
    existing = find_by_title(client, "/core/communities", "communities", title)
    if existing:
        print(f"Reusing community {existing['uuid']}", file=sys.stderr)
        return existing
    body = {"name": title, "metadata": {"dc.title": [{"value": title}]}}
    created = client.post_json("/core/communities", body)
    print(f"Created community {created['uuid']}", file=sys.stderr)
    return created


def ensure_collection(client, parent_uuid, title):
    existing = find_by_title(client, "/core/collections", "collections", title)
    if existing:
        print(f"Reusing collection {existing['uuid']}", file=sys.stderr)
        return existing
    body = {"name": title, "metadata": {"dc.title": [{"value": title}]}}
    created = client.post_json(f"/core/collections?parent={parent_uuid}", body)
    print(f"Created collection {created['uuid']}", file=sys.stderr)
    return created


def ensure_license(client):
    """Port of ensureLicense() in dspace-e2e/seed-archived-item.js."""
    d = client.get_json("/core/clarinlicenses?size=1")
    licenses = d.get("_embedded", {}).get("clarinlicenses", [])
    if licenses:
        return licenses[0]["name"]

    label_body = {"label": "PUB", "title": "Publicly Available", "extended": False}
    try:
        created = client.post_json("/core/clarinlicenselabels", label_body)
        label = {
            "id": created["id"],
            "label": created["label"],
            "title": created["title"],
            "extended": created["extended"],
        }
    except DspaceApiError:
        ld = client.get_json("/core/clarinlicenselabels?size=100")
        found = next(
            (
                l
                for l in ld.get("_embedded", {}).get("clarinlicenselabels", [])
                if l["label"] == "PUB" and not l["extended"]
            ),
            None,
        )
        if not found:
            raise
        label = {
            "id": found["id"],
            "label": found["label"],
            "title": found["title"],
            "extended": found["extended"],
        }

    name = "AI Model Serving PoC License"
    license_body = {
        "name": name,
        "bitstreams": 0,
        "confirmation": 0,  # Confirmation.NOT_REQUIRED
        "requiredInfo": "",
        "definition": "http://example.com/aimodel-serving-poc-license",
        "clarinLicenseLabel": label,
        "extendedClarinLicenseLabels": [],
    }
    client.post_json("/core/clarinlicenses", license_body)
    print(f"Created clarin license: {name}", file=sys.stderr)
    return name


def deposit_item(client, collection_uuid):
    license_name = ensure_license(client)

    res = client.request(
        "POST",
        f"/submission/workspaceitems?owningCollection={collection_uuid}",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    wsi = res.json()
    wsi_id = wsi["id"]
    wsi_self = wsi["_links"]["self"]["href"]
    print(f"workspaceitem {wsi_id}", file=sys.stderr)

    model_card = (
        f"# {ITEM_TITLE}\n\nDemo model card for the KServe model-serving PoC.\n"
        f"Storage URI: {DEMO_STORAGE_URI}\n"
    ).encode()
    client.request(
        "POST",
        f"/submission/workspaceitems/{wsi_id}",
        headers={"Accept": "application/json"},
        files={"file": ("MODEL_CARD.txt", model_card, "text/plain")},
    )
    print("uploaded MODEL_CARD.txt", file=sys.stderr)

    # One PATCH call *per section*: batching ops for two different sections
    # (e.g. traditionalpageone + traditionalpagetwo) in a single request 422s
    # on this fork even though every op is individually valid -- confirmed by
    # isolating each op, then splitting section-batches, live against the API.
    for section, fields in ITEM_META.items():
        ops = [
            {"op": "add", "path": f"/sections/{section}/{key}", "value": [{"value": value}]}
            for key, value in fields.items()
        ]
        client.patch_json(f"/submission/workspaceitems/{wsi_id}", ops)

    client.patch_json(
        f"/submission/workspaceitems/{wsi_id}",
        [{"op": "add", "path": "/sections/license/granted", "value": True}],
    )

    # NOTE: dspace-e2e/seed-archived-item.js uses "/sections/clarin-license/select"
    # (matching Angular's section-license.component.ts), but that path 404/422s
    # against this backend: ClarinLicenseResourceStep.doPatchProcessing only
    # accepts ".../granted"; the license-select endpoint actually routes through
    # WorkspaceItemRestRepository's top-level "license" path (ClarinLicenseUtils
    # .updateLicenseForItem), confirmed live against this deployment.
    client.patch_json(
        f"/submission/workspaceitems/{wsi_id}",
        [{"op": "replace", "path": "/license", "value": license_name}],
    )

    check = client.get_json(f"/submission/workspaceitems/{wsi_id}")
    errs = [
        e
        for e in check.get("errors", [])
        if e.get("message") == "error.validation.required" or "notgranted" in (e.get("message") or "")
    ]
    if errs:
        print(f"Remaining validation errors: {errs}", file=sys.stderr)

    client.request(
        "POST",
        "/workflow/workflowitems",
        headers={"Content-Type": "text/uri-list", "Accept": "application/json"},
        data=wsi_self,
    )
    print("deposited", file=sys.stderr)

    time.sleep(1.5)
    data = client.get_json("/core/items?size=100")
    items = data.get("_embedded", {}).get("items", [])
    for it in items:
        titles = it.get("metadata", {}).get("dc.title", [])
        if it.get("inArchive") and titles and titles[0].get("value") == ITEM_TITLE:
            return it["uuid"]
    raise RuntimeError("Deposit succeeded but no archived item with matching title was found.")


def add_aimodel_metadata(client, uuid):
    ops = [
        {"op": "add", "path": f"/metadata/aimodel.{field}", "value": [{"value": DEMO_AIMODEL_VALUES[field]}]}
        for field in AIMODEL_FIELDS
    ]
    try:
        client.patch_json(f"/core/items/{uuid}", ops)
        return
    except DspaceApiError as e:
        print(f"Batch aimodel metadata patch failed ({e}); retrying per-field with append form.", file=sys.stderr)

    for field in AIMODEL_FIELDS:
        op = [{"op": "add", "path": f"/metadata/aimodel.{field}/-", "value": {"value": DEMO_AIMODEL_VALUES[field]}}]
        client.patch_json(f"/core/items/{uuid}", op)


def cmd_schema(client, _args):
    try:
        schema = client.post_json(
            "/core/metadataschemas", {"prefix": "aimodel", "namespace": AIMODEL_NAMESPACE}
        )
        schema_id = schema["id"]
        print(f"Created aimodel schema id={schema_id}", file=sys.stderr)
    except DspaceApiError as e:
        if e.status_code != 422:
            raise
        schemas = client.get_json("/core/metadataschemas?size=100")
        found = next(
            (s for s in schemas["_embedded"]["metadataschemas"] if s["prefix"] == "aimodel"), None
        )
        if not found:
            raise
        schema_id = found["id"]
        print(f"Reusing aimodel schema id={schema_id}", file=sys.stderr)

    for field in AIMODEL_FIELDS:
        try:
            client.post_json(
                f"/core/metadatafields?schemaId={schema_id}",
                {"element": field, "qualifier": None, "scopeNote": ""},
            )
            print(f"Created field aimodel.{field}", file=sys.stderr)
        except DspaceApiError as e:
            if e.status_code != 422:
                raise
            print(f"Field aimodel.{field} already exists, skipping", file=sys.stderr)


def cmd_seed_item(client, _args):
    community = ensure_community(client, COMMUNITY_TITLE)
    collection = ensure_collection(client, community["uuid"], COLLECTION_TITLE)
    print(f"COLLECTION {collection['uuid']}")

    existing = None
    data = client.get_json("/core/items?size=100")
    for it in data.get("_embedded", {}).get("items", []):
        titles = it.get("metadata", {}).get("dc.title", [])
        if it.get("inArchive") and titles and titles[0].get("value") == ITEM_TITLE:
            existing = it["uuid"]
            break

    if existing:
        print(f"Reusing existing item {existing}", file=sys.stderr)
        uuid = existing
    else:
        uuid = deposit_item(client, collection["uuid"])

    add_aimodel_metadata(client, uuid)
    print(f"ITEM {uuid}")


def cmd_mark(client, args):
    val = "true" if args.serveable == "true" else "false"
    ops = [{"op": "replace", "path": "/metadata/aimodel.serveable/0", "value": {"value": val}}]
    try:
        client.patch_json(f"/core/items/{args.uuid}", ops)
    except DspaceApiError:
        try:
            client.patch_json(
                f"/core/items/{args.uuid}",
                [{"op": "remove", "path": "/metadata/aimodel.serveable/0"}],
            )
        except DspaceApiError:
            pass
        client.patch_json(
            f"/core/items/{args.uuid}",
            [{"op": "add", "path": "/metadata/aimodel.serveable", "value": [{"value": val}]}],
        )
    print(f"MARKED {args.uuid} serveable={val}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dspace-api", default="http://dspace.127.0.0.1.sslip.io:8080/server/api")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("schema")
    sub.add_parser("seed-item")
    mark_parser = sub.add_parser("mark")
    mark_parser.add_argument("--uuid", required=True)
    mark_parser.add_argument("--serveable", choices=["true", "false"], required=True)

    args = parser.parse_args()
    client = client_from_env(args.dspace_api)

    if args.command == "schema":
        cmd_schema(client, args)
    elif args.command == "seed-item":
        cmd_seed_item(client, args)
    elif args.command == "mark":
        cmd_mark(client, args)


if __name__ == "__main__":
    main()
