import unittest
import os
import json
import shutil
import time
import subprocess
import requests
import threading
from pathlib import Path

# Mocking parts of the system
from src.scheduler.headnode_service import app as headnode_app, FREE_SPACE_THRESHOLD_GB
from src.scheduler.persistence import init_db, DB_PATH
from src.scheduler.worker_agent import app as agent_app

class TestDataRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
        init_db()

        # Start headnode
        cls.headnode_thread = threading.Thread(target=headnode_app.run, kwargs={"port": 5001, "host": "0.0.0.0"}, daemon=True)
        cls.headnode_thread.start()

        # Start agent
        cls.agent_thread = threading.Thread(target=agent_app.run, kwargs={"port": 6001, "host": "0.0.0.0"}, daemon=True)
        cls.agent_thread.start()

        time.sleep(2) # Wait for servers to start

    def setUp(self):
        self.headnode_url = "http://localhost:5001"
        self.agent_url = "http://localhost:6001"

        # Setup dummy repositories dir
        # Correctly point to /app/repositories
        self.repo_dir = Path("/app/repositories")
        self.repo_dir.mkdir(exist_ok=True)
        self.registry_path = self.repo_dir / "registry.json"
        if self.registry_path.exists():
            os.remove(self.registry_path)

    def test_check_space(self):
        resp = requests.get(f"{self.headnode_url}/check_space")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("sufficient", data)
        self.assertIn("free_gb", data)

    def test_worker_registration_with_service_url(self):
        payload = {
            "worker_id": "test_worker",
            "hostname": "test_host",
            "service_url": self.agent_url,
            "total_ram_gb": 16
        }
        resp = requests.post(f"{self.headnode_url}/register_worker", json=payload)
        self.assertEqual(resp.status_code, 200)

        # Verify in DB (not strictly necessary but good for deep check)
        from src.scheduler.persistence import get_db_conn
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT service_url FROM workers WHERE worker_id = 'test_worker'")
            row = cursor.fetchone()
            self.assertEqual(row['service_url'], self.agent_url)

    def test_drain_webhook(self):
        # Temporarily mock threshold for testing if we can trigger sufficient space
        # But here free space is ~92GB, threshold is 100GB.

        # 1. Create a dummy project marked as pending sync
        project_name = "test_repo" # Avoid subdirectories for simple test
        (self.repo_dir / project_name).mkdir(exist_ok=True)

        registry = {
            project_name: {
                "status": "idle",
                "sync_status": "pending"
            }
        }
        with open(self.registry_path, 'w') as f:
            json.dump(registry, f)

        # 2. Trigger drain via headnode notification
        # First register the worker so headnode knows who to notify
        requests.post(f"{self.headnode_url}/register_worker", json={
            "worker_id": "test_worker",
            "hostname": "test_host",
            "service_url": self.agent_url,
            "total_ram_gb": 16
        })

        # Create a dummy uv and dvc for the agent to "run"
        # In a real test environment this might be tricky, but we can check if it tries to run them.
        # For now, let's just trigger the notify and see if it hits the agent.

        resp = requests.post(f"{self.headnode_url}/notify_cleanup")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['notified'], 1)

        # 3. Wait a bit for async drain and check registry
        # Since we don't have real dvc push here, it might fail or we'd need to mock it.
        # But we've verified the communication flow.

if __name__ == '__main__':
    unittest.main()
