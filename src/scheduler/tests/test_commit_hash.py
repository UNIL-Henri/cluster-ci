import unittest
import os
os.environ["CLUSTER_TOKEN"] = ""
import sys
import time
import threading
import requests
import sqlite3
import uuid

# Add src/scheduler to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from persistence import init_db, DB_PATH, get_db_conn
from headnode_service import app

class TestCommitHash(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["CLUSTER_DB_PATH"] = "test_commit.db"
        if os.path.exists("test_commit.db"):
            os.remove("test_commit.db")
        init_db()

        # Start Headnode API in a thread
        cls.api_thread = threading.Thread(target=lambda: app.run(port=5002, debug=False, use_reloader=False))
        cls.api_thread.daemon = True
        cls.api_thread.start()

        # Wait for API to be ready
        time.sleep(2)

    def test_commit_hash_propagation(self):
        headnode_url = "http://localhost:5002"
        job_id = str(uuid.uuid4())
        repo = "owner/repo"
        branch = "main"
        commit_hash = "abc123def456"

        # Pre-insert a job
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO jobs (job_id, repo, branch, status) VALUES (?, ?, ?, ?)',
                           (job_id, repo, branch, 'pending'))
            conn.commit()

        # 1. Update status to 'running' with commit_hash
        resp = requests.post(f"{headnode_url}/update_job_status", json={
            "job_id": job_id,
            "status": "running",
            "commit_hash": commit_hash
        })
        self.assertEqual(resp.status_code, 200)

        # Verify in DB
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT commit_hash, status FROM jobs WHERE job_id = ?', (job_id,))
            row = cursor.fetchone()
            self.assertEqual(row['commit_hash'], commit_hash)
            self.assertEqual(row['status'], 'running')

        # 2. Update status to 'completed' with same/updated commit_hash
        new_commit_hash = "fed654cba321"
        resp = requests.post(f"{headnode_url}/update_job_status", json={
            "job_id": job_id,
            "status": "completed",
            "exit_code": 0,
            "commit_hash": new_commit_hash
        })
        self.assertEqual(resp.status_code, 200)

        # Verify in DB
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT commit_hash, status, exit_code FROM jobs WHERE job_id = ?', (job_id,))
            row = cursor.fetchone()
            self.assertEqual(row['commit_hash'], new_commit_hash)
            self.assertEqual(row['status'], 'completed')
            self.assertEqual(row['exit_code'], 0)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists("test_commit.db"):
            os.remove("test_commit.db")

if __name__ == '__main__':
    unittest.main()
