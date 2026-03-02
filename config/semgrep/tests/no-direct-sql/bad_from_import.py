# ruleid: no-direct-sql
from sqlite3 import connect

conn = connect(":memory:")
