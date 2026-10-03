# Stack

**Status:** adopted 2026-10-03.

## Decision

- **Back end:** Django, serving both the JSON API required by tadmor's
  `spec/api.md` and the user interface.
- **User interface:** server-rendered Django templates. No SPA, no npm, no
  JavaScript build step. If a small amount of client-side behaviour is
  needed, it is handwritten, or at most one vendored JavaScript file (such
  as htmx), which needs a conversation first.
- **Database:** Postgres 17 with the shared schema from tadmor's
  `db/migrations/`.

## Why Django

Django is the most widely used Python web framework, and it costs almost
nothing on the supply-chain metrics. Its runtime closure on Linux is two
packages, both pure Python:

| Package | Purpose | Required dependencies (Linux, Python 3.13+) | Publisher |
| ------- | ------- | -------------------------------------------- | --------- |
| `django` | framework | `asgiref`, `sqlparse` | Django project |
| `asgiref` | async/sync bridging | none | Django project |
| `sqlparse` | SQL script splitting | none | independent maintainer |
| `psycopg` (v3, pure Python) | Postgres driver, linked to the OS's libpq | none | Daniele Varrazzo |
| `gunicorn` | production WSGI server | none | gunicorn project |

That is five packages from about four publishers. Alternatives were no
leaner: Flask is about six packages from one organization, FastAPI adds
pydantic and its compiled Rust core, and a stdlib-only server is not a
credible production stack.

## Why server-rendered templates

In tadmor the npm tree behind the React front end carries nearly all of the
supply-chain surface. Django's template engine is part of Django, so a
server-rendered UI removes that tree entirely rather than reproducing it.
`spec/README.md` requires each counterpart to have a UI of its own and allows
server-rendered pages; the JSON API remains mandatory alongside it.

## Permitted packages

The five packages above, and nothing else, without a conversation first. In
particular:

- **No Django REST Framework.** Plain views returning `JsonResponse` cover
  the API.
- **No `psycopg[binary]` or `psycopg-c`.** Use pure `psycopg` with Debian's
  `libpq5` (the OS is out of scope for the metrics).
- **No pytest.** Django's test runner (stdlib `unittest`) is enough.
- **No mypy or django-stubs** for now; they would add several build-time
  packages.

## Supply-chain posture

- **Vendored and committed.** Third-party source lives under `vendor/` in
  the repo and is put on `sys.path` directly, so a clean clone runs with no
  PyPI access and no pip. Pure-Python packages execute no code at install
  time, because there is no install step.
- **Pinned.** Exact versions and their wheel hashes are recorded in a
  lockfile alongside `vendor/`.
- **Cooldown.** As with tadmor's pnpm policy, no version is adopted until it
  has been published for at least 7 days.
- **Toolchain:** CPython 3.13 or later (Debian 13 ships 3.13). On 3.13+
  neither `asgiref` nor `psycopg` needs `typing-extensions`.

## Consequences for the shared schema

The users, sessions, and other tables the spec relies on are defined by the
shared schema, so Django's `contrib` apps that bring their own tables
(`auth`, `sessions`, `admin`, `contenttypes`) are not used. Authentication
and sessions are implemented in this repo over the shared tables, as tadmor
does. Migrations are applied by a small runner of our own that follows
`spec/README.md` (every `*.up.sql` in lexical order, recorded in
`schema_migrations`).

## Open questions

- **PDF generation.** tadmor hand-writes its PDFs (`internal/pdf`, no
  dependency). The default is to port that approach rather than adopt a PDF
  library.
