# ruleid: no-direct-sql
import aiosqlite

db = aiosqlite.connect(":memory:")
