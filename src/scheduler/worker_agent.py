import time
import requests
import os
import socket
import psutil
import subprocess
import logging
import uuid

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HEADNODE_URL = os.environ.get("HEADNODE_URL", "http://localhost:5000")
# Generate or load a persistent worker ID
WORKER_ID_FILE = "worker_id.txt"
if os.path.exists(WORKER_ID_FILE):
    with open(WORKER_ID_FILE, 'r') as f:
        WORKER_ID = f.read().strip()
else:
    WORKER_ID = str(uuid.uuid4())
    with open(WORKER_ID_FILE, 'w') as f:
        f.write(WORKER_ID)

HOSTNAME = socket.gethostname()

def get_ram_info():
    mem = psutil.virtual_memory()
    total_gb = mem.total / (1024**3)
    available_gb = mem.available / (1024**3)
    return total_gb, available_gb

def register():
    total_gb, available_gb = get_ram_info()
    try:
        resp = requests.post(f"{HEADNODE_URL}/register_worker", json={
            "worker_id": WORKER_ID,
            "hostname": HOSTNAME,
            "total_ram_gb": total_gb,
            "available_ram_gb": available_gb
        })
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to register: {e}")
        return False

def poll_for_job():
    try:
        resp = requests.get(f"{HEADNODE_URL}/worker_poll/{WORKER_ID}")
        resp.raise_for_status()
        data = resp.json()
        if data.get("job_id"):
            return data
    except Exception as e:
        logger.error(f"Failed to poll: {e}")
    return None

def update_job_status(job_id, status, exit_code=None):
    try:
        payload = {"job_id": job_id, "status": status}
        if exit_code is not None:
            payload["exit_code"] = exit_code
        resp = requests.post(f"{HEADNODE_URL}/update_job_status", json=payload)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to update job status: {e}")

def execute_job(job):
    job_id = job['job_id']
    repo = job['repo']
    branch = job['branch']
    ram_limit_gb = job['ram_required_gb']

    logger.info(f"Executing job {job_id} for {repo}@{branch} with {ram_limit_gb}GB limit")
    update_job_status(job_id, 'running')

    # Use systemd-run for memory isolation
    # We call the cluster-ci-run command which is supposed to be in /usr/local/bin/cluster-ci-run
    # Note: In a real worker, we need to make sure this script is present.

    memory_limit = f"{int(ram_limit_gb * 1024)}M"

    cmd = [
        "sudo", "systemd-run", "--scope", "--quiet",
        f"--property=MemoryMax={memory_limit}",
        f"--property=MemorySwapMax={memory_limit}",
        "--setenv=CLUSTER_CI_MODE=executor",
        "cluster-ci-run", repo, branch
    ]

    try:
        process = subprocess.Popen(cmd)
        exit_code = process.wait()

        if exit_code == 0:
            update_job_status(job_id, 'completed', exit_code)
        else:
            update_job_status(job_id, 'failed', exit_code)

    except Exception as e:
        logger.error(f"Execution failed: {e}")
        update_job_status(job_id, 'failed', -1)

def main_loop():
    while True:
        if register():
            job = poll_for_job()
            if job:
                execute_job(job)
        time.sleep(10)

if __name__ == '__main__':
    main_loop()
