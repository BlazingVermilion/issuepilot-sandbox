"""Compatibility entry point. New installations use manage.py."""

import sys

from manage import run


def arguments(argv):
    if not argv:
        return ["--help"]
    action, *rest = argv
    if action == "recover":
        raise SystemExit(
            "No data changed. Restore the original workspace settings and matching PostgreSQL "
            "volume, then run manage.py start. See issuepilot/docs/runbook.md."
        )
    if action == "all":
        return ["submit", "--resume", *(arg for arg in rest if arg != "--resume")]
    if action == "smoke":
        return ["verify-model", "--require-gpu", *rest]
    return ["workflow" if action == "link" else action, *rest]


if __name__ == "__main__":
    run(arguments(sys.argv[1:]))
