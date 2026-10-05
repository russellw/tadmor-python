#!/usr/bin/env python3
"""Vendor pinned, pure-Python wheels into vendor/ without pip.

vendor/lock.txt lists one wheel per line with its sha256. `sync` downloads
each wheel from PyPI, verifies the hash, and unpacks it into vendor/site/,
which is committed. `add NAME==VERSION` looks a wheel up on PyPI, checks the
publish cooldown, and appends it to the lock. `check` verifies that
vendor/site/ is exactly what the lock describes, so a review of the lock
diff is a review of what changed.

`manifest` writes dependencies.json, the dependency manifest tadmor's
tools/measure.py reads (tadmor's docs/counterpart-metrics.md): every
wheel, its category, and the PyPI accounts that can publish it, looked up
online; `sync` runs it, and `check` verifies that it lists exactly the
locked wheels.

Standard library only: this script runs before anything is vendored.
"""

import datetime
import hashlib
import io
import json
import shutil
import subprocess
import sys
import time
import urllib.request
import xmlrpc.client
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "vendor" / "lock.txt"
SITE = ROOT / "vendor" / "site"
COOLDOWN_DAYS = 7
PYPI = "https://pypi.org/pypi"
MANIFEST = ROOT / "dependencies.json"


def read_lock():
    entries = []
    for line in LOCK.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, version, filename, digest = line.split()
        assert digest.startswith("sha256:"), line
        entries.append((name, version, filename, digest.removeprefix("sha256:")))
    return entries


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def release_files(name, version):
    return fetch_json(f"{PYPI}/{name}/{version}/json")["urls"]


def add(spec):
    name, _, version = spec.partition("==")
    if not version:
        sys.exit("usage: vendor.py add NAME==VERSION")
    wheels = [f for f in release_files(name, version)
              if f["packagetype"] == "bdist_wheel" and f["filename"].endswith("-py3-none-any.whl")]
    if len(wheels) != 1:
        sys.exit(f"{spec}: expected exactly one pure-Python wheel, found {[f['filename'] for f in wheels]}")
    w = wheels[0]
    uploaded = datetime.datetime.fromisoformat(w["upload_time_iso_8601"].replace("Z", "+00:00"))
    age = datetime.datetime.now(datetime.timezone.utc) - uploaded
    if age < datetime.timedelta(days=COOLDOWN_DAYS):
        sys.exit(f"{spec} was published {age.days} days ago; the cooldown is {COOLDOWN_DAYS} days")
    if any(e[0].lower() == name.lower() for e in read_lock()):
        sys.exit(f"{name} is already in the lock; remove its line first to change version")
    with LOCK.open("a") as f:
        f.write(f"{name} {version} {w['filename']} sha256:{w['digests']['sha256']}\n")
    print(f"added {w['filename']} (published {uploaded.date()}); now run: tools/vendor.py sync")


def sync():
    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir(parents=True)
    for name, version, filename, digest in read_lock():
        url = next(f["url"] for f in release_files(name, version) if f["filename"] == filename)
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read()
        got = hashlib.sha256(data).hexdigest()
        if got != digest:
            sys.exit(f"{filename}: sha256 mismatch: lock has {digest}, PyPI served {got}")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for member in z.namelist():
                if member.startswith("/") or ".." in Path(member).parts:
                    sys.exit(f"{filename}: unsafe path {member}")
            z.extractall(SITE)
        print(f"vendored {filename}")
    manifest()


def check():
    """Verify vendor/site against each wheel's RECORD file (no network)."""
    problems = 0
    recorded = set()
    for name, version, filename, _ in read_lock():
        dist = filename.split("-")[0] + "-" + filename.split("-")[1] + ".dist-info"
        record = SITE / dist / "RECORD"
        if not record.exists():
            print(f"missing {record.relative_to(ROOT)}")
            problems += 1
            continue
        for row in record.read_text().splitlines():
            path, digest, _size = row.rsplit(",", 2)
            recorded.add(path)
            if not digest:
                continue
            algo, _, expected = digest.partition("=")
            p = SITE / path
            if not p.exists():
                print(f"missing vendor/site/{path}")
                problems += 1
                continue
            import base64
            got = base64.urlsafe_b64encode(hashlib.new(algo, p.read_bytes()).digest()).rstrip(b"=").decode()
            if got != expected:
                print(f"modified vendor/site/{path}")
                problems += 1
    for p in SITE.rglob("*"):
        if p.is_file() and p.relative_to(SITE).as_posix() not in recorded and "__pycache__" not in p.parts:
            print(f"unexpected vendor/site/{p.relative_to(SITE).as_posix()}")
            problems += 1
    problems += check_manifest()
    if problems:
        sys.exit(f"{problems} problem(s) in vendor/site")
    print("vendor/site and dependencies.json match vendor/lock.txt")


def identities(name, rpc):
    """The project's publishing identities: each account PyPI lists in a role
    (Owner or Maintainer). A project published through a PyPI organization
    lists none and counts as one identity, the organization (tadmor's
    docs/counterpart-metrics.md, section 1)."""
    for attempt in range(6):
        try:
            roles = rpc.package_roles(name)
            break
        except xmlrpc.client.Fault as e:
            # PyPI rate-limits XML-RPC; it says when to try again.
            if "TooManyRequests" not in e.faultString or attempt == 5:
                raise
            time.sleep(2 * (attempt + 1))
    return sorted({f"pypi:{user}" for _role, user in roles}) or [f"pypi-org:{name.lower()}"]


def manifest():
    """Write dependencies.json. Every wheel is unpacked into vendor/site and
    imported, so every one is runtime."""
    rpc = xmlrpc.client.ServerProxy(PYPI)
    packages = [{
        "ecosystem": "pypi", "name": name.lower(), "version": version, "category": "runtime",
        "identities": identities(name, rpc), "evidence": f"PyPI XML-RPC package_roles({name})",
    } for name, version, _filename, _digest in read_lock()]
    packages.sort(key=lambda p: p["name"])
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "vendor/site"], check=True,
                             capture_output=True, text=True).stdout.split()
    files = [ROOT / f for f in tracked]
    doc = {
        "format": "tadmor-dependencies/1",
        "generator": "tools/vendor.py manifest (tadmor-python)",
        "platform": "linux/x64",
        "toolchains": ["CPython (Python Software Foundation)"],
        "packages": packages,
        "sources": [{
            "label": "PyPI vendor/site (runtime)",
            "bytes": sum(f.stat().st_size for f in files),
            "lines": sum(sum(1 for line in f.open(encoding="utf-8", errors="replace") if line.strip())
                         for f in files if f.suffix == ".py"),
        }],
    }
    MANIFEST.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"wrote {MANIFEST.relative_to(ROOT)}: {len(packages)} packages")


def check_manifest():
    """1 if dependencies.json does not list exactly the locked wheels, else 0."""
    if not MANIFEST.exists():
        print("dependencies.json is missing; run tools/vendor.py manifest")
        return 1
    listed = {(p["name"], p["version"]) for p in json.loads(MANIFEST.read_text())["packages"]}
    if listed != {(name.lower(), version) for name, version, _f, _d in read_lock()}:
        print("dependencies.json does not list the locked wheels; run tools/vendor.py manifest")
        return 1
    return 0


def main():
    args = sys.argv[1:]
    if args[:1] == ["add"] and len(args) == 2:
        add(args[1])
    elif args == ["sync"]:
        sync()
    elif args == ["check"]:
        check()
    elif args == ["manifest"]:
        manifest()
    else:
        sys.exit("usage: tools/vendor.py add NAME==VERSION | sync | check | manifest")


if __name__ == "__main__":
    main()
