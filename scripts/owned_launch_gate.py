#!/usr/bin/env python
"""Hold a newly created private session until its parent records ownership."""

from __future__ import annotations

import os
import sys

AUTHORIZED_BYTE = b"\x01"
GATE_FAILURE_EXIT_CODE = 125


def main(arguments: list[str]) -> int:
    if len(arguments) < 3 or arguments[1] != "--":
        return GATE_FAILURE_EXIT_CODE
    try:
        gate_descriptor = int(arguments[0])
    except ValueError:
        return GATE_FAILURE_EXIT_CODE
    command = arguments[2:]
    try:
        authorization = os.read(gate_descriptor, 1)
    except OSError:
        return GATE_FAILURE_EXIT_CODE
    finally:
        try:
            os.close(gate_descriptor)
        except OSError:
            pass
    if authorization != AUTHORIZED_BYTE:
        return GATE_FAILURE_EXIT_CODE
    os.execvpe(command[0], command, os.environ)
    return GATE_FAILURE_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
