#!/usr/bin/env python3
"""Print server.host from a service config file; fail if it is absent."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


def main(arguments: list[str]) -> int:
    if len(arguments) != 1:
        print("usage: service_host.py <config.yaml>", file=sys.stderr)
        return 2
    config_path = Path(arguments[0])
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    server = document.get("server") if isinstance(document, dict) else None
    host = server.get("host") if isinstance(server, dict) else None
    if not isinstance(host, str) or not host:
        print(f"{config_path}: server.host is missing", file=sys.stderr)
        return 1
    print(host)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
