"""One-shot helper: check whether the marketplace is ready to deploy, and say exactly what is missing.

    .venv\Scripts\python tools\deploy_check.py

Read-only. It never creates anything and never prints a credential. It exists because the
difference between "the token is invalid" and "the token cannot write" is invisible from the app -
both surface as an empty list from the API - and getting that wrong sends you chasing the wrong
problem.
"""
from __future__ import annotations

import importlib.util
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.cloudflare.com/client/v4"

spec = importlib.util.spec_from_file_location("deploy", ROOT / "cloudflare" / "deploy.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


def get(env, path):
    request = urllib.request.Request(
        f"{API}{path}", headers={"Authorization": f"Bearer {env.get('CLOUDFLARE_API_TOKEN','')}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            import json
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        import json
        try:
            return json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:                                   # noqa: BLE001
            return {"success": False, "errors": [{"message": f"HTTP {exc.code}"}]}
    except Exception as exc:                                # noqa: BLE001
        return {"success": False, "errors": [{"message": str(exc)}]}


def main() -> int:
    env = deploy.read_env()
    account = env.get("CLOUDFLARE_ACCOUNT_ID", "")
    print("=== credentials ===")
    print(f"account id ..... {'set' if account else 'MISSING'}")
    print(f"api token ...... {'set (' + str(len(env.get('CLOUDFLARE_API_TOKEN',''))) + ' chars)' if env.get('CLOUDFLARE_API_TOKEN') else 'MISSING'}")
    if not account or not env.get("CLOUDFLARE_API_TOKEN"):
        print("\nput CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN in .env")
        return 1

    verify = get(env, "/user/tokens/verify")
    print(f"token verify ... {'ok' if verify.get('success') else 'FAILED (code 1000 - normal for some token types)'}")

    print()
    print("=== what this token can do (read vs write) ===")
    # an EMPTY result is not proof of failure: Cloudflare returns success:true with [] for an
    # unauthenticated read too. So the read is confirmed against an endpoint that echoes the account.
    who = get(env, f"/accounts/{account}")
    account_name = (who.get("result") or {}).get("name", "")
    print(f"read  /accounts  {'ok  (' + account_name + ')' if who.get('success') and account_name else 'FAILED'}")

    dbs = get(env, f"/accounts/{account}/d1/database")
    names = [d.get("name") for d in (dbs.get("result") or [])]
    print(f"read  /d1        {'ok' if dbs.get('success') else 'FAILED'}  databases: {names or 'none'}")

    if deploy.DB_NAME in names:
        print(f"\n>>> the database {deploy.DB_NAME} already exists - the remaining step is the Worker.")
        print(">>> re-run:  .venv\\Scripts\\python cloudflare\\deploy.py")
        return 0

    print()
    print("=== the one thing only you can do ===")
    print("An R2 API token can read D1 but cannot create one. That is a scope limit, not a")
    print("misconfiguration, so there is nothing to retry here. Either:\n")
    print("  A) create the database by hand (no new token needed, ~20 seconds)")
    print(f"     dashboard -> Workers & Pages -> D1 SQL Database -> Create -> name it exactly")
    print(f"     {deploy.DB_NAME}")
    print("     then run:  .venv\\Scripts\\python cloudflare\\deploy.py")
    print("     (it finds the existing database and continues from there)\n")
    print("  B) or make an API token that can write:")
    print("     dashboard -> My Profile -> API Tokens -> Create Token -> Edit Account")
    print("     permissions: Account > D1 > Edit   and   Account > Workers Scripts > Edit")
    print("     then put it in .env as CLOUDFLARE_API_TOKEN and re-run deploy.py")
    return 1


if __name__ == "__main__":
    sys.exit(main())
