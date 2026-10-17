"""Check the installed Playwright browser without downloading or skipping tests."""

from __future__ import annotations

import argparse
import sys

from playwright.sync_api import Error, sync_playwright


def main() -> int:
    """Launch the exact browser revision selected by the current environment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-ms", type=int, required=True)
    args = parser.parse_args()
    if args.timeout_ms <= 0:
        parser.error("--timeout-ms must be positive")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(timeout=args.timeout_ms)
            browser.close()
    except Error as exc:
        print(f"Chromium prerequisite failed: {exc}", file=sys.stderr)
        print(
            "From services/ui, run: uv run playwright install chromium\n"
            "Then rerun the check with the same PLAYWRIGHT_BROWSERS_PATH, if configured.",
            file=sys.stderr,
        )
        return 1

    print("Chromium launch prerequisite passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
