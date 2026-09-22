# DataKollecta-DataBackup

Exports all Supabase data for one or more projects (matched by `projects.slug`,
e.g. `prismcss2026`) to CSV files: one per form/CRF table (`hh_info.csv`,
`hh_members.csv`, `sleeping_structure.csv`, `nets.csv`, ...) plus
`formchanges.csv`, per project.

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
- `PROJECT_CODES` — comma-separated list of project slugs to back up, e.g.
  `prismcss2026,eduassessment,critical_malaria`

## Run

```bash
python export_data.py
```

Each run backs up every project listed in `PROJECT_CODES`. CSV files for each
project are written to
`<project_slug>/<timestamp>/` under
`ProtonDrive....DataKollecta-Backup/output`
(synced via ProtonDrive), with all projects in the same run sharing one
timestamp. If a slug in `PROJECT_CODES` doesn't match any project, the script
prints the available slugs and stops (projects processed earlier in the list
keep their output).
