#!/usr/bin/env python3
"""Tail all service log files and print color-coded output.

Usage: python3 scripts/tail_logs.py [--date YYYY-MM-DD]

Tails today's log file for each service by default. Lines are prefixed
with [SERVICE-NAME] in service-specific colors. ERROR lines print in
red and WARNING lines in orange, overriding the service color.
"""

from __future__ import annotations

import io

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SERVICES: dict[str, str] = {
    "identity": "IDENTITY",
    "central-bank": "BANK",
    "task-board": "TASKBOARD",
    "reputation": "REPUTATION",
    "court": "COURT",
    "db-gateway": "DB-GATEWAY",
    "ui": "UI",
}

# Blue/green shades per service (ANSI 256-color)
SERVICE_COLORS: dict[str, str] = {
    "identity": "\033[38;5;75m",    # steel blue
    "central-bank": "\033[38;5;79m",  # medium aquamarine
    "task-board": "\033[38;5;69m",  # cornflower blue
    "reputation": "\033[38;5;120m",  # light green
    "court": "\033[38;5;111m",      # sky blue
    "db-gateway": "\033[38;5;74m",  # cadet blue
    "ui": "\033[38;5;156m",         # pale green
}

COLOR_RED = "\033[38;5;196m"
COLOR_ORANGE = "\033[38;5;214m"
COLOR_RESET = "\033[0m"
COLOR_DIM = "\033[2m"


def log_path(service_dir: str, date_str: str) -> str:
    return os.path.join(PROJECT_ROOT, "services", service_dir, "data", "logs", f"{date_str}.log")


def format_line(service_dir: str, raw_line: str) -> str:
    label = SERVICES[service_dir]
    svc_color = SERVICE_COLORS[service_dir]

    stripped = raw_line.strip()
    if not stripped:
        return ""

    level = ""
    message = stripped
    try:
        data = json.loads(stripped)
        level = data.get("level", "")
        ts = data.get("timestamp", "")
        msg = data.get("message", "")
        logger = data.get("logger", "")
        extra = data.get("extra", {})

        parts = []
        if ts:
            parts.append(ts)
        if msg:
            parts.append(msg)
        if extra:
            detail_parts = [f"{k}={v}" for k, v in extra.items()]
            parts.append(" ".join(detail_parts))
        elif logger:
            parts.append(logger)

        message = " | ".join(parts) if parts else stripped
    except (json.JSONDecodeError, AttributeError):
        pass

    if level == "ERROR" or "ERROR" in raw_line:
        line_color = COLOR_RED
    elif level == "WARNING" or "WARN" in raw_line:
        line_color = COLOR_ORANGE
    else:
        line_color = svc_color

    label_padded = f"[{label}]".ljust(14)
    return f"{svc_color}{label_padded}{COLOR_RESET} {line_color}{message}{COLOR_RESET}"


def tail_logs(date_str: str) -> None:
    files: dict[str, io.TextIOWrapper] = {}
    offsets: dict[str, int] = {}

    for svc in SERVICES:
        path = log_path(svc, date_str)
        if os.path.exists(path):
            fh = open(path, "r")  # noqa: SIM115
            fh.seek(0, 2)  # seek to end
            files[svc] = fh
            offsets[svc] = fh.tell()

    if not files:
        print(f"No log files found for {date_str}.")
        print("Expected paths like: services/<name>/data/logs/{date_str}.log")
        sys.exit(1)

    active = ", ".join(SERVICES[s] for s in files)
    print(f"{COLOR_DIM}Tailing {len(files)} services: {active}{COLOR_RESET}")
    print(f"{COLOR_DIM}Date: {date_str} | Ctrl+C to stop{COLOR_RESET}")
    print()

    try:
        while True:
            had_output = False
            for svc, fh in list(files.items()):
                path = log_path(svc, date_str)

                # Handle file rotation / truncation
                try:
                    current_size = os.path.getsize(path)
                except OSError:
                    continue

                if current_size < offsets[svc]:
                    fh.seek(0)
                    offsets[svc] = 0

                line = fh.readline()
                while line:
                    had_output = True
                    formatted = format_line(svc, line)
                    if formatted:
                        print(formatted)
                    line = fh.readline()
                offsets[svc] = fh.tell()

            # Check for new log files from services that didn't have one at startup
            for svc in SERVICES:
                if svc not in files:
                    path = log_path(svc, date_str)
                    if os.path.exists(path):
                        fh = open(path, "r")  # noqa: SIM115
                        fh.seek(0, 2)
                        files[svc] = fh
                        offsets[svc] = fh.tell()
                        label = SERVICES[svc]
                        print(f"{COLOR_DIM}[+] {label} log appeared{COLOR_RESET}")

            if not had_output:
                time.sleep(0.2)

    except KeyboardInterrupt:
        print(f"\n{COLOR_DIM}Stopped.{COLOR_RESET}")
    finally:
        for fh in files.values():
            fh.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Tail all service logs with color-coded output")
    parser.add_argument(
        "--date",
        default=datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
        help="Date to tail (default: today UTC, format: YYYY-MM-DD)",
    )
    args = parser.parse_args()
    tail_logs(args.date)


if __name__ == "__main__":
    main()
