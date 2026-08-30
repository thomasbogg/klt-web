# klt-web

Django booking site + internal admin for **Algarve Beach Apartments**, a real
holiday-apartment rental business in the Algarve, Portugal. This runs the
actual business (properties, owners, managers, accountants, bookings, guests)
— data in the database is real operational data, not fixtures.

Thomas edits `models.py` and other code directly himself and runs Django
management commands in his own terminal in parallel with Claude sessions, so
the working tree can already be mid-change when a session starts. Check
`git status` and pending migrations before assuming a clean slate.

## Environment

- Python venv is **shared across three sibling projects** (klt-web, klt-hooks,
  klt-management-software): `/home/thomas-bogg/apps/.env`. Activate with
  `source /home/thomas-bogg/apps/.env/bin/activate` from this repo before
  running `manage.py`. This is *not* the same as `second-continent/.venv` —
  a different project's venv that happens to sort first alphabetically.
- Database is a **shared remote Railway Postgres** (`ENGINE:
  django.db.backends.postgresql` in `klt_web/settings.py`), not local SQLite.
  Multiple machines/sessions can be pointed at it simultaneously.
- **klt-web itself is not deployed anywhere** — `DEBUG = True`,
  `ALLOWED_HOSTS = []`, no deploy config. It runs as local dev
  (`python manage.py runserver`) on Thomas's own machine(s), pointed at the
  shared Postgres. Practical implication: a migration only needs to run once
  (from any machine) to take effect everywhere; shipping a code change is
  just `git pull` + restart wherever it's running.
- `git push` works via SSH (`git@github.com:thomasbogg/klt-web.git`).
- A separate Flask service, `klt-hooks` (`/home/thomas-bogg/apps/klt-hooks`),
  *is* actually deployed on Railway and handles real external webhooks
  (Revolut). It has its own legacy SQLite DB plus a second, separate
  connection to this same Postgres DB. It does not auto-deploy on klt-web's
  (or its own) `git push` — needs `railway up` run manually from its own repo.

## Working conventions

- **Commit implies push** on this project — when Thomas says "commit," push
  immediately after unless he says otherwise for that instance.
- **`.claude/settings.json`** (the permission allowlist) is intentionally
  tracked and committed, so it travels across machines. Only genuinely safe,
  read-only-in-effect entries belong in it.
- Running the local test suite (`python manage.py test`) and driving a
  browser (Selenium/CDP — screenshots, clicks, full flow walkthroughs) to
  verify changes are both pre-approved — no need to ask first. This does
  *not* extend to direct DB writes/migrations against the shared DB, or other
  destructive ops.
- **Always confirm scope (and whether a backup is wanted) before any
  destructive DB operation** (`DROP`/`TRUNCATE`/bulk `DELETE`, migration
  history surgery) — the DB holds real data and is shared live across
  machines. Check row counts and cross-app foreign keys first; don't assume
  "dev" or "local" implies safe to destroy.
- Default new model `ForeignKey`s to `on_delete=PROTECT` unless there's a
  specific, articulated reason `SET_NULL`/`CASCADE` is correct. A field
  "just being a reference" says nothing about whether deleting its parent row
  should be routine — this already caused one real near-miss data-loss
  incident (a CASCADE on `Platform` silently deleted a live migrated listing
  ID).
- For guest-facing options, prefer fixed/structured choices (radio buttons,
  a small preset catalog) over freeform text fields whenever the space of
  reasonable answers is small and enumerable. This is a two-person operation
  — a freeform field is an open invitation to negotiate that has to be
  granted or declined case by case. Reserve freeform text for genuinely
  unbounded, informational-only input (e.g. an allergy note).
- The `owners`/`staff` CSS convention uses container-level descendant
  selectors (`.owner-filter-form label`, `.staff-field-row > .staff-button`)
  rather than component-scoped classes. A new component-level class
  (`.owner-*`, `.staff-*`) added inside an existing container will often lose
  a specificity fight it looks like it should win — if a shared class
  "doesn't work" only inside one particular container, check for a
  container-level rule beating it on specificity before changing the
  component rule itself.

## Apps

`availability`, `bookings`, `finance`, `guests`, `index`, `klt_web` (project
settings), `libraries`, `media`, `owners`, `properties`, `staff`.
