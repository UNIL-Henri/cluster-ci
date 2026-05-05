import unittest
import os
import time
import subprocess
import requests
import threading
from unittest.mock import patch, MagicMock
# Import app AFTER setting env vars
os.environ['GITHUB_CLIENT_ID'] = 'fake_id'
os.environ['GITHUB_CLIENT_SECRET'] = 'fake_secret'
os.environ['CLUSTER_DB_PATH'] = 'test_cluster_scheduler.db'
os.environ['DVC_VIEWER_TIMEOUT_MIN'] = '0' # Force immediate timeout for testing cleanup
from src.scheduler.headnode_service import app, local_viewers, local_viewers_lock, cleanup_inactive_viewers
from src.scheduler.persistence import init_db, get_db_conn

class TestPortalAndProxy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        # Ensure session is used
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test'
        cls.client = app.test_client()
        cls.app_context = app.app_context()
        cls.app_context.push()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists('test_cluster_scheduler.db'):
            os.remove('test_cluster_scheduler.db')
        cls.app_context.pop()

    def setUp(self):
        with local_viewers_lock:
            local_viewers.clear()
        # Clear session for each test to ensure isolation
        with self.client.session_transaction() as sess:
            sess.clear()

    def test_dashboard_renders_login_template(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Cluster-CI Portal', response.data)

    def test_view_project_redirects_when_not_logged_in(self):
        # Let's try to fetch what exactly is in the url_map for view_project
        with app.test_request_context():
            from flask import url_for
            target = url_for('view_project', owner='someowner', repo='somerepo')

        response = self.client.get(target, follow_redirects=False)
        self.assertEqual(response.status_code, 302, f"Failed for {target}. Body: {response.data}")
        self.assertTrue(response.location.endswith('/'))

    def test_view_project_404_when_repo_missing(self):
        with self.client.session_transaction() as sess:
            sess['user'] = {'login': 'testuser'}

        with app.test_request_context():
            from flask import url_for
            target = url_for('view_project', owner='nonexistent', repo='repo')

        response = self.client.get(target, follow_redirects=True)
        self.assertEqual(response.status_code, 404)
        # Check for presence of key words as strings from the decoded response
        body = response.data.decode('utf-8')
        self.assertIn('non', body)
        self.assertIn('trouv', body)
        self.assertIn('localement', body)

    def test_historical_proxy_spawns_process(self):
        # Mock login
        with self.client.session_transaction() as sess:
            sess['user'] = {'login': 'testuser'}
            sess['token'] = {'access_token': 'fake_token'}

        # Create a dummy repo directory
        os.makedirs('repositories/testowner/testrepo', exist_ok=True)

        with patch('subprocess.Popen') as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            with patch('src.scheduler.headnode_service.proxy_request') as mock_proxy:
                mock_proxy.return_value = app.response_class("proxied")
                response = self.client.get('/view/testowner/testrepo/')

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, b'proxied')
                mock_popen.assert_called()
                with local_viewers_lock:
                    self.assertIn('testowner/testrepo', local_viewers)

        # Clean up dummy repo
        import shutil
        shutil.rmtree('repositories/testowner')

    def test_inactivity_cleanup(self):
        # Manually add a fake viewer to the registry
        mock_proc = MagicMock()
        with local_viewers_lock:
            local_viewers['old/repo'] = {
                'proc': mock_proc,
                'port': 12345,
                'last_access': time.time() - 10 # 10 seconds ago, and timeout is 0
            }

        from src.scheduler.headnode_service import cleanup_inactive_viewers
        import src.scheduler.headnode_service
        src.scheduler.headnode_service.DVC_VIEWER_TIMEOUT_MIN = 0

        with patch('time.sleep', side_effect=[None, Exception("Stop loop")]):
            try:
                cleanup_inactive_viewers()
            except Exception as e:
                if str(e) != "Stop loop":
                    raise e

        with local_viewers_lock:
            self.assertNotIn('old/repo', local_viewers)
        mock_proc.terminate.assert_called()

if __name__ == '__main__':
    unittest.main()
