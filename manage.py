#!/usr/bin/env python3
"""Django's command-line utility, run against the vendored packages."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor" / "site"))


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tadmor.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
