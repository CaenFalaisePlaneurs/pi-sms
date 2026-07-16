"""External dependency checking.

Unlike the camera/OGN sibling projects, pi-sms has no required external
binaries at runtime (httpx and apscheduler are pure-Python dependencies
already validated by pip at install time). This module exists so `main.py`
follows the same startup sequence as the other pi-* daemons, and so future
external dependencies (e.g. a specific `nmcli` version) have an obvious place
to be checked.
"""

import sys


def check_external_dependencies() -> None:
    """Check for required external dependencies and exit if missing.

    Currently a no-op: kept for parity with the daemon startup sequence used
    by other pi-* projects, and as an extension point for future checks.
    """
    missing_deps: list[str] = []

    if missing_deps:
        print("ERROR: Required external dependencies are missing:")
        for dep in missing_deps:
            print(f"  - {dep}")
        sys.exit(1)
