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
- `BACKUP_OUTPUT_ROOT` — absolute path to the directory backups are written
  under (e.g. a synced cloud-storage folder)
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USE_SSL`, `SMTP_USERNAME`, `SMTP_PASSWORD`,
  `SMTP_FROM`, `NOTIFY_EMAIL` — SMTP settings used to email a summary on every
  run. `SMTP_USE_SSL=true` connects with implicit TLS (typically port 465);
  otherwise the script connects plain and upgrades with STARTTLS (typically
  port 587). `NOTIFY_EMAIL` accepts a comma-separated list of recipients.

## Run

```bash
python export_data.py
```

Each run backs up every project listed in `PROJECT_CODES`. CSV files for each
project are written to `<project_slug>/<timestamp>/` under
`BACKUP_OUTPUT_ROOT`, with all projects in the same run sharing one
timestamp. If a slug in `PROJECT_CODES` doesn't match any project, the run is
recorded as failed and processing stops there (projects backed up earlier in
the list keep their output).

Every run — success or failure — appends a summary to `backup.log` (slugs
backed up, or what failed and why) and emails the same summary to
`NOTIFY_EMAIL`. A failed email send is logged to the console but does not
fail the run; a failed backup run exits with a non-zero status code.

**Transient failures are retried.** A project's export (fetch + write) is
retried up to `MAX_ATTEMPTS` times (default 3), `RETRY_DELAY_SECONDS` apart
(default 20s), before the whole run is reported as failed — covers a wifi
blip or a laptop briefly asleep mid-run without needing a manual re-run. The
Supabase client also uses a generous request timeout
(`POSTGREST_TIMEOUT_SECONDS`, default 300s) rather than the library default,
since field connections can be slow without actually being dead. All three
are constants at the top of `export_data.py`, not `.env` settings.
