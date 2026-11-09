"""Admin commands, run from the project root:

  .venv/bin/python server/manage.py make-admin you@example.com   make an existing account an admin
  .venv/bin/python server/manage.py sync-agents                  copy agents/*.md over the shared agents in the database
"""
import asyncio
import sys

import asyncpg

import config
import db


async def make_admin(email: str):
    con = await asyncpg.connect(config.DATABASE_URL)
    try:
        row = await con.fetchrow("UPDATE users SET role = 'admin', confirmed_at = coalesce(confirmed_at, now()) "
                                 "WHERE email = $1 RETURNING email", email.strip().lower())
        print(f"{row['email']} is now an admin." if row else f"No account with the email {email}. Register it first.")
    finally:
        await con.close()


async def sync_agents():
    con = await asyncpg.connect(config.DATABASE_URL)
    try:
        async with con.transaction():
            print(f"Copied {await db.seed_from_files(con, replace=True)} agents from {config.AGENTS_DIR} into the shared agents.")
    finally:
        await con.close()


def main():
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "make-admin":
        asyncio.run(make_admin(args[1]))
    elif args == ["sync-agents"]:
        asyncio.run(sync_agents())
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
