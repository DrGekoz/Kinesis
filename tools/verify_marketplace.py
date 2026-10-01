"""End-to-end test of the Submit-to-Marketplace path, with no Cloudflare involved.

Runs the REAL cloudflare/worker.js in a local Node stub against an in-memory D1, and drives it with
the REAL kinesis/marketplace.py client over the same urllib path production uses.

This is the only way to prove the submit button actually works before the Worker is deployed: the
button, the form, the HTTP call, the server-side validation and the response handling are all real.
Only the D1 database and the network hop are local.

    node tools/marketplace_stub.mjs 8787      # in another shell
    .venv\\Scripts\\python tools\\verify_marketplace.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kinesis import marketplace                                   # noqa: E402
from kinesis.config import Config                                 # noqa: E402
from kinesis.gesture_map import GestureMap, default_map, validate  # noqa: E402

PORT = 8787
BASE = f"http://127.0.0.1:{PORT}"
results = []


def marketplace_slugify(text: str) -> str:
    """Mirror of slugify() in cloudflare/worker.js.

    Kept here rather than imported because the worker is JavaScript - this is the same rule written
    out, so the test asserts the real behaviour instead of re-implementing it differently. If the
    worker's slugify ever changes, this must change with it.
    """
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:60]
    return slug or "map"


def check(name, got, want=True):
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got={got!r} want={want!r}")


def post(path, payload):
    """POST and return (status, body). Never raises, so a rejection is data, not an exception."""
    request = urllib.request.Request(f"{BASE}{path}", data=json.dumps(payload).encode("utf-8"),
                                     method="POST", headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") or "{}"
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"error": body}


def main() -> int:
    cfg = Config()
    cfg.set("marketplace_api", BASE)

    print("=== the worker is up ===")
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=10) as response:
            health = json.loads(response.read().decode("utf-8"))
        check("health responds ok", health.get("ok"), True)
    except Exception as exc:                                       # noqa: BLE001
        print(f"FAIL  could not reach the stub: {exc}")
        print("      start it first:  node tools\\marketplace_stub.mjs 8787")
        return 1

    print()
    print("=== a real map submits and comes back ===")
    gmap = default_map()
    check("the default map is valid client-side", validate(gmap.to_json()), [])
    result = marketplace.submit_map(
        cfg, "Test Map", "A map used to prove the submit path works.", "Joseph Williams",
        "https://github.com/DrGekoz", gmap)
    check("submit returns a slug", bool(result.get("slug")), True)
    check("submit says ok", result.get("ok"), True)
    check("the binding count is right", result.get("bindings"), len(gmap.bindings))
    slug = str(result.get("slug"))

    listed = marketplace.list_maps(cfg)
    check("the map is in the listing", any(m.get("slug") == slug for m in listed), True)
    row = next((m for m in listed if m.get("slug") == slug), {})
    check("the listing shows the author", row.get("author_name"), "Joseph Williams")

    payload = marketplace.fetch_map(cfg, slug)
    downloaded = payload.get("map") or {}
    check("the downloaded map validates", validate(downloaded), [])
    check("the downloaded map is the same one", downloaded.get("name"), gmap.name)

    print()
    print("=== downloads are counted server-side ===")
    before = payload.get("downloads")
    again = marketplace.fetch_map(cfg, slug)
    check("a second download increments", (again.get("downloads") or 0) > (before or 0), True)

    print()
    print("=== the server rejects what it should ===")
    bad_schema = post("/maps", {"title": "Bad", "author_name": "x", "github_url":
                                 "https://github.com/DrGekoz", "map": {"schema": "wrong"}})
    check("a non-Kinesis schema is rejected", bad_schema[0], 400)

    bad_gesture = post("/maps", {"title": "Bad", "author_name": "x", "github_url":
                                 "https://github.com/DrGekoz",
                                 "map": {"schema": "kinesis.gesture-map", "version": 1,
                                         "bindings": [{"gesture": {"pose": "banana"},
                                                       "action": {"type": "none"}}]}})
    check("an impossible gesture is rejected", bad_gesture[0], 400)

    bad_github = post("/maps", {"title": "Bad", "author_name": "x", "github_url": "not-a-link",
                                "map": gmap.to_json()})
    check("a non-GitHub link is rejected", bad_github[0], 400)

    missing_title = post("/maps", {"author_name": "x", "github_url":
                                   "https://github.com/DrGekoz", "map": gmap.to_json()})
    check("a missing title is rejected", missing_title[0], 400)

    print()
    print("=== THE DRIFT BUG: mouse_hold ===")
    drag_map = GestureMap.from_json(gmap.to_json())
    drag_map.name = "Drag map"
    drag_map.bindings.append(
        __import__("kinesis.gesture_map", fromlist=["Binding"]).Binding(
            id="drag-test",
            gesture=__import__("kinesis.gesture_map", fromlist=["Gesture"]).Gesture(posed="four"),
            action=__import__("kinesis.gesture_map", fromlist=["Action"]).Action(
                kind="mouse_hold", button="left")))
    check("the client accepts mouse_hold (a drag)", validate(drag_map.to_json()), [])
    status, body = post("/maps", {"title": "Drag map", "author_name": "Joseph Williams",
                                 "github_url": "https://github.com/DrGekoz",
                                 "map": drag_map.to_json()})
    check("the WORKER accepts mouse_hold too (client and server agree)", status, 201)
    if status != 201:
        print(f"      worker said: {body.get('problems') or body.get('error')}")

    print()
    print("=== two maps with the same title both survive ===")
    # a unique title each run, so the first submit really does get the clean slug rather than a
    # de-duplicated one left over from an earlier run against the same stub
    unique = f"Same Title {int(time.time())}"
    first = marketplace.submit_map(cfg, unique, "one", "Joseph Williams",
                                   "https://github.com/DrGekoz", gmap)
    second = marketplace.submit_map(cfg, unique, "two", "Joseph Williams",
                                    "https://github.com/DrGekoz", gmap)
    check("the first gets the clean slug", first.get("slug"),
          marketplace_slugify(unique))
    check("the second is de-duplicated", second.get("slug") != first.get("slug"), True)

    print()
    print(f"{sum(results)}/{len(results)} marketplace checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
