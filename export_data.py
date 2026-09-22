"""Export all Supabase data for one or more projects (by project code / slug)
to CSV files.

Pulls the CRF/form definitions, submissions (flattened from JSONB), and
formchanges audit log for each project and writes one CSV per table into
<project_slug>/<timestamp>/ under the BACKUP_OUTPUT_ROOT directory (see .env).
A project's export is retried a few times on transient failures (network
blips, a laptop briefly asleep) before the run is reported as failed.
Appends a summary to backup.log and emails that summary on every run -
success or failure (see .env for SMTP settings).

Usage:
    python export_data.py
"""
import csv
import os
import smtplib
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, ClientOptions, create_client

PAGE_SIZE = 1000

# Field connections (e.g. Uganda) can be slow but usually still come through,
# so this is generous on purpose rather than failing fast.
POSTGREST_TIMEOUT_SECONDS = 300

# A project's export (fetch + write) is retried this many times total before
# the run is reported as failed - covers a transient blip (wifi drop, laptop
# briefly asleep) without needing a person to re-run it by hand.
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 20

BASE_DIR = Path(__file__).resolve().parent
LOG_PATH = BASE_DIR / "backup.log"


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    options = ClientOptions(postgrest_client_timeout=POSTGREST_TIMEOUT_SECONDS)
    return create_client(url, key, options=options)


def get_project(client: Client, project_code: str) -> dict:
    resp = client.table("projects").select("id, name, slug").eq("slug", project_code).execute()
    if resp.data:
        return resp.data[0]

    all_projects = client.table("projects").select("slug").execute().data
    available = ", ".join(sorted(p["slug"] for p in all_projects if p.get("slug")))
    raise RuntimeError(
        f"No project found with slug '{project_code}'. "
        f"Available project slugs: {available or '(none found)'}"
    )


def get_crf_field_order(client: Client, project_id: str) -> dict[str, list[str]]:
    resp = client.table("crfs").select("table_name, fields").eq("project_id", project_id).execute()
    field_order: dict[str, list[str]] = {}
    for row in resp.data:
        fields = row.get("fields") or []
        names = [f["name"] for f in fields if isinstance(f, dict) and "name" in f]
        field_order[row["table_name"]] = names
    return field_order


def fetch_paginated(client: Client, table: str, project_id: str) -> list[dict]:
    rows: list[dict] = []
    start = 0
    while True:
        resp = (
            client.table(table)
            .select("*")
            .eq("project_id", project_id)
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        )
        batch = resp.data
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return rows


def build_column_order(preferred: list[str], rows: list[dict]) -> list[str]:
    seen = set(preferred)
    columns = list(preferred)
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                columns.append(key)
    return columns


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def export_submissions(client: Client, project_id: str, output_dir: Path) -> None:
    field_order = get_crf_field_order(client, project_id)
    submissions = fetch_paginated(client, "submissions", project_id)

    by_table: dict[str, list[dict]] = {}
    for sub in submissions:
        table_name = sub["table_name"]
        by_table.setdefault(table_name, []).append(sub.get("data") or {})

    for table_name, data_rows in by_table.items():
        preferred = field_order.get(table_name, [])
        columns = build_column_order(preferred, data_rows)
        write_csv(output_dir / f"{table_name}.csv", columns, data_rows)
        print(f"Wrote {len(data_rows)} rows to {table_name}.csv")


def export_formchanges(client: Client, project_id: str, output_dir: Path) -> None:
    rows = fetch_paginated(client, "formchanges", project_id)
    columns = build_column_order(
        [
            "formchanges_uuid",
            "record_uuid",
            "tablename",
            "fieldname",
            "oldvalue",
            "newvalue",
            "surveyor_id",
            "changed_at",
        ],
        rows,
    )
    write_csv(output_dir / "formchanges.csv", columns, rows)
    print(f"Wrote {len(rows)} rows to formchanges.csv")


def send_email(subject: str, body: str) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ["SMTP_USERNAME"]
    password = os.environ["SMTP_PASSWORD"]
    sender = os.environ.get("SMTP_FROM", username)
    recipients = os.environ["NOTIFY_EMAIL"]
    use_ssl = os.environ.get("SMTP_USE_SSL", "false").strip().lower() == "true"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipients
    msg.set_content(body)

    smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_cls(host, port) as server:
        if not use_ssl:
            server.starttls()
        server.login(username, password)
        server.send_message(msg)


def log_run(lines: list[str]) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n")


def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    run_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backed_up: list[str] = []
    failed_project_code: str | None = None
    error_message: str | None = None

    try:
        project_codes = [code.strip() for code in os.environ["PROJECT_CODES"].split(",") if code.strip()]
        if not project_codes:
            raise RuntimeError("PROJECT_CODES is empty. Set it to a comma-separated list of project slugs.")
        output_root = Path(os.environ["BACKUP_OUTPUT_ROOT"])
        client = get_client()

        for project_code in project_codes:
            failed_project_code = project_code
            project_output_dir = output_root / project_code / run_timestamp

            last_exc: Exception | None = None
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    project = get_project(client, project_code)
                    project_id = project["id"]
                    print(f"Exporting project '{project['name']}' (slug={project['slug']}, id={project_id})")

                    project_output_dir.mkdir(parents=True, exist_ok=True)
                    export_submissions(client, project_id, project_output_dir)
                    export_formchanges(client, project_id, project_output_dir)
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    print(f"Attempt {attempt}/{MAX_ATTEMPTS} for '{project_code}' failed: {exc}")
                    if attempt < MAX_ATTEMPTS:
                        time.sleep(RETRY_DELAY_SECONDS)
            if last_exc is not None:
                raise last_exc

            print(f"Done. Files written to {project_output_dir}")
            backed_up.append(project["slug"])

        failed_project_code = None
    except Exception as exc:
        error_message = str(exc)

    if error_message:
        subject = f"DataKollecta backup FAILED - {run_timestamp}"
        lines = [
            f"Run: {run_timestamp}",
            "Status: FAILED",
            f"Backed up before failure: {', '.join(backed_up) or '(none)'}",
            f"Failed on project: {failed_project_code or '(before project loop started)'}",
            f"Error: {error_message}",
        ]
    else:
        subject = f"DataKollecta backup succeeded - {run_timestamp}"
        lines = [
            f"Run: {run_timestamp}",
            "Status: SUCCESS",
            f"Backed up: {', '.join(backed_up)}",
        ]

    body = "\n".join(lines)
    print(body)

    try:
        log_run(lines)
    except Exception as exc:
        print(f"Warning: failed to write {LOG_PATH}: {exc}")

    try:
        send_email(subject, body)
    except Exception as exc:
        print(f"Warning: failed to send notification email: {exc}")

    if error_message:
        sys.exit(1)


if __name__ == "__main__":
    main()
