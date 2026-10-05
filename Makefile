# tadmor-python developer tasks.
#
# Everything runs against the vendored packages in vendor/site: nothing is
# installed from PyPI, and no target touches the network except vendor-sync.
export PYTHONPATH := $(CURDIR)/vendor/site:$(CURDIR)
PYTHON ?= python3

# Connection strings. Override on the command line, e.g.
#   make run DATABASE_URL=postgres://user:pass@host:5432/db
DATABASE_URL ?= postgres://tadmor:tadmor@127.0.0.1:5432/tadmor
TEST_DATABASE_URL ?= postgres://tadmor:tadmor@127.0.0.1:5432/tadmor
CONFORMANCE_DATABASE_URL ?= postgres://tadmor:tadmor@127.0.0.1:5432/tadmor_conformance
HTTP_ADDR ?= 127.0.0.1:8080

.DEFAULT_GOAL := help
.PHONY: help run serve migrate adduser test conformance check vendor-check vendor-sync db

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*## "}{printf "  make %-13s %s\n", $$1, $$2}'

run: ## Run the development server (migrates on start, reloads on change)
	DATABASE_URL=$(DATABASE_URL) $(PYTHON) manage.py runserver $(HTTP_ADDR)

serve: ## Run the production server (gunicorn) on HTTP_ADDR
	DATABASE_URL=$(DATABASE_URL) DB_CONN_MAX_AGE=60 $(PYTHON) -m gunicorn tadmor.wsgi --bind $(HTTP_ADDR) --workers 4

migrate: ## Apply pending shared-schema migrations
	DATABASE_URL=$(DATABASE_URL) $(PYTHON) manage.py dbmigrate

adduser: ## Create or reset an administrator: make adduser EMAIL=... NAME=... (password on stdin)
	DATABASE_URL=$(DATABASE_URL) $(PYTHON) manage.py adduser --email "$(EMAIL)" --name "$(NAME)"

test: ## Run the Django test suite (creates and drops a test_<name> database)
	DATABASE_URL=$(TEST_DATABASE_URL) $(PYTHON) manage.py test tadmor --noinput

conformance: ## Run tadmor's conformance suite against a fresh server (wipes the _conformance DB)
	DATABASE_URL=$(CONFORMANCE_DATABASE_URL) tools/conformance.sh $(ARGS)

check: vendor-check ## Django system checks and the vendor tree check
	DATABASE_URL=$(DATABASE_URL) $(PYTHON) manage.py check

vendor-check: ## Verify vendor/site and dependencies.json against vendor/lock.txt (offline)
	$(PYTHON) tools/vendor.py check

vendor-sync: ## Re-download and unpack every wheel in vendor/lock.txt (network)
	$(PYTHON) tools/vendor.py sync

db: ## Start a local Postgres 17 container (podman or docker) for development
	podman run -d --name tadmor-python-pg -e POSTGRES_USER=tadmor -e POSTGRES_PASSWORD=tadmor \
		-e POSTGRES_DB=tadmor -p 127.0.0.1:5432:5432 docker.io/library/postgres:17
