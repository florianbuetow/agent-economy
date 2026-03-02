# ok: no-direct-sql
import sqlite3


def open_gateway_connection(path: str) -> sqlite3.Connection:
    """Fixture showing db-gateway style SQLite usage as an allowed case."""
    return sqlite3.connect(path)
