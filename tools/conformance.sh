#!/usr/bin/env bash
# Run tadmor's conformance suite (conformance/, exported from tadmor) against
# this implementation, from scratch: wipe a dedicated database, bootstrap one
# administrator with a random password, start the server with email disabled,
# run the suite, and always stop the server again. Extra arguments go to the
# suite (e.g. -v, or -run 'banking').
#
#   DATABASE_URL  must name a database ending in _conformance (default:
#                 postgres://tadmor:tadmor@127.0.0.1:5432/tadmor_conformance)
#   HTTP_ADDR     where the server listens (default 127.0.0.1:8092)
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

export DATABASE_URL="${DATABASE_URL:-postgres://tadmor:tadmor@127.0.0.1:5432/tadmor_conformance}"
HTTP_ADDR="${HTTP_ADDR:-127.0.0.1:8092}"
export PYTHONPATH="$repo_root/vendor/site:$repo_root"
mkdir -p bin

echo "==> Wiping the database"
python3 manage.py resetdb

email="admin@conformance.test"
password="$(head -c 18 /dev/urandom | base64 | tr -d '/+=')"
echo "==> Bootstrapping administrator (migrates the empty database)"
printf '%s\n' "$password" | python3 manage.py adduser --email "$email" --name 'Conformance Admin' >/dev/null

echo "==> Starting server on $HTTP_ADDR (email disabled)"
env -u SMTP_ADDR -u SMTP_USER -u SMTP_PASS -u MAIL_FROM \
	python3 -m gunicorn tadmor.wsgi --bind "$HTTP_ADDR" --workers 4 \
	>"$repo_root/bin/conformance-server.log" 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true' EXIT

host="${HTTP_ADDR%%:*}"
port="${HTTP_ADDR##*:}"
for _ in $(seq 1 30); do
	if ! kill -0 "$server_pid" 2>/dev/null; then
		echo "server exited before becoming ready; see bin/conformance-server.log" >&2
		exit 1
	fi
	if (exec 3<>"/dev/tcp/$host/$port") 2>/dev/null; then
		exec 3>&- 3<&-
		break
	fi
	sleep 1
done

echo "==> Running the conformance suite"
(cd conformance && go run . -base-url "http://$HTTP_ADDR" -email "$email" -password "$password" "$@")
