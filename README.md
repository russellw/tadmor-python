# tadmor-python

Business management software: the Python counterpart of
[tadmor](https://github.com/russellw/tadmor). It is the same product, specified
by tadmor's `spec/` and checked by its conformance suite, built on Django with
server-rendered pages. The counterparts exist to compare supply-chain exposure;
see [`docs/stack.md`](docs/stack.md).

## Layout

```
tadmor/            the Django project, which is also its only app
  models.py        unmanaged models over the shared schema
  services/        business rules: one service layer for the API and the UI
  api/             the JSON API of spec/api.md
  ui/              the server-rendered UI of spec/domain.md §13
  templates/, static/   HTML templates, one stylesheet, one small script
  pdf.py, printing.py   printable documents (a stdlib-only PDF writer)
  migrate.py       applies db/migrations, the shared schema
spec/, conformance/, db/migrations/   copied from tadmor (spec/UPSTREAM); never edited here
vendor/            third-party wheels, unpacked and committed (vendor/lock.txt)
tools/             vendor.py (vendoring), conformance.sh (suite wrapper)
docs/              decisions and notes
```

## Prerequisites

- **Python 3.13 or later** (Debian 13 ships 3.13).
- **libpq**, the Postgres client library: `sudo apt install libpq5`. The pure
  Python psycopg links to it; no compiled wheel is used.
- **Postgres 17**, reachable via `DATABASE_URL`. `make db` starts one in a
  container.
- **Go**, only to run the conformance suite.

No `pip install` step: every package is already in `vendor/site`.

## Configuration

| Env var | Default | Purpose |
| ------- | ------- | ------- |
| `DATABASE_URL` | `postgres://localhost/tadmor` | Postgres connection string |
| `SECRET_KEY` | random per process | Set it in production |
| `ALLOWED_HOSTS` | `*` | Comma-separated host names |
| `CSRF_TRUSTED_ORIGINS` | none | e.g. `https://tadmor-python.example.com` behind a proxy |
| `DB_CONN_MAX_AGE` | `0` | Seconds to reuse a database connection (useful under gunicorn) |
| `SMTP_ADDR`, `SMTP_USER`, `SMTP_PASS`, `MAIL_FROM` | unset | Email sending; unset means the email endpoints answer 501 |
| `DEBUG` | off | `1` for Django's debug pages |

## Build, run, test

```sh
make db                      # once: a local Postgres in a container
make adduser EMAIL=me@example.com NAME='My Name'    # password on stdin; migrates first
make run                     # development server on 127.0.0.1:8080
make serve                   # gunicorn, as in production
make test                    # the Django test suite (UI walkthrough)
make conformance             # tadmor's conformance suite, from a wiped database
make check                   # Django checks, and vendor/ against its lock
```

The server applies pending migrations when it starts. Endpoints: the UI at
`/`, the JSON API under `/api/`, and the probes `/healthz` and `/readyz`.

## Dependency and supply-chain policy

Five packages, all pure Python, all vendored: Django, asgiref, sqlparse,
psycopg, and gunicorn. `vendor/lock.txt` pins each wheel by sha256;
`tools/vendor.py` fetches and verifies them, refuses versions published less
than 7 days ago, and checks the unpacked tree against each wheel's `RECORD`.
A clean clone runs offline. New packages need a conversation first. See
[`docs/stack.md`](docs/stack.md).
