#!/usr/bin/env python
import base64
import secrets
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

USAGE = (
    "Usage:\n"
    "  crypto_helper.py keygen\n"
    "  crypto_helper.py sign_raw <private_key_hex> <raw_string>\n"
    "  crypto_helper.py sign <private_key_hex> <payload_b64>\n"
    "  crypto_helper.py pubkey_bytes <n>\n"
    "  crypto_helper.py zero_key\n"
    "  crypto_helper.py random_b64 <n>\n"
    "  crypto_helper.py large_sign <private_key_hex> <size_bytes>"
)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def print_usage_and_exit() -> None:
    print(USAGE, file=sys.stderr)
    sys.exit(1)


def keygen() -> None:
    private_key = Ed25519PrivateKey.generate()
    private_raw = private_key.private_bytes_raw()
    public_raw = private_key.public_key().public_bytes_raw()
    print(private_raw.hex())
    print(f"ed25519:{b64(public_raw)}")


def sign_raw(private_key_hex: str, raw_string: str) -> None:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    payload_bytes = raw_string.encode("utf-8")
    signature = private_key.sign(payload_bytes)
    print(b64(payload_bytes))
    print(b64(signature))


def sign(private_key_hex: str, payload_b64: str) -> None:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    payload_bytes = base64.b64decode(payload_b64)
    signature = private_key.sign(payload_bytes)
    print(b64(signature))


def pubkey_bytes(length: str) -> None:
    raw = secrets.token_bytes(int(length))
    print(f"ed25519:{b64(raw)}")


def zero_key() -> None:
    raw = bytes(32)
    print(f"ed25519:{b64(raw)}")


def random_b64(length: str) -> None:
    raw = secrets.token_bytes(int(length))
    print(b64(raw))


def large_sign(private_key_hex: str, size_bytes: str) -> None:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    payload = secrets.token_bytes(int(size_bytes))
    signature = private_key.sign(payload)
    print(b64(payload))
    print(b64(signature))


def _run_command(command: str, args: list[str]) -> None:
    """Dispatch a CLI command with its arguments."""
    match command:
        case "keygen":
            keygen()
        case "sign_raw":
            sign_raw(args[0], args[1])
        case "sign":
            sign(args[0], args[1])
        case "pubkey_bytes":
            pubkey_bytes(args[0])
        case "zero_key":
            zero_key()
        case "random_b64":
            random_b64(args[0])
        case "large_sign":
            large_sign(args[0], args[1])


# command name -> expected total argc (including script name and command)
_COMMANDS: dict[str, int] = {
    "keygen": 2,
    "sign_raw": 4,
    "sign": 4,
    "pubkey_bytes": 3,
    "zero_key": 2,
    "random_b64": 3,
    "large_sign": 4,
}


def main(argv: list[str]) -> None:
    if len(argv) < 2 or argv[1] not in _COMMANDS:
        print_usage_and_exit()

    command = argv[1]
    if len(argv) != _COMMANDS[command]:
        print_usage_and_exit()

    _run_command(command, argv[2:])


if __name__ == "__main__":
    main(sys.argv)
