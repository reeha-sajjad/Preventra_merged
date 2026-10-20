"""
The on-site agent: for a hospital that will not let any server connect to its
database. It runs inside the hospital, reads the database with the same code
the worker uses (ingestion/pull.py), and pushes what it read to Preventra's
ingest API with the hospital's credential - outbound only.

    python -m ingestion.agent --config agent.json            one run (schedule it nightly)
    python -m ingestion.agent --config agent.json --dry-run  read and report, send nothing

agent.json:

    {
      "api_url": "https://preventra.example-hospital.org",
      "token_env": "PREVENTRA_INGEST_TOKEN",          the ing_... credential, from the environment
      "database": {"dialect": "mssql", "host": "his-db", "port": 1433, "database": "HIS",
                   "username": "preventra_ro", "password_env": "HIS_DB_PASSWORD"},
      "queries": {
        "encounters": "SELECT VisitID AS encounter_key, MRN AS patient_key, ... FROM dbo.Discharges WHERE Updated >= :since",
        "diagnoses": "SELECT VisitID AS encounter_key, ... FROM dbo.Diagnoses d JOIN ... WHERE ... >= :since"
      },
      "state_file": "preventra_agent_state.json",
      "overlap_hours": 2,
      "first_since": "2026-01-01"
    }

Column names are the hospital's own - its data profile on the Data page maps
them - except the two keys: name them encounter_key and patient_key, so the
agent can send large nights in parts that keep each discharge whole.

Needs Python 3.10+, SQLAlchemy and the driver for the database (requirements-api.txt);
nothing else. Secrets come from the environment, never from agent.json.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta

from ingestion import pull

PART_SIZE = 500          # discharges per request - the ingest API's limit


def parts(rows: dict, size: int = PART_SIZE) -> list:
    """Split one night's rows into requests of at most `size` discharges, each
    carrying the diagnoses, labs, ... of its own discharges and its patients.
    Weekly check-ins go with the last part, after every discharge."""
    encounters = rows.get("encounters") or []
    if len(encounters) <= size:
        return [rows] if any(rows.values()) else []
    if any("encounter_key" not in r for r in encounters):
        raise SystemExit("More than 500 discharges in one run: name the key columns encounter_key and "
                         "patient_key in the queries, so the agent can send them in parts.")
    out = []
    for i in range(0, len(encounters), size):
        chunk = encounters[i:i + size]
        keys = {r["encounter_key"] for r in chunk}
        people = {r.get("patient_key") for r in chunk}
        part = {"encounters": chunk}
        for table, table_rows in rows.items():
            if table in ("encounters", "weeks"):
                continue
            field = "patient_key" if table == "patients" else "encounter_key"
            wanted = people if table == "patients" else keys
            part[table] = [r for r in table_rows if r.get(field) in wanted]
        out.append(part)
    if rows.get("weeks"):
        out[-1]["weeks"] = rows["weeks"]
    return out


def _csv(rows: list) -> bytes:
    columns = list(dict.fromkeys(c for r in rows for c in r))
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _multipart(files: dict) -> tuple:
    boundary = uuid.uuid4().hex
    body = io.BytesIO()
    for name, rows in files.items():
        if not rows:
            continue
        body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                   f"filename=\"{name}.csv\"\r\nContent-Type: text/csv\r\n\r\n".encode())
        body.write(_csv(rows))
        body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    return body.getvalue(), f"multipart/form-data; boundary={boundary}"


def send(api_url: str, token: str, part: dict, key: str) -> dict:
    data, content_type = _multipart(part)
    req = urllib.request.Request(f"{api_url.rstrip('/')}/ingest/v1/files", data=data, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": content_type, "Idempotency-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"The ingest API refused part {key}: {exc.code} {exc.read()[:300]!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Read the hospital database and send it to Preventra.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    with open(args.config) as fh:
        cfg = json.load(fh)
    state_file = cfg.get("state_file", "preventra_agent_state.json")
    state = json.load(open(state_file)) if os.path.exists(state_file) else {}

    started = datetime.now()                     # the database's own (local) time
    if state.get("watermark"):
        since = datetime.fromisoformat(state["watermark"]) - timedelta(hours=cfg.get("overlap_hours", 2))
    else:
        since = datetime.fromisoformat(cfg.get("first_since") or (started - timedelta(days=30)).date().isoformat())

    d = cfg["database"]
    url = pull.build_url(d["dialect"], d["host"], d.get("port"), d["database"], d["username"],
                         os.environ.get(d.get("password_env", "HIS_DB_PASSWORD"), ""))
    try:
        rows = pull.fetch(url, cfg["queries"], since)
    except pull.PullError as exc:
        sys.exit(f"Reading the database failed: {exc}")
    counts = {t: len(r) for t, r in rows.items()}
    print(f"[agent] read since {since:%Y-%m-%d %H:%M}: " + ", ".join(f"{t} {n:,}" for t, n in counts.items()))
    if args.dry_run:
        return

    token = os.environ.get(cfg.get("token_env", "PREVENTRA_INGEST_TOKEN"), "")
    if not token:
        sys.exit("No ingest credential in the environment.")
    run_id = started.strftime("%Y%m%d%H%M%S")
    for i, part in enumerate(parts(rows)):
        result = send(cfg["api_url"], token, part, f"agent-{run_id}-{i}")
        enc = result.get("encounters") or {}
        print(f"[agent] part {i + 1}: {enc.get('accepted', 0)} discharges accepted, "
              f"{len(enc.get('rejected') or [])} rejected (batch {enc.get('batch_id', '-')})")
    with open(state_file, "w") as fh:
        json.dump({"watermark": started.isoformat(), "last_counts": counts}, fh)


if __name__ == "__main__":
    main()
