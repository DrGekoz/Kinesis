"""Put the Gesture-Map marketplace online.

Run it from the Kinesis folder:

    .venv\\Scripts\\python.exe cloudflare\\deploy.py            # create, migrate, seed, publish
    .venv\\Scripts\\python.exe cloudflare\\deploy.py --status   # what exists right now

It needs CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN in ../.env, and the token must be allowed to
create databases and deploy Workers - a read-only token will stop with "Authentication error" and
say so rather than half-finishing. Nothing here prints the token.

Steps, in order, each skipped if it is already done:
  1. create the D1 database (kinesis-gesture-maps)
  2. apply cloudflare/schema.sql
  3. seed the ten shipped Gesture-Maps
  4. publish the Worker, and report its URL
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CF_DIR = ROOT / "cloudflare"
DB_NAME = "kinesis-gesture-maps"
API = "https://api.cloudflare.com/client/v4"


def read_env() -> dict:
    env = {}
    path = ROOT / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


def api(env: dict, path: str, method: str = "GET", payload: dict | None = None) -> dict:
    request = urllib.request.Request(
        f"{API}{path}", method=method,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Authorization": f"Bearer {env['CLOUDFLARE_API_TOKEN']}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:
            return {"success": False, "errors": [{"message": f"HTTP {exc.code}"}]}


def errors(result: dict) -> str:
    return "; ".join(str(e.get("message", e)) for e in (result.get("errors") or [])) or "unknown error"


def main() -> int:
    ap = argparse.ArgumentParser(description="Deploy the Kinesis Gesture-Map marketplace")
    ap.add_argument("--status", action="store_true", help="report what exists and stop")
    args = ap.parse_args()

    env = read_env()
    if not env.get("CLOUDFLARE_API_TOKEN") or not env.get("CLOUDFLARE_ACCOUNT_ID"):
        print("CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN must be in .env")
        return 1
    account = env["CLOUDFLARE_ACCOUNT_ID"]

    # 1 --------------------------------------------------------------- the database
    listing = api(env, f"/accounts/{account}/d1/database")
    if not listing.get("success"):
        print(f"could not list databases: {errors(listing)}")
        print("if that says Authentication error, the token cannot read D1")
        return 1
    found = next((d for d in listing.get("result") or [] if d.get("name") == DB_NAME), None)
    if found:
        print(f"database ...... {DB_NAME} ({found.get('uuid')})")
    elif args.status:
        print(f"database ...... {DB_NAME} does NOT exist yet")
        return 1
    else:
        created = api(env, f"/accounts/{account}/d1/database", "POST", {"name": DB_NAME})
        if not created.get("success"):
            print(f"could not create the database: {errors(created)}")
            print("a token needs 'D1: Edit' to create one. Create it in the dashboard instead:")
            print(f"  Storage & Databases -> D1 -> Create database -> name it {DB_NAME}")
            return 1
        found = created.get("result") or {}
        print(f"created ....... {DB_NAME} ({found.get('uuid')})")

    database_id = str(found.get("uuid") or "")
    if args.status:
        print("status ........ exists; re-run without --status to deploy")
        return 0

    # 2/3 ------------------------------------------------------------- schema and seed
    for label, sql in (("schema", CF_DIR / "schema.sql"), ("seed  ", CF_DIR / "seed.sql")):
        if not sql.is_file():
            print(f"{label} file missing: {sql}")
            continue
        statements = [s.strip() for s in sql.read_text(encoding="utf-8").split(";") if s.strip()]
        done = 0
        for statement in statements:
            if statement.startswith("--"):
                continue
            result = api(env, f"/accounts/{account}/d1/database/{database_id}/query", "POST",
                         {"sql": statement})
            if result.get("success"):
                done += 1
            else:
                print(f"{label}: {errors(result)}  (statement {done + 1})")
                break
        print(f"{label} ........ {done}/{len(statements)} statements applied")

    # 4 ---------------------------------------------------------------- the Worker
    if shutil.which("npx") is None:
        print("npx not found - install Node 18+ to publish the Worker")
        return 1
    toml = (CF_DIR / "wrangler.toml").read_text(encoding="utf-8").replace(
        "REPLACE_WITH_DATABASE_ID", database_id)
    (CF_DIR / "wrangler.toml").write_text(toml, encoding="utf-8")
    print("publishing the Worker...")
    process = subprocess.run(["npx", "--yes", "wrangler@latest", "deploy"], cwd=str(CF_DIR),
                             env={**env, "CLOUDFLARE_API_TOKEN": env["CLOUDFLARE_API_TOKEN"],
                                  "CLOUDFLARE_ACCOUNT_ID": account},
                             capture_output=True, text=True, shell=sys.platform == "win32")
    output = (process.stdout or "") + (process.stderr or "")
    url = ""
    for line in output.splitlines():
        if "workers.dev" in line:
            for token in line.replace("https://", " https://").split():
                if token.startswith("https://") and "workers.dev" in token:
                    url = token.strip().rstrip(".,")
    if process.returncode != 0:
        print(output.strip()[-1500:])
        print("the Worker did not deploy - a token needs 'Workers Scripts: Edit'")
        return 1
    print(f"worker ........ {url or '(check the wrangler output above)'}")
    if url:
        print(f"\npoint the app at it:\n  run.bat --tune marketplace_api={url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
