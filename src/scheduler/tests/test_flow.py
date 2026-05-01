import unittest
import os
import sys
import time
import threading
import requests
import sqlite3

# Add src/scheduler to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from persistence import init_db, DB_PATH
from headnode_service import app
from scheduler_loop import schedule_jobs

class TestScheduler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["CLUSTER_DB_PATH"] = "test_cluster.db"
        if os.path.exists("test_cluster.db"):
            os.remove("test_cluster.db")
        init_db()

        # Start Headnode API in a thread
        cls.api_thread = threading.Thread(target=lambda: app.run(port=5001, debug=False, use_reloader=False))
        cls.api_thread.daemon = True
        cls.api_thread.start()

        # Start Scheduler Loop in a thread
        cls.loop_thread = threading.Thread(target=schedule_jobs)
        cls.loop_thread.daemon = True
        cls.loop_thread.start()

        # Wait for API to be ready
        time.sleep(2)

    def test_full_flow(self):
        headnode_url = "http://localhost:5001"

        # 1. Register a worker
        resp = requests.post(f"{headnode_url}/register_worker", json={
            "worker_id": "worker-1",
            "hostname": "lenovo-1",
            "total_ram_gb": 64.0,
            "available_ram_gb": 64.0
        })
        self.assertEqual(resp.status_code, 200)

        # 2. Submit a job
        resp = requests.post(f"{headnode_url}/submit_job", json={
            "repo": "owner/repo",
            "branch": "main",
            "ram_required_gb": 16.0
        })
        self.assertEqual(resp.status_code, 200)
        job_id = resp.json()['job_id']

        # 3. Wait for scheduler to assign the job
        time.sleep(7)

        # 4. Check worker poll
        resp = requests.get(f"{headnode_url}/worker_poll/worker-1")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['job_id'], job_id)

        # 5. Update job status
        resp = requests.post(f"{headnode_url}/update_job_status", json={
            "job_id": job_id,
            "status": "completed",
            "exit_code": 0
        })
        self.assertEqual(resp.status_code, 200)

        # 6. Verify final status
        resp = requests.get(f"{headnode_url}/job_status/{job_id}")
        self.assertEqual(resp.json()['status'], 'completed')

    @classmethod
    def tearDownClass(cls):
        if os.path.exists("test_cluster.db"):
            os.remove("test_cluster.db")

if __name__ == '__main__':
    unittest.main()
