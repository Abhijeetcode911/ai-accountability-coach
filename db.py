import sqlite3

DB_NAME = "accountability.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # One row per system day
    c.execute("""
    CREATE TABLE IF NOT EXISTS daily_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day_number INTEGER,
        date TEXT,
        targets TEXT,
        summary TEXT
    )
    """)

    # Multiple progress updates per day
    c.execute("""
    CREATE TABLE IF NOT EXISTS daily_updates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day_number INTEGER,
        update_text TEXT,
        timestamp TEXT
    )
    """)

    # Strategic direction notes
    c.execute("""
    CREATE TABLE IF NOT EXISTS strategic_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        note TEXT,
        timestamp TEXT
    )
    """)

    conn.commit()
    conn.close()
