#!/usr/bin/env python3
"""Vendor pinned, pure-Python wheels into vendor/ without pip.

vendor/lock.txt lists one wheel per line with its sha256. `sync` downloads
each wheel from PyPI, verifies the hash, and unpacks it into vendor/site/,
which is committed. `add NAME==VERSION` looks a wheel up on PyPI, checks the
publish cooldown, and appends it to the lock. `check` verifies that
vendor/site/ is exactly what the lock describes, so a review of the lock
diff is a review of what changed.

Standard library only: this script runs before anything is vendored.
"""

import datetime
import hashlib
import io
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "vendor" / "lock.txt"
SITE = ROOT / "vendor" / "site"
COOLDOWN_DAYS = 7
PYPI = "https://pypi.org/pypi"


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
    if problems:
        sys.exit(f"{problems} problem(s) in vendor/site")
    print("vendor/site matches vendor/lock.txt")


def main():
    args = sys.argv[1:]
    if args[:1] == ["add"] and len(args) == 2:
        add(args[1])
    elif args == ["sync"]:
        sync()
    elif args == ["check"]:
        check()
    else:
        sys.exit("usage: tools/vendor.py add NAME==VERSION | sync | check")


if __name__ == "__main__":
    main()
