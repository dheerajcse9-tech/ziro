"""Management commands: `python -m app.cli [migrate|seed|reset|run-tests]`."""
from __future__ import annotations

import sys

import app.models  # noqa: F401
from app.db.session import SessionLocal


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "help"
    if cmd == "migrate":
        from alembic import command
        from alembic.config import Config

        command.upgrade(Config("alembic.ini"), "head")
        print("migrations applied")
        return 0
    if cmd in ("seed", "reset"):
        from app.services.seed import reset_database, seed

        with SessionLocal() as db:
            if cmd == "reset":
                reset_database(db)
            print(seed(db))
        return 0
    if cmd == "run-tests":
        from app.services.test_lab import SCENARIOS, run_scenario

        ok = True
        with SessionLocal() as db:
            for tid in SCENARIOS:
                r = run_scenario(db, tid, None)
                ok &= r.passed
                print(f"{tid} {'PASS' if r.passed else 'FAIL'} ({sum(c['passed'] for c in r.checks)}/{len(r.checks)} checks)")
                if not r.passed:
                    print(r.failure_details)
        return 0 if ok else 1
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
