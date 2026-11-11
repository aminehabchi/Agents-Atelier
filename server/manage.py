"""Admin commands, run from the project root:

  .venv/bin/python server/manage.py make-admin you@example.com   make an existing account an admin
  .venv/bin/python server/manage.py migrate                      apply pending migrations (the server also does it on start)
  .venv/bin/python server/manage.py migrations                   list migrations and whether each one is applied
  .venv/bin/python server/manage.py make-agents-migration [name] after editing agents/*.md or agents/_presets.json:
                                                                 write the next migration with what changed
  .venv/bin/python server/manage.py paypal-setup                 create the PayPal product and the Pro and Agency plans
                                                                 (needs PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env)
"""
import asyncio
import sys

import asyncpg

import config
import migrate


async def make_admin(email: str):
    con = await asyncpg.connect(config.DATABASE_URL)
    try:
        row = await con.fetchrow("UPDATE users SET role = 'admin', confirmed_at = coalesce(confirmed_at, now()) "
                                 "WHERE email = $1 RETURNING email", email.strip().lower())
        print(f"{row['email']} is now an admin." if row else f"No account with the email {email}. Register it first.")
    finally:
        await con.close()


async def run_migrations():
    con = await asyncpg.connect(config.DATABASE_URL)
    try:
        done = await migrate.apply_all(con)
        print("Applied: " + ", ".join(done) if done else "Nothing to apply: the database is up to date.")
    finally:
        await con.close()


async def list_migrations():
    con = await asyncpg.connect(config.DATABASE_URL)
    try:
        seen = await migrate.applied(con)
    finally:
        await con.close()
    for m in migrate.available():
        row = seen.get(m["version"])
        state = "pending" if not row else ("applied " + row["applied_at"].strftime("%Y-%m-%d %H:%M")
                                           + ("  (changed since: add a new migration instead)" if row["checksum"] != m["checksum"] else ""))
        print(f"{m['name']:<40} {state}")


def make_agents_migration(name: str):
    path = migrate.write_agents_migration(name)
    if path:
        print(f"Wrote {path.relative_to(migrate.DIR.parent.parent)}. It's applied the next time the server starts, "
              "or now with: manage.py migrate")
    else:
        print("Nothing changed in agents/*.md or agents/_presets.json since the last agents migration.")


async def paypal_setup():
    import billing
    if not (config.PAYPAL_CLIENT_ID and config.PAYPAL_CLIENT_SECRET):
        raise SystemExit("Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env first (developer.paypal.com → Apps & Credentials).")
    ids = await billing.setup_plans()
    print(f"Created on PayPal ({config.PAYPAL_MODE}). Add these lines to .env, then restart the server:\n")
    for k, v in ids.items():
        print(f"{k}={v}")


def main():
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "make-admin":
        asyncio.run(make_admin(args[1]))
    elif args == ["migrate"]:
        asyncio.run(run_migrations())
    elif args == ["migrations"]:
        asyncio.run(list_migrations())
    elif args[:1] == ["make-agents-migration"] and len(args) <= 2:
        make_agents_migration(args[1] if len(args) == 2 else "shared_agents")
    elif args == ["paypal-setup"]:
        asyncio.run(paypal_setup())
    elif args == ["sync-agents"]:
        raise SystemExit("sync-agents was replaced by migrations: run `manage.py make-agents-migration`, "
                         "then restart the server (or run `manage.py migrate`).")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
