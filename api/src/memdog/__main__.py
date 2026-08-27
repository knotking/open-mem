"""`python -m memdog bootstrap` -- create a tenant and print a credential."""

from __future__ import annotations

import asyncio
import sys

from .bootstrap import bootstrap_tenant
from .config import load_settings
from .db import create_pool, migrate


async def _bootstrap(email: str, scope: str) -> None:
    settings = load_settings()
    pool = await create_pool(settings)
    await migrate(pool, settings)
    tenant = await bootstrap_tenant(pool, email=email, connection_scope=scope)
    await pool.close()
    print(f"org_id      {tenant.org_id}")
    print(f"project_id  {tenant.project_id}")
    print(f"user_id     {tenant.user_id}")
    print(f"producer_id {tenant.producer_id}")
    print(f"api_key     {tenant.api_key}    # shown once, never again")


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "bootstrap":
        print("usage: python -m memdog bootstrap [email] [personal|shared]", file=sys.stderr)
        return 2
    email = sys.argv[2] if len(sys.argv) > 2 else "owner@example.com"
    scope = sys.argv[3] if len(sys.argv) > 3 else "personal"
    asyncio.run(_bootstrap(email, scope))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
