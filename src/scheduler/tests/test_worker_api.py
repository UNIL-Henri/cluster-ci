import unittest
import os
import json
import tempfile
import shutil
from src.scheduler.worker_agent import app, REPOS_DIR as ORIGINAL_REPOS_DIR
import src.scheduler.worker_agent as worker_agent

class TestWorkerAPI(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        # Override REPOS_DIR in worker_agent module
        worker_agent.REPOS_DIR = self.test_dir
        self.app = app.test_client()
        self.app.testing = True

    def tearDown(self):
        shutil.rmtree(self.test_dir)
        # Restore ORIGINAL_REPOS_DIR
        worker_agent.REPOS_DIR = ORIGINAL_REPOS_DIR

    def test_check_cache_logic(self):
        # Create some cache files
        repo = "user/repo"
        hash1 = "1234567890abcdef1234567890abcdef"
        hash2 = "abcdef1234567890abcdef1234567890"
        hash3 = "deadbeefdeadbeefdeadbeefdeadbeef"

        # hash1 and hash2 exist, hash3 does not
        for h in [hash1, hash2]:
            cache_dir = os.path.join(self.test_dir, repo, ".dvc", "cache", "files", "md5", h[:2])
            os.makedirs(cache_dir, exist_ok=True)
            with open(os.path.join(cache_dir, h[2:]), "w") as f:
                f.write("data")

        resp = self.app.post('/check_cache', json={
            "repo": repo,
            "hashes": [hash1, hash2, hash3]
        })

        self.assertEqual(resp.status_code, 200)
        found = resp.json
        self.assertIn(hash1, found)
        self.assertIn(hash2, found)
        self.assertNotIn(hash3, found)
        self.assertEqual(len(found), 2)

    def test_fetch_artifact_success(self):
        # Create a dummy file
        repo_path = os.path.join(self.test_dir, "user/repo")
        os.makedirs(repo_path)
        file_path = os.path.join(repo_path, "artifact.txt")
        with open(file_path, "w") as f:
            f.write("hello world")

        resp = self.app.get('/fetch_artifact/user/repo/artifact.txt')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.decode(), "hello world")

    def test_fetch_artifact_dvc_cache(self):
        # DVC cache nomenclature: .dvc/cache/files/md5/<2_chars>/<rest>
        hash_val = "3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d"
        repo = "user/repo"
        cache_dir = os.path.join(self.test_dir, repo, ".dvc", "cache", "files", "md5", hash_val[:2])
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, hash_val[2:])
        with open(cache_file, "wb") as f:
            f.write(b"binary data")

        # In DVC, these files are usually served via their path in the cache
        fetch_path = f"{repo}/.dvc/cache/files/md5/{hash_val[:2]}/{hash_val[2:]}"
        resp = self.app.get(f'/fetch_artifact/{fetch_path}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, b"binary data")

    def test_fetch_artifact_traversal_protection(self):
        # Create a file outside REPOS_DIR
        outside_file = os.path.join(os.path.dirname(self.test_dir), "secret.txt")
        with open(outside_file, "w") as f:
            f.write("secret")

        # Attempt directory traversal
        resp = self.app.get('/fetch_artifact/../secret.txt')
        # Flask's send_from_directory should handle this and return 404 or 400
        # Actually it returns 404 because it can't find it within the directory
        self.assertNotEqual(resp.status_code, 200)

        if os.path.exists(outside_file):
            os.remove(outside_file)

if __name__ == '__main__':
    unittest.main()
