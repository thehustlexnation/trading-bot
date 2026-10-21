"""Read-only acceptance report for an existing streaming journal."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from realtime.acceptance import session_report
from realtime.service import database_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, help="New York exchange date, YYYY-MM-DD")
    parser.add_argument("--expected-sha")
    args = parser.parse_args()
    load_dotenv()
    url = database_url()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    url = make_url(url)
    if url.get_backend_name() == "sqlite":
        path = Path(url.database or "")
        if not path.is_file():
            print(json.dumps({"status": "UNAVAILABLE", "reason": "Existing journal database not found."}))
            return 2
        url = url.set(database=f"file:{path.resolve()}").update_query_dict({"mode": "ro", "uri": "true"})
    engine = create_engine(url, pool_pre_ping=True)
    try:
        report = session_report(engine, args.session, datetime.now(timezone.utc), args.expected_sha)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] == "PASS" else 1 if report["status"] == "FAIL" else 2
    except (SQLAlchemyError, ValueError) as exc:
        print(json.dumps({"status": "UNAVAILABLE", "error_type": type(exc).__name__,
                          "reason": "Could not read the requested session from the existing journal."}))
        return 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
