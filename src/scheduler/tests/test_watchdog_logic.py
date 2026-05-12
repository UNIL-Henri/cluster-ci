import time
import os
import threading
import subprocess
from unittest.mock import MagicMock, patch
from src.scheduler.worker_agent import LivenessWatchdog

def test_watchdog_zombie_detection():
    job_id = "test-job-id"
    log_path = "test_job.log"

    # Create a dummy log file
    with open(log_path, "w") as f:
        f.write("start\n")

    # Initialize watchdog with a very short window for testing (e.g. 1 second)
    # We'll mock the check interval and window to make it fast.
    watchdog = LivenessWatchdog(job_id, log_path, window_minutes=0.01) # ~0.6 seconds
    watchdog.window_seconds = 1 # Force 1 second window

    # Mock metrics to return 0 (no activity)
    with patch.object(LivenessWatchdog, 'get_container_metrics', return_value={"cpu": "0.00%", "net": "0B / 0B"}), \
         patch.object(LivenessWatchdog, 'get_gpu_metrics', return_value=0), \
         patch('subprocess.run') as mock_run:

        # Start watchdog in a way that we can control its loop
        # We'll monkeypatch the sleep to speed up the test
        with patch('time.sleep', return_value=None):
            # We need to ensure it runs at least once and then hits the timeout
            # The run() loop uses a while not self.stop_event.is_set()

            def side_effect(*args, **kwargs):
                # After a few iterations, if zombie_detected is True, we stop the event
                if watchdog.zombie_detected:
                    watchdog.stop()

            # We can't easily side_effect on time.sleep to stop the loop because it's inside run()
            # Let's run it in a thread and wait
            watchdog.start()

            # Wait for it to detect zombie
            start_time = time.time()
            while not watchdog.zombie_detected and time.time() - start_time < 5:
                time.sleep(0.1)

            watchdog.stop()
            watchdog.join(timeout=1)

            assert watchdog.zombie_detected is True
            mock_run.assert_any_call(["docker", "kill", f"cluster-job-{job_id}"], capture_output=True)

    if os.path.exists(log_path):
        os.remove(log_path)

def test_watchdog_activity_prevents_kill():
    job_id = "test-job-id-active"
    log_path = "test_job_active.log"

    with open(log_path, "w") as f:
        f.write("start\n")

    watchdog = LivenessWatchdog(job_id, log_path, window_minutes=0.01)
    watchdog.window_seconds = 2

    # Mock metrics to return activity.
    # For net, it needs to CHANGE to be detected as activity if tracked by delta.
    metrics_seq = [
        {"cpu": "10.00%", "net": "1KB / 1KB"},
        {"cpu": "10.00%", "net": "2KB / 1KB"}
    ]

    with patch.object(LivenessWatchdog, 'get_container_metrics', side_effect=metrics_seq), \
         patch.object(LivenessWatchdog, 'get_gpu_metrics', return_value=50), \
         patch('subprocess.run') as mock_run:

        watchdog.start()
        time.sleep(3) # Wait longer than window_seconds
        watchdog.stop()
        watchdog.join(timeout=1)

        assert watchdog.zombie_detected is False
        # Ensure docker kill was NOT called
        for call in mock_run.call_args_list:
            assert "kill" not in call[0][0]

    if os.path.exists(log_path):
        os.remove(log_path)
