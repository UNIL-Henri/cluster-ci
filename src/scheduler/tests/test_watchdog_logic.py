import time
import os
import json
import subprocess
from unittest.mock import MagicMock, patch
from src.runner.gc_orchestrator import run_zombie_gc, get_zombie_registry_path, get_repositories_dir

def test_run_zombie_gc_detection():
    # Setup
    repo_dir = get_repositories_dir()
    repo_dir.mkdir(parents=True, exist_ok=True)
    registry_path = get_zombie_registry_path()
    if registry_path.exists(): registry_path.unlink()

    container_name = "cluster-job-test-job"

    # 1. Mock docker ps to return one container
    with patch('subprocess.run') as mock_run:
        def side_effect(cmd, **kwargs):
            if cmd[0:2] == ["docker", "ps"]:
                return MagicMock(stdout=container_name, returncode=0)
            if cmd[0:2] == ["docker", "stats"]:
                # First run: no activity
                return MagicMock(stdout='{"cpu": "0.00%", "net": "0B / 0B"}', returncode=0)
            if cmd[0] == "nvidia-smi":
                return MagicMock(stdout="0", returncode=0)
            return MagicMock(stdout="", returncode=0)

        mock_run.side_effect = side_effect

        # Run first time to register
        run_zombie_gc()
        assert registry_path.exists()

        with open(registry_path, "r") as f:
            reg = json.load(f)
            assert container_name in reg
            first_activity = reg[container_name]["last_activity"]

        # 2. Wait and run again with no activity
        with patch('time.time', return_value=time.time() + 700): # 11 minutes later
             run_zombie_gc()

             # Check that docker rm was called
             mock_run.assert_any_call(["docker", "rm", "-f", container_name], capture_output=True)
             mock_run.assert_any_call(["docker", "rm", "-f", "cluster-viewer-test-job"], capture_output=True)

def test_run_zombie_gc_activity_resets_timer():
    # Setup
    repo_dir = get_repositories_dir()
    repo_dir.mkdir(parents=True, exist_ok=True)
    registry_path = get_zombie_registry_path()
    if registry_path.exists(): registry_path.unlink()

    container_name = "cluster-job-test-job-active"

    with patch('subprocess.run') as mock_run:
        # First call: Idle
        mock_run.side_effect = [
            MagicMock(stdout=container_name, returncode=0), # ps
            MagicMock(stdout='{"cpu": "0.00%", "net": "0B / 0B"}', returncode=0), # stats
            MagicMock(stdout="0", returncode=0), # gpu
        ]
        run_zombie_gc()

        with open(registry_path, "r") as f:
            reg = json.load(f)
            t1 = reg[container_name]["last_activity"]

        # Second call: 5 mins later with activity
        with patch('time.time', return_value=time.time() + 300):
            mock_run.side_effect = [
                MagicMock(stdout=container_name, returncode=0), # ps
                MagicMock(stdout='{"cpu": "10.00%", "net": "1KB / 0B"}', returncode=0), # stats (activity!)
                MagicMock(stdout="0", returncode=0), # gpu
            ]
            run_zombie_gc()

            with open(registry_path, "r") as f:
                reg = json.load(f)
                t2 = reg[container_name]["last_activity"]
                assert t2 > t1
