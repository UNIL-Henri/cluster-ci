import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.environ.get("CLUSTER_DB_PATH", "cluster_scheduler.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Table des Workers
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS workers (
            worker_id TEXT PRIMARY KEY,
            hostname TEXT,
            service_url TEXT,
            total_ram_gb REAL,
            available_ram_gb REAL,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'online'
        )
    ''')

    # Add service_url if it doesn't exist (migration)
    try:
        cursor.execute('ALTER TABLE workers ADD COLUMN service_url TEXT')
    except sqlite3.OperationalError:
        pass # Already exists

    # Table des Jobs
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            repo TEXT,
            branch TEXT,
            ram_required_gb REAL,
            status TEXT DEFAULT 'pending',
            worker_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            exit_code INTEGER,
            FOREIGN KEY (worker_id) REFERENCES workers (worker_id)
        )
    ''')

    conn.commit()
    conn.close()

@contextmanager
def get_db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
