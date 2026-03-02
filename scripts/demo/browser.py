#!/usr/bin/env python3
"""
Demo Browser Automation Script
Opens the ATE UI and navigates through the task lifecycle demo.

Usage:
    cd <repo-root>
    uv run --with playwright scripts/demo/browser.py [OPTIONS]

    # First time only — install browser binaries:
    uv run --with playwright python -m playwright install chromium

Options:
    --step-delay N    Seconds between demo steps (default: 3.0)
    --no-landing      Skip the landing page, go straight to task demo
    --headed          Run with visible browser (default: headed)
    --headless        Run in headless mode (for CI)
    --screenshots DIR Save screenshots to this directory
    --base-url URL    Base URL (default: http://localhost:8008)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def run_demo(args: argparse.Namespace) -> None:
    """Run the browser demo."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Run:")
        print("  uv run --with playwright python -m playwright install chromium")
        sys.exit(1)

    step_delay = args.step_delay

    # Screenshot helper
    shot_dir = Path(args.screenshots) if args.screenshots else None
    if shot_dir:
        shot_dir.mkdir(parents=True, exist_ok=True)
    shot_counter = 0

    def screenshot(page, label: str) -> None:
        nonlocal shot_counter
        if not shot_dir:
            return
        shot_counter += 1
        path = shot_dir / f"{shot_counter:02d}-{label}.png"
        page.screenshot(path=str(path))
        print(f"        [screenshot] {path.name}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=args.headless,
            args=["--window-size=1440,900"],
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            no_viewport=False,
        )
        page = context.new_page()

        # ────────────────────────────────────────────
        # ACT 1: Landing Page (the "vision hook")
        # ────────────────────────────────────────────
        if not args.no_landing:
            print("  [1/4] Opening landing page...")
            page.goto(f"{args.base_url}/")
            page.wait_for_load_state("networkidle")
            time.sleep(step_delay * 2)
            screenshot(page, "landing-hero")

            # Scroll down to show exchange board
            print("  [1/4] Scrolling to exchange board...")
            page.evaluate("document.getElementById('board').scrollIntoView({behavior:'smooth'})")
            time.sleep(step_delay)
            screenshot(page, "landing-exchange-board")

            # Scroll to leaderboard
            print("  [1/4] Scrolling to leaderboard...")
            page.evaluate("document.getElementById('leaderboard').scrollIntoView({behavior:'smooth'})")
            time.sleep(step_delay)
            screenshot(page, "landing-leaderboard")

            # Scroll back to top
            page.evaluate("window.scrollTo({top:0, behavior:'smooth'})")
            time.sleep(step_delay / 2)

        # ────────────────────────────────────────────
        # ACT 2: Task Lifecycle Demo
        # ────────────────────────────────────────────
        print("  [2/4] Opening task lifecycle demo...")
        page.goto(f"{args.base_url}/task.html")
        page.wait_for_load_state("networkidle")
        time.sleep(1)
        screenshot(page, "task-posted")

        time.sleep(step_delay)

        # ────────────────────────────────────────────
        # ACT 3: Click through all demo steps
        # ────────────────────────────────────────────
        print("  [3/4] Walking through task lifecycle...")

        step_labels = [
            "Bob Places Bid",
            "Carol Places Bid",
            "Alice Accepts Carol's Bid — Contract Signed",
            "Carol Submits Deliverable",
            "Alice Reviews Deliverable",
            "Alice Files Dispute",
            "Carol Submits Rebuttal",
            "LLM Judges Evaluate — Court Ruling",
            "Escrow Released — Settlement",
            "Feedback Exchange — Complete",
        ]

        # Screenshots at these step indices (0-based within step_labels)
        screenshot_steps = {
            2: "contract-signed",
            3: "deliverable",
            5: "dispute-filed",
            6: "rebuttal",
            7: "court-ruling",
            8: "settlement",
            9: "complete",
        }

        next_btn = page.locator("#btn-next")
        for i, label in enumerate(step_labels):
            next_btn.click()
            print(f"        Step {i + 2}/11: {label}")
            # Longer pause on key moments
            if "Dispute" in label or "Ruling" in label or "Settlement" in label:
                time.sleep(step_delay * 1.5)
            else:
                time.sleep(step_delay)
            if i in screenshot_steps:
                screenshot(page, screenshot_steps[i])

        time.sleep(step_delay)

        print("\n  Demo complete.\n")

        # Keep browser open for manual exploration
        if not args.headless:
            print("  Browser is open. Press Ctrl+C to close.\n")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass

        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="ATE Demo Browser Automation")
    parser.add_argument("--step-delay", type=float, default=3.0,
                        help="Seconds between demo steps (default: 3.0)")
    parser.add_argument("--no-landing", action="store_true",
                        help="Skip landing page, go straight to task demo")
    parser.add_argument("--headless", action="store_true", default=False,
                        help="Run in headless mode")
    parser.add_argument("--headed", action="store_true", default=True,
                        help="Run with visible browser (default)")
    parser.add_argument("--screenshots", type=str, default=None,
                        help="Save screenshots to this directory")
    parser.add_argument("--base-url", default="http://localhost:8008",
                        help="Base URL (default: http://localhost:8008)")
    run_demo(parser.parse_args())


if __name__ == "__main__":
    main()
