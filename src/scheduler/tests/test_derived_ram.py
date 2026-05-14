import unittest
import os
import sys
import time
import threading
import requests
import sqlite3

# Add src/scheduler to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from persistence import init_db
from headnode_service import app
from scheduler_loop import schedule_jobs

class TestDerivedRAM(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.headnode_url = "http://localhost:5002"
        os.environ["CLUSTER_DB_PATH"] = "test_derived_ram.db"
        if os.path.exists("test_derived_ram.db"):
            os.remove("test_derived_ram.db")
        init_db()

        # Start Headnode API in a thread
        cls.api_thread = threading.Thread(target=lambda: app.run(port=5002, debug=False, use_reloader=False))
        cls.api_thread.daemon = True
        cls.api_thread.start()

        # Start Scheduler Loop in a thread
        cls.loop_thread = threading.Thread(target=schedule_jobs)
        cls.loop_thread.daemon = True
        cls.loop_thread.start()

        # Wait for API to be ready
        time.sleep(2)

    def test_derived_ram_calculation(self):
        headnode_url = self.headnode_url
        worker_id = "worker-ram-test"

        # 1. Register a worker with 64GB
        resp = requests.post(f"{headnode_url}/register_worker", json={
            "worker_id": worker_id,
            "hostname": "test-host",
            "total_ram_gb": 64.0
        })
        self.assertEqual(resp.status_code, 200)

        # 2. Check initial available RAM (should be 64.0)
        resp = requests.get(f"{headnode_url}/workers")
        workers = resp.json()
        target_worker = next(w for w in workers if w['worker_id'] == worker_id)
        self.assertEqual(target_worker['available_ram_gb'], 64.0)

        # 3. Submit a job requiring 16GB
        resp = requests.post(f"{headnode_url}/submit_job", json={
            "repo": "owner/repo",
            "branch": "main",
            "ram_required_gb": 16.0
        })
        self.assertEqual(resp.status_code, 200)
        job_id = resp.json()['job_id']

        # 4. Wait for scheduler to assign the job
        time.sleep(7)

        # 5. Check derived available RAM (should be 64.0 - 16.0 = 48.0)
        resp = requests.get(f"{headnode_url}/workers")
        workers = resp.json()
        target_worker = next(w for w in workers if w['worker_id'] == worker_id)
        self.assertEqual(target_worker['available_ram_gb'], 48.0)

        # 6. Complete the job
        resp = requests.post(f"{headnode_url}/update_job_status", json={
            "job_id": job_id,
            "status": "completed",
            "exit_code": 0
        })
        self.assertEqual(resp.status_code, 200)

        # 7. Check derived available RAM again (should be back to 64.0)
        resp = requests.get(f"{headnode_url}/workers")
        workers = resp.json()
        target_worker = next(w for w in workers if w['worker_id'] == worker_id)
        self.assertEqual(target_worker['available_ram_gb'], 64.0)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists("test_derived_ram.db"):
            os.remove("test_derived_ram.db")

if __name__ == '__main__':
    unittest.main()
