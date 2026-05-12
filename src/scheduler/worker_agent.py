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
import tempfile
import shutil
from flask import Flask, jsonify, send_from_directory, request, Response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HEADNODE_URL = os.environ.get("HEADNODE_URL", "http://localhost:5000")
CLUSTER_TOKEN = os.environ.get("CLUSTER_TOKEN")
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGS_DIR = os.path.join(BASE_DIR, "job_logs")
os.makedirs(LOGS_DIR, exist_ok=True)

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

def get_storage_info():
    try:
        # Use the repositories directory if it exists, otherwise the root of the project
        target_path = REPOS_DIR if os.path.exists(REPOS_DIR) else BASE_DIR
        usage = shutil.disk_usage(target_path)
        total_gb = usage.total / (1024**3)
        available_gb = usage.free / (1024**3)
        return total_gb, available_gb
    except Exception as e:
        logger.error(f"Error getting storage info: {e}")
        return 0.0, 0.0

def heartbeat_loop():
    is_startup = True
    while True:
        total_ram_gb, available_ram_gb = get_ram_info()
        total_storage_gb, available_storage_gb = get_storage_info()
        try:
            resp = requests.post(f"{HEADNODE_URL}/register_worker", json={
                "worker_id": WORKER_ID,
                "hostname": HOSTNAME,
                "service_url": SERVICE_URL,
                "total_ram_gb": total_ram_gb,
                "available_ram_gb": available_ram_gb,
                "total_storage_gb": total_storage_gb,
                "available_storage_gb": available_storage_gb,
                "is_startup": is_startup
            }, headers=get_headers())
            resp.raise_for_status()
            is_startup = False
        except Exception as e:
            logger.error(f"Failed to send heartbeat: {e}")
        time.sleep(10)

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
    p2p_url = job.get('p2p_url')
    gh_token = job.get('gh_token')
    env_vars = job.get('env_vars')
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
    if p2p_url:
        logger.info(f"Injecting P2P URL for job {job_id}: {p2p_url}")
        env["DVC_REMOTE_P2P_URL"] = p2p_url
    if gh_token:
        logger.info(f"Injecting GH_TOKEN for job {job_id}")
        env["GH_TOKEN"] = gh_token

    secrets_file = None
    if env_vars:
        try:
            parsed_vars = json.loads(env_vars) if isinstance(env_vars, str) else env_vars
            if parsed_vars:
                # Create a secure temp file for job secrets
                fd, secrets_file = tempfile.mkstemp(prefix=f"job_secrets_{job_id}_", suffix=".env")
                with os.fdopen(fd, 'w') as f:
                    for k, v in parsed_vars.items():
                        f.write(f"{k}={v}\n")
                logger.info(f"Injecting {len(parsed_vars)} custom environment variables via {secrets_file}")
                env["CLUSTER_CI_SECRETS_FILE"] = secrets_file
        except Exception as e:
            logger.error(f"Failed to write job secrets: {e}")

    log_path = os.path.join(LOGS_DIR, f"{job_id}.log")
    log_file = open(log_path, 'w')

    try:
        process = subprocess.Popen(cmd, env=env, stdout=log_file, stderr=subprocess.STDOUT)
        with job_lock:
            current_process = process
        port_reported = False

        # Status monitoring loop (no more manual watchdog as it is handled by Docker)
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

        if exit_code == 137:
            # Docker returns 137 when OOM-killed
            error_msg = f"❌ [CLUSTER ARTIFICIAL OOM] Execution interrupted! You reserved {ram_limit_gb} GB of RAM in '.cluster-ci', but your pipeline was just killed by Docker. Please increase your reservation.\n"
            sys.stderr.write(error_msg)
            sys.stderr.flush()
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
        if 'log_file' in locals() and not log_file.closed:
            log_file.close()
        with job_lock:
            current_job_id = None
            current_process = None
        if secrets_file and os.path.exists(secrets_file):
            try:
                os.remove(secrets_file)
                logger.info(f"Cleaned up secrets file: {secrets_file}")
            except Exception as e:
                logger.error(f"Failed to cleanup secrets file: {e}")

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
                        # Check if a default DVC remote is configured
                        has_remote = False
                        dvc_config_path = os.path.join(project_dir, ".dvc", "config")
                        dvc_config_local_path = os.path.join(project_dir, ".dvc", "config.local")
                        
                        for config_path in [dvc_config_path, dvc_config_local_path]:
                            if os.path.exists(config_path):
                                with open(config_path, "r") as f:
                                    content = f.read()
                                    import re
                                    if re.search(r"^\s*remote\s*=", content, re.MULTILINE):
                                        has_remote = True
                                        break

                        if not has_remote:
                            logger.info(f"No default DVC remote configured for {project_name}. Skipping push.")
                            subprocess.run(["python3", os.path.join(base_dir, "src/runner/gc_orchestrator.py"), "mark-sync-done", project_name])
                        else:
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
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                return jsonify({"status": "cancelled", "message": "Termination signal sent to children"}), 200
            except psutil.NoSuchProcess:
                return jsonify({"status": "already_finished", "message": "Process already finished"}), 200
            except Exception as e:
                logger.error(f"Error while killing process: {e}")
                return jsonify({"status": "error", "message": str(e)}), 500
        else:
            logger.warning(f"No active job matches {job_id} (current: {current_job_id})")
            return jsonify({"status": "not_found", "message": "Job not running on this worker"}), 404

@app.route('/job_logs/<job_id>', methods=['GET'])
def get_job_logs(job_id):
    offset = int(request.args.get('offset', 0))
    log_path = os.path.join(LOGS_DIR, f"{job_id}.log")
    
    if not os.path.exists(log_path):
        return jsonify({"logs": "", "offset": offset})
        
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            f.seek(offset)
            new_logs = f.read()
            new_offset = f.tell()
        return jsonify({"logs": new_logs, "offset": new_offset})
    except Exception as e:
        logger.error(f"Error reading logs for {job_id}: {e}")
        return jsonify({"logs": "", "offset": offset}), 500

@app.route('/fetch_artifact/<path:file_path>', methods=['GET'])
def fetch_artifact(file_path):
    """
    Serves a file from the repositories directory.
    send_from_directory provides protection against directory traversal.
    """
    logger.info(f"Worker received request for artifact: {file_path}")
    return send_from_directory(REPOS_DIR, file_path)

@app.route('/check_cache', methods=['POST'])
def check_cache():
    """
    Checks if the worker has the specified DVC cache files.
    Input JSON: {"repo": "owner/repo", "hashes": ["hash1", "hash2", ...]}
    Returns: JSON list of hashes present on this worker.
    """
    data = request.get_json()
    if not data or 'repo' not in data or 'hashes' not in data:
        return jsonify({"error": "Missing repo or hashes"}), 400

    repo = data['repo']
    hashes = data['hashes']
    found_hashes = []

    for h in hashes:
        if len(h) < 2:
            continue
        # DVC CAS nomenclature: .dvc/cache/files/md5/<2_chars>/<rest>
        cache_path = os.path.join(REPOS_DIR, repo, ".dvc", "cache", "files", "md5", h[:2], h[2:])
        if os.path.exists(cache_path):
            found_hashes.append(h)

    return jsonify(found_hashes)

def get_executable(name):
    """Finds an executable in system PATH, local bin, or current venv."""
    cmd = shutil.which(name)
    if cmd: return cmd
    local_path = os.path.expanduser(f"~/.local/bin/{name}")
    if os.path.exists(local_path): return local_path
    venv_path = os.path.join(os.path.dirname(sys.executable), name)
    if os.path.exists(venv_path): return venv_path
    return name

DVC_CMD = get_executable("dvc")

@app.route('/api/worker/dvc/list', methods=['GET'])
def worker_dvc_list():
    repo = request.args.get('repo')
    rev = request.args.get('rev')
    if not repo: return jsonify({"error": "Missing repo"}), 400

    repo_path = os.path.join(REPOS_DIR, repo)
    if not os.path.exists(repo_path):
        return jsonify({"error": "Repository not found on this worker"}), 404

    cmd = [DVC_CMD, "list", ".", "--dvc-only", "--json"]
    if rev: cmd += ["--rev", rev]

    try:
        res = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True)
        if res.returncode == 0:
            return Response(res.stdout, mimetype='application/json')
        return jsonify({"error": res.stderr}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/worker/dvc/get', methods=['GET'])
def worker_dvc_get():
    repo = request.args.get('repo')
    rev = request.args.get('rev')
    file_path = request.args.get('path')
    if not repo or not file_path: return jsonify({"error": "Missing repo or path"}), 400

    repo_path = os.path.join(REPOS_DIR, repo)
    if not os.path.exists(repo_path):
        return jsonify({"error": "Repository not found on this worker"}), 404

    tmp_dir = tempfile.mkdtemp()
    try:
        cmd = [DVC_CMD, "get", ".", file_path, "--out", tmp_dir]
        if rev: cmd += ["--rev", rev]

        res = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True)
        if res.returncode != 0:
            return jsonify({"error": res.stderr}), 500

        filename = os.path.basename(file_path)
        full_path = os.path.join(tmp_dir, filename)

        if not os.path.exists(full_path) or not os.path.isfile(full_path):
            shutil.rmtree(tmp_dir)
            return jsonify({"error": "Path is a directory or not found"}), 400

        def generate():
            try:
                with open(full_path, 'rb') as f:
                    while True:
                        chunk = f.read(4096)
                        if not chunk: break
                        yield chunk
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        return Response(generate(), mimetype='application/octet-stream',
                        headers={"Content-Disposition": f"attachment; filename={filename}"})
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": str(e)}), 500

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
    
    # Start heartbeat in background thread
    threading.Thread(target=heartbeat_loop, daemon=True).start()

    while True:
        job = poll_for_job()
        if job:
            execute_job(job)
        time.sleep(5)

if __name__ == '__main__':
    main_loop()
