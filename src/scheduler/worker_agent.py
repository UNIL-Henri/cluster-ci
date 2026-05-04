import time
import requests
import os
import sys
import socket
import psutil
import subprocess
import logging
import uuid
import threading
import json
from flask import Flask, jsonify, send_from_directory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HEADNODE_URL = os.environ.get("HEADNODE_URL", "http://localhost:5000")
CLUSTER_TOKEN = os.environ.get("CLUSTER_TOKEN")

def get_headers():
    headers = {}
    if CLUSTER_TOKEN:
        headers["Authorization"] = f"Bearer {CLUSTER_TOKEN}"
    return headers

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
AGENT_PORT = int(os.environ.get("AGENT_PORT", 6000))
SERVICE_URL = os.environ.get("SERVICE_URL", f"http://{HOSTNAME}:{AGENT_PORT}")

# Global state for current job tracking
current_job_id = None
current_process = None
job_lock = threading.Lock()

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
            "service_url": SERVICE_URL,
            "total_ram_gb": total_gb,
            "available_ram_gb": available_gb
        }, headers=get_headers())
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to register: {e}")
        return False

def poll_for_job():
    try:
        resp = requests.get(f"{HEADNODE_URL}/worker_poll/{WORKER_ID}", headers=get_headers())
        resp.raise_for_status()
        data = resp.json()
        if data.get("job_id"):
            return data
    except Exception as e:
        logger.error(f"Failed to poll: {e}")
    return None

def update_job_status(job_id, status, exit_code=None, commit_hash=None, viewer_port=None):
    try:
        payload = {"job_id": job_id, "status": status}
        if exit_code is not None:
            payload["exit_code"] = exit_code
        if commit_hash is not None:
            payload["commit_hash"] = commit_hash
        if viewer_port is not None:
            payload["viewer_port"] = viewer_port
        resp = requests.post(f"{HEADNODE_URL}/update_job_status", json=payload, headers=get_headers())
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to update job status: {e}")

def execute_job(job):
    global current_job_id, current_process
    job_id = job['job_id']
    repo = job['repo']
    branch = job['branch']
    ram_limit_gb = job['ram_required_gb']
    ram_limit_bytes = ram_limit_gb * (1024**3)

    logger.info(f"Executing job {job_id} for {repo}@{branch} with {ram_limit_gb}GB limit")
    update_job_status(job_id, 'running')

    with job_lock:
        current_job_id = job_id

    # We call the cluster-ci-run command which is supposed to be in /usr/local/bin/cluster-ci-run
    # or provided via CLUSTER_CI_RUN_PATH environment variable
    executable = os.environ.get("CLUSTER_CI_RUN_PATH", "/usr/local/bin/cluster-ci-run")
    cmd = [executable, repo, branch]

    env = os.environ.copy()
    env["CLUSTER_CI_MODE"] = "executor"

    try:
        process = subprocess.Popen(cmd, env=env)
        with job_lock:
            current_process = process
        oom_triggered = False
        port_reported = False

        # Watchdog loop
        while process.poll() is None:
            # Try to report dynamic viewer port if not already done
            if not port_reported:
                port_file = os.path.join(REPOS_DIR, repo, ".cluster-ci-viewer-port")
                if os.path.exists(port_file):
                    try:
                        with open(port_file, 'r') as f:
                            viewer_port = int(f.read().strip())
                        logger.info(f"Reporting dynamic viewer port {viewer_port} for job {job_id}")
                        update_job_status(job_id, 'running', viewer_port=viewer_port)
                        port_reported = True
                    except Exception as e:
                        logger.error(f"Failed to read/report viewer port: {e}")

            try:
                parent = psutil.Process(process.pid)
                # RSS of parent
                total_rss = parent.memory_info().rss
                # RSS of all children recursively
                for child in parent.children(recursive=True):
                    try:
                        total_rss += child.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

                if total_rss > ram_limit_bytes:
                    oom_triggered = True
                    total_rss_gb = total_rss / (1024**3)
                    error_msg = f"❌ [OOM ARTIFICIEL CLUSTER] Exécution interrompue ! Vous aviez réservé {ram_limit_gb} Go de RAM dans '.cluster-ci', or votre pipeline vient d'atteindre {total_rss_gb:.2f} Go. Veuillez augmenter votre réservation.\n"
                    sys.stderr.write(error_msg)
                    sys.stderr.flush()

                    # Kill the process tree aggressively
                    for child in parent.children(recursive=True):
                        try:
                            child.kill()
                        except psutil.NoSuchProcess:
                            pass
                    parent.kill()
                    break
            except psutil.NoSuchProcess:
                break

            time.sleep(2)

        exit_code = process.wait()

        # Try to extract the commit hash from the job's directory
        commit_hash = None
        commit_file = os.path.join(REPOS_DIR, repo, ".cluster-ci-commit")
        if os.path.exists(commit_file):
            try:
                with open(commit_file, 'r') as f:
                    commit_hash = f.read().strip()
            except Exception as e:
                logger.error(f"Failed to read commit hash file: {e}")

        if oom_triggered:
            update_job_status(job_id, 'failed', 137, commit_hash=commit_hash)
        elif exit_code == 0:
            update_job_status(job_id, 'completed', exit_code, commit_hash=commit_hash)
        elif exit_code < 0:
            # Likely killed by a signal (cancellation)
            logger.info(f"Job {job_id} was killed (exit code {exit_code})")
            update_job_status(job_id, 'failed', exit_code, commit_hash=commit_hash)
        else:
            update_job_status(job_id, 'failed', exit_code, commit_hash=commit_hash)

    except Exception as e:
        logger.error(f"Execution failed: {e}")
        update_job_status(job_id, 'failed', -1)
    finally:
        with job_lock:
            current_job_id = None
            current_process = None

def drain_pending_syncs():
    logger.info("Starting drain of pending synchronizations...")

    # Path to registry.json
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    registry_path = os.path.join(base_dir, "repositories", "registry.json")

    logger.info(f"Looking for registry at: {registry_path}")
    if not os.path.exists(registry_path):
        logger.info("No registry.json found, nothing to drain.")
        return

    try:
        with open(registry_path, 'r') as f:
            registry = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load registry: {e}")
        return

    for project_name, data in registry.items():
        if data.get("sync_status") == "pending":
            logger.info(f"Project {project_name} has pending sync. Checking headnode space...")
            try:
                resp = requests.get(f"{HEADNODE_URL}/check_space", timeout=5, headers=get_headers())
                resp.raise_for_status()
                space_info = resp.json()

                if space_info.get("sufficient"):
                    logger.info(f"Headnode space sufficient. Pushing {project_name}...")
                    project_dir = os.path.join(base_dir, "repositories", project_name)
                    if os.path.exists(project_dir):
                        # Execute dvc push via uv
                        res = subprocess.run(["uv", "run", "dvc", "push"], cwd=project_dir)
                        if res.returncode == 0:
                            # Mark as done
                            subprocess.run(["python3", os.path.join(base_dir, "src/runner/gc_orchestrator.py"), "mark-sync-done", project_name])
                            logger.info(f"Successfully pushed and marked {project_name} as done.")
                        else:
                            logger.error(f"dvc push failed for {project_name}")
                    else:
                        logger.warning(f"Project directory {project_dir} not found for {project_name}")
                else:
                    logger.info(f"Headnode still full ({space_info.get('free_gb'):.2f} GB free). Stopping drain.")
                    break
            except Exception as e:
                logger.error(f"Error during drain for {project_name}: {e}")

# Webhook server
app = Flask(__name__)

REPOS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "repositories")

@app.route('/cancel/<job_id>', methods=['POST'])
def cancel_job(job_id):
    global current_job_id, current_process
    logger.info(f"Received cancellation request for job {job_id}")

    with job_lock:
        if current_job_id == job_id and current_process:
            logger.info(f"Killing process tree for job {job_id}")
            try:
                parent = psutil.Process(current_process.pid)
                for child in parent.children(recursive=True):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                parent.kill()
                return jsonify({"status": "cancelled", "message": "Process tree terminated"}), 200
            except psutil.NoSuchProcess:
                return jsonify({"status": "already_finished", "message": "Process already finished"}), 200
            except Exception as e:
                logger.error(f"Error while killing process: {e}")
                return jsonify({"status": "error", "message": str(e)}), 500
        else:
            logger.warning(f"No active job matches {job_id} (current: {current_job_id})")
            return jsonify({"status": "not_found", "message": "Job not running on this worker"}), 404

@app.route('/fetch_artifact/<path:file_path>', methods=['GET'])
def fetch_artifact(file_path):
    """
    Serves a file from the repositories directory.
    send_from_directory provides protection against directory traversal.
    """
    logger.info(f"Worker received request for artifact: {file_path}")
    return send_from_directory(REPOS_DIR, file_path)

@app.route('/webhook/drain_request', methods=['POST'])
def drain_request():
    logger.info("Received drain request webhook")
    # Run drain in a separate thread to avoid blocking the webhook response
    threading.Thread(target=drain_pending_syncs).start()
    return jsonify({"status": "accepted"})

def start_webhook_server():
    app.run(host='0.0.0.0', port=AGENT_PORT)

def main_loop():
    # Start webhook server in background thread
    threading.Thread(target=start_webhook_server, daemon=True).start()

    while True:
        if register():
            job = poll_for_job()
            if job:
                execute_job(job)
        time.sleep(10)

if __name__ == '__main__':
    main_loop()
