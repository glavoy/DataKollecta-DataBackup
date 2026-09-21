"""Scratch check: verify projects <-> submissions link by project_id. Not part of the export tool."""
import os
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
project_code = os.environ["PROJECT_CODE"]

projects = client.table("projects").select("id, name, slug").execute().data
print(f"Total projects: {len(projects)}")
for p in projects:
    print(" -", p)

match = [p for p in projects if p.get("slug") == project_code]
if not match:
    print(f"\nNo project with slug == '{project_code}'")
else:
    project = match[0]
    project_id = project["id"]
    print(f"\nMatched project: {project}")

    count_resp = (
        client.table("submissions")
        .select("id", count="exact")
        .eq("project_id", project_id)
        .limit(1)
        .execute()
    )
    print(f"submissions count for project_id={project_id}: {count_resp.count}")

    sample = (
        client.table("submissions")
        .select("id, project_id, table_name, record_id")
        .eq("project_id", project_id)
        .limit(3)
        .execute()
        .data
    )
    print("Sample rows:")
    for row in sample:
        print(" -", row)
