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

- **Vendored and committed.** Each wheel is unpacked, unmodified, into
  `vendor/site/` and put on `sys.path` directly (`manage.py`, the Makefile,
  and `tools/conformance.sh` do this), so a clean clone runs with no PyPI
  access and no pip. Pure-Python wheels execute no code when unpacked, so
  there is no install-time code to block.
- **Pinned.** `vendor/lock.txt` records each wheel's exact filename and
  sha256. `tools/vendor.py` (standard library only) is the whole toolchain:
  `add NAME==VERSION` looks the wheel up on PyPI and appends it to the lock,
  `sync` downloads every locked wheel, verifies its hash, and unpacks it, and
  `check` verifies `vendor/site/` file by file against each wheel's `RECORD`,
  offline. A dependency change is therefore reviewable as a lock diff plus a
  source diff.
- **Dependency manifest.** `tools/vendor.py manifest` (which `sync` runs)
  writes `dependencies.json`, the manifest tadmor's `tools/measure.py`
  reads (tadmor's `docs/counterpart-metrics.md`): every wheel, its
  category, and the accounts PyPI lists in a role on the project, read
  through PyPI's XML-RPC `package_roles`. `check` also verifies that it
  lists exactly the locked wheels.
- **Cooldown.** `tools/vendor.py add` refuses a version published less than
  7 days ago, as tadmor's pnpm policy does.
- **Toolchain:** CPython 3.13 or later (Debian 13 ships 3.13). On 3.13+
  neither `asgiref` nor `psycopg` needs `typing-extensions`. The operating
  system supplies libpq (`libpq5`), which psycopg's pure-Python
  implementation loads.
- **Hermetic build.** There is no build step: the deployable is the source
  tree. A clean clone runs offline with only the pinned toolchain (level 3
  of tadmor's ladder), and since nothing is compiled or bundled, two
  checkouts of a commit are byte-identical deployables.

| Package | Version | Published | Runtime role |
| ------- | ------- | --------- | ------------ |
| Django | 6.1.1 | 2026-09-02 | framework, templates, ORM, test runner |
| asgiref | 3.12.1 | 2026-07-14 | required by Django |
| sqlparse | 0.6.0 | 2026-08-13 | required by Django |
| psycopg | 3.3.6 | 2026-09-18 | Postgres driver |
| gunicorn | 26.2.0 | 2026-08-24 | production WSGI server |

## How the application uses Django

- **Models over the shared schema.** Every model is unmanaged
  (`managed = False`); generated columns are `GeneratedField`s, and the
  `*_balances` and fulfilment views have models of their own. Reports are
  aggregate SQL over the shared tables and views.
- **One service layer.** `tadmor/services/` holds the business rules and
  raises errors that carry their HTTP status. The JSON API and the HTML UI
  both call it, so the UI shows exactly the API's rules and messages
  (domain §13 G5). Each call runs in one transaction.
- **Our own sessions.** The shared `sessions` and `users` tables back a
  small middleware; passwords use Django's PBKDF2 hasher. `contrib.auth`,
  `contrib.sessions`, `contrib.admin`, and `contrib.contenttypes` are not
  installed, so Django adds no tables to the schema.
- **Static files.** One stylesheet and one script, served by
  `django.views.static.serve` from `tadmor/static/`. That view is simple
  rather than fast; behind a caching proxy it is enough for two small
  files, and it avoids a package such as WhiteNoise.
- **JavaScript.** One handwritten file, `tadmor/static/app.js`, for the
  line-item editor: adding and removing lines, filling a line from its
  product and tax code, and previewing totals with exact BigInt decimal
  arithmetic that rounds as the server does. Everything else works without
  script. A same-origin Content Security Policy is set on every response.
- **Tests.** Django's runner, with a custom runner (`tadmor/testing.py`)
  that applies the shared migrations to the test database. The tests walk
  the UI checklist of domain §13; the API is covered by the conformance
  suite.

## Consequences for the shared schema

The users, sessions, and other tables the spec relies on are defined by the
shared schema, so Django's `contrib` apps that bring their own tables
(`auth`, `sessions`, `admin`, `contenttypes`) are not used. Authentication
and sessions are implemented in this repo over the shared tables, as tadmor
does. Migrations are applied by a small runner of our own that follows
`spec/README.md` (every `*.up.sql` in lexical order, recorded in
`schema_migrations`).

## Printing

tadmor writes its PDFs by hand with the standard-14 Helvetica fonts, so no
library is needed; `tadmor/pdf.py` ports that writer (about 100 lines, using
`zlib` and the `cp1252` codec), and `tadmor/printing.py` its layout. The
font widths in `tadmor/pdf_metrics.py` are copied from tadmor's generated
table.

## What would make these choices worth revisiting

- **A second script or a richer client.** If more screens need client-side
  behaviour than the line editor, htmx (one vendored file, one maintainer
  group) is the first candidate to discuss.
- **Static-file load.** If the server ever serves real traffic without a
  caching proxy, compare WhiteNoise against Caddy serving `tadmor/static/`
  directly.
- **Type checking.** mypy with django-stubs would add several build-time
  packages; it is not used today.
