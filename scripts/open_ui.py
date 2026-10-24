"""Open the economy UI in the user's default web browser."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from collections.abc import Sequence


def main(argv: Sequence[str]) -> int:
    """Print and open the explicitly provided UI URL."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    args = parser.parse_args(argv)

    print(f"Opening economy UI: {args.url}", flush=True)
    try:
        opened = webbrowser.open(args.url, new=2)
    except (OSError, webbrowser.Error) as exc:
        print(
            f"Could not open the browser: {exc}. Open it manually: {args.url}",
            file=sys.stderr,
        )
        return 1
    if not opened:
        print(
            f"Could not open the browser. Open it manually: {args.url}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
