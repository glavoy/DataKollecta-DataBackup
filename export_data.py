"""Export all Supabase data for a project (by project code / slug) to CSV files.

Pulls the CRF/form definitions, submissions (flattened from JSONB), and
formchanges audit log for a single project and writes one CSV per table
into the output/ directory.

Usage:
    python export_data.py
"""
import csv
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, create_client

PAGE_SIZE = 1000

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_ROOT = BASE_DIR / "output"
OUTPUT_DIR = OUTPUT_ROOT / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


def get_project(client: Client, project_code: str) -> dict:
    resp = client.table("projects").select("id, name, slug").eq("slug", project_code).execute()
    if resp.data:
        return resp.data[0]

    all_projects = client.table("projects").select("slug").execute().data
    available = ", ".join(sorted(p["slug"] for p in all_projects if p.get("slug")))
    sys.exit(
        f"No project found with slug '{project_code}'.\n"
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


def export_submissions(client: Client, project_id: str) -> None:
    field_order = get_crf_field_order(client, project_id)
    submissions = fetch_paginated(client, "submissions", project_id)

    by_table: dict[str, list[dict]] = {}
    for sub in submissions:
        table_name = sub["table_name"]
        by_table.setdefault(table_name, []).append(sub.get("data") or {})

    for table_name, data_rows in by_table.items():
        preferred = field_order.get(table_name, [])
        columns = build_column_order(preferred, data_rows)
        write_csv(OUTPUT_DIR / f"{table_name}.csv", columns, data_rows)
        print(f"Wrote {len(data_rows)} rows to {table_name}.csv")


def export_formchanges(client: Client, project_id: str) -> None:
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
    write_csv(OUTPUT_DIR / "formchanges.csv", columns, rows)
    print(f"Wrote {len(rows)} rows to formchanges.csv")


def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    project_code = os.environ["PROJECT_CODE"]

    client = get_client()
    project = get_project(client, project_code)
    project_id = project["id"]
    print(f"Exporting project '{project['name']}' (slug={project['slug']}, id={project_id})")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    export_submissions(client, project_id)
    export_formchanges(client, project_id)
    print(f"Done. Files written to {OUTPUT_DIR}")

    publish_latest()
    print(f"Latest copy published to {OUTPUT_ROOT}")


def publish_latest() -> None:
    """Copy this run's CSVs into output/ root as the 'latest' extract."""
    for stale in OUTPUT_ROOT.glob("*.csv"):
        stale.unlink()
    for csv_file in OUTPUT_DIR.glob("*.csv"):
        shutil.copy2(csv_file, OUTPUT_ROOT / csv_file.name)


if __name__ == "__main__":
    main()
