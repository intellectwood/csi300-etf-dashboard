import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS index_daily(
    date  TEXT PRIMARY KEY,
    close REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS index_anchor(
    date      TEXT PRIMARY KEY,
    total_cap REAL,
    float_cap REAL,
    close     REAL
);
CREATE TABLE IF NOT EXISTS index_price(
    code  TEXT,
    date  TEXT,
    close REAL,
    PRIMARY KEY(code, date)
);
CREATE TABLE IF NOT EXISTS etf_nav(
    date TEXT,
    code TEXT,
    nav  REAL,
    PRIMARY KEY(date, code)
);
CREATE TABLE IF NOT EXISTS etf_shares_q(
    date   TEXT,
    code   TEXT,
    shares REAL,
    PRIMARY KEY(date, code)
);
CREATE TABLE IF NOT EXISTS etf_daily(
    date    TEXT,
    code    TEXT,
    name    TEXT,
    shares  REAL,
    scale   REAL,
    quality TEXT,
    PRIMARY KEY(date, code)
);
CREATE TABLE IF NOT EXISTS ratio_daily(
    date        TEXT PRIMARY KEY,
    numerator   REAL,
    denominator REAL,
    ratio       REAL,
    quality     TEXT,
    note        TEXT
);
CREATE TABLE IF NOT EXISTS job_run(
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at  TEXT,
    mode    TEXT,
    ok      INTEGER,
    rows    INTEGER,
    message TEXT
);
"""


def connect(path):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def upsert(conn, table, columns, rows):
    if not rows:
        return 0
    placeholders = ",".join("?" for _ in columns)
    sql = "INSERT OR REPLACE INTO %s(%s) VALUES(%s)" % (
        table, ",".join(columns), placeholders)
    conn.executemany(sql, rows)
    conn.commit()
    return len(rows)


def log_run(conn, run_at, mode, ok, rows, message):
    conn.execute(
        "INSERT INTO job_run(run_at, mode, ok, rows, message) VALUES(?,?,?,?,?)",
        (run_at, mode, 1 if ok else 0, rows, message))
    conn.commit()
