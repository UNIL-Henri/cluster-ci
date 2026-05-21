import unittest
import os
import sys
import json
import sqlite3
from unittest.mock import patch, MagicMock

# Add src/scheduler to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from persistence import init_db, get_db_conn
import scheduler_loop

class TestDVCLocality(unittest.TestCase):
    def setUp(self):
        self.db_path = f"test_locality_{self._testMethodName}.db"
        os.environ["CLUSTER_DB_PATH"] = self.db_path
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        init_db()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    @patch('requests.post')
    def test_scheduling_priority(self, mock_post):
        # 1. Register two workers
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO workers (worker_id, hostname, service_url, total_ram_gb, available_ram_gb, last_seen, status)
                VALUES (?, ?, ?, ?, ?, datetime('now'), 'online')
            ''', ("worker-A", "host-A", "http://worker-A:6000", 64.0, 64.0))
            cursor.execute('''
                INSERT INTO workers (worker_id, hostname, service_url, total_ram_gb, available_ram_gb, last_seen, status)
                VALUES (?, ?, ?, ?, ?, datetime('now'), 'online')
            ''', ("worker-B", "host-B", "http://worker-B:6000", 64.0, 64.0))

            # 2. Submit a job with required hashes
            hashes = ["hash1", "hash2", "hash3"]
            cursor.execute('''
                INSERT INTO jobs (job_id, repo, branch, ram_required_gb, required_hashes, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
            ''', ("job-1", "owner/repo", "main", 16.0, json.dumps(hashes)))
            conn.commit()

        # 3. Mock worker responses: worker-B has 2 hashes, worker-A has 0
        def side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if "worker-B" in url:
                mock_resp.json.return_value = ["hash1", "hash2"]
            else:
                mock_resp.json.return_value = []
            return mock_resp

        mock_post.side_effect = side_effect

        # 4. Run one iteration of scheduler loop (manually calling the logic inside)
        # We'll slightly modify scheduler_loop to be testable or just call the core part.
        # For this test, we call the function but we need to break the while True.
        # Alternatively, we just copy the logic or mock time.sleep to raise exception.

        with patch('time.sleep', side_effect=InterruptedError):
            try:
                scheduler_loop.schedule_jobs()
            except InterruptedError:
                pass

        # 5. Verify job-1 was assigned to worker-B (more hashes)
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT worker_id, p2p_url FROM jobs WHERE job_id = "job-1"')
            job = cursor.fetchone()
            self.assertEqual(job['worker_id'], "worker-B")
            # worker-B only has 2/3 hashes, so it should have a p2p_url pointing to worker-A if A had any,
            # but in our mock worker-A has 0.
            # Actually, the logic sets p2p_url if best_peer score > 0.
            # In this case worker-A score is 0, so p2p_url should be None.
            self.assertIsNone(job['p2p_url'])

    @patch('requests.post')
    def test_p2p_url_injection(self, mock_post):
        # 1. Register two workers
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO workers (worker_id, hostname, service_url, total_ram_gb, available_ram_gb, last_seen, status)
                VALUES (?, ?, ?, ?, ?, datetime('now'), 'online')
            ''', ("worker-A", "host-A", "http://worker-A:6000", 64.0, 64.0))
            cursor.execute('''
                INSERT INTO workers (worker_id, hostname, service_url, total_ram_gb, available_ram_gb, last_seen, status)
                VALUES (?, ?, ?, ?, ?, datetime('now'), 'online')
            ''', ("worker-B", "host-B", "http://worker-B:6000", 64.0, 64.0))

            # 2. Submit a job with required hashes
            hashes = ["hash1", "hash2", "hash3"]
            cursor.execute('''
                INSERT INTO jobs (job_id, repo, branch, ram_required_gb, required_hashes, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
            ''', ("job-2", "owner/repo", "main", 16.0, json.dumps(hashes)))
            conn.commit()

        # 3. Mock worker responses:
        # worker-B has 2 hashes ("hash1", "hash2")
        # worker-A has 1 hash ("hash3")
        def side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if "worker-B" in url:
                mock_resp.json.return_value = ["hash1", "hash2"]
            else:
                mock_resp.json.return_value = ["hash3"]
            return mock_resp

        mock_post.side_effect = side_effect

        with patch('time.sleep', side_effect=InterruptedError):
            try:
                scheduler_loop.schedule_jobs()
            except InterruptedError:
                pass

        # 5. Verify job-2 was assigned to worker-B and has p2p_url to worker-A
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT worker_id, p2p_url FROM jobs WHERE job_id = "job-2"')
            job = cursor.fetchone()
            self.assertEqual(job['worker_id'], "worker-B")
            self.assertEqual(job['p2p_url'], "http://worker-A:6000/fetch_artifact")

if __name__ == '__main__':
    unittest.main()
