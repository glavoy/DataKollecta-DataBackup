# prismcss_download_data

Exports all Supabase data for a project (matched by `projects.slug`, e.g.
`prismcss2026`) to CSV files: one per form/CRF table (`hh_info.csv`,
`hh_members.csv`, `sleeping_structure.csv`, `nets.csv`, ...) plus
`formchanges.csv`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and fill in:

- `SUPABASE_URL` — Project Settings → API → Project URL
- `SUPABASE_SERVICE_ROLE_KEY` — Project Settings → API → service_role key (bypasses RLS; keep this secret, never commit it)
- `PROJECT_CODE` — the project's slug, e.g. `prismcss2026`

## Run

```bash
python export_data.py
```

CSV files are written to `output/`. If `PROJECT_CODE` doesn't match any
project, the script prints the available slugs so you can correct it.
