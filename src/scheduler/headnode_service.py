from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from persistence import init_db, get_db_conn
import uuid
import datetime
import os
import shutil
import requests

app = Flask(__name__)

FREE_SPACE_THRESHOLD_GB = 100
CLUSTER_TOKEN = os.environ.get("CLUSTER_TOKEN")

def check_token():
    if not CLUSTER_TOKEN:
        return True # Default to no auth if not set
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    token = auth_header.split(" ")[1]
    return token == CLUSTER_TOKEN

@app.before_request
def require_token():
    # Only protect API endpoints that workers or users use to modify state
    protected_endpoints = ['register_worker', 'submit_job', 'update_job_status', 'worker_poll', 'notify_cleanup']
    if request.endpoint in protected_endpoints:
        if not check_token():
            return jsonify({"error": "Unauthorized"}), 401

@app.route('/register_worker', methods=['POST'])
def register_worker():
    data = request.json
    worker_id = data.get('worker_id')
    hostname = data.get('hostname')
    service_url = data.get('service_url')
    total_ram_gb = data.get('total_ram_gb')
    # We ignore the worker's reported available RAM for scheduling
    # to avoid the race condition where physical RAM isn't yet claimed by jobs.
    # available_ram_gb = data.get('available_ram_gb')

    with get_db_conn() as conn:
        cursor = conn.cursor()
        # On first registration, we initialize available_ram_gb to total_ram_gb
        cursor.execute('''
            INSERT INTO workers (worker_id, hostname, service_url, total_ram_gb, available_ram_gb, last_seen, status)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 'online')
            ON CONFLICT(worker_id) DO UPDATE SET
                hostname = ?,
                service_url = ?,
                last_seen = CURRENT_TIMESTAMP,
                status = 'online'
        ''', (worker_id, hostname, service_url, total_ram_gb, total_ram_gb, hostname, service_url))
        conn.commit()

    return jsonify({"status": "ok"})

@app.route('/submit_job', methods=['POST'])
def submit_job():
    data = request.json
    repo = data.get('repo')
    branch = data.get('branch')
    ram_required_gb = data.get('ram_required_gb', 0)
    job_id = str(uuid.uuid4())

    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO jobs (job_id, repo, branch, ram_required_gb, status)
            VALUES (?, ?, ?, ?, 'pending')
        ''', (job_id, repo, branch, ram_required_gb))
        conn.commit()

    return jsonify({"job_id": job_id, "status": "pending"})

@app.route('/job_status/<job_id>', methods=['GET'])
def job_status(job_id):
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM jobs WHERE job_id = ?', (job_id,))
        job = cursor.fetchone()
        if job:
            return jsonify(dict(job))
        else:
            return jsonify({"error": "Job not found"}), 404

@app.route('/worker_poll/<worker_id>', methods=['GET'])
def worker_poll(worker_id):
    # This endpoint is for workers to check if they have a job assigned
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM jobs
            WHERE worker_id = ? AND status = 'assigned'
            ORDER BY created_at ASC LIMIT 1
        ''', (worker_id,))
        job = cursor.fetchone()
        if job:
            return jsonify(dict(job))
        else:
            return jsonify({"status": "no_job"})

@app.route('/update_job_status', methods=['POST'])
def update_job_status():
    data = request.json
    job_id = data.get('job_id')
    status = data.get('status')
    exit_code = data.get('exit_code')

    with get_db_conn() as conn:
        cursor = conn.cursor()
        if status == 'running':
            cursor.execute('UPDATE jobs SET status = ?, started_at = CURRENT_TIMESTAMP WHERE job_id = ?', (status, job_id))
        elif status in ['completed', 'failed']:
            # Restore RAM to the worker
            cursor.execute('SELECT worker_id, ram_required_gb FROM jobs WHERE job_id = ?', (job_id,))
            job = cursor.fetchone()
            if job and job['worker_id']:
                cursor.execute('''
                    UPDATE workers
                    SET available_ram_gb = available_ram_gb + ?
                    WHERE worker_id = ?
                ''', (job['ram_required_gb'], job['worker_id']))

            cursor.execute('UPDATE jobs SET status = ?, finished_at = CURRENT_TIMESTAMP, exit_code = ? WHERE job_id = ?', (status, exit_code, job_id))
        else:
            cursor.execute('UPDATE jobs SET status = ? WHERE job_id = ?', (status, job_id))
        conn.commit()
    return jsonify({"status": "ok"})

@app.route('/check_space', methods=['GET'])
def check_space():
    # Use the root of the repositories directory if it exists, else use current dir
    repo_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "repositories")
    if not os.path.exists(repo_dir):
        repo_dir = "."

    usage = shutil.disk_usage(repo_dir)
    free_gb = usage.free / (1024**3)

    return jsonify({
        "free_gb": free_gb,
        "threshold_gb": FREE_SPACE_THRESHOLD_GB,
        "sufficient": free_gb > FREE_SPACE_THRESHOLD_GB
    })

REPOS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "repositories")

@app.route('/artifacts/<repo_owner>/<repo_name>/<branch>/<path:file_path>', methods=['GET'])
def artifacts(repo_owner, repo_name, branch, file_path):
    repo = f"{repo_owner}/{repo_name}"
    local_path = os.path.join(repo, file_path)

    # 1. Try local storage first
    if os.path.exists(os.path.join(REPOS_DIR, local_path)):
        return send_from_directory(REPOS_DIR, local_path)

    # 2. If not found locally, find which worker has it
    with get_db_conn() as conn:
        cursor = conn.cursor()
        # Find the last worker that completed a job for this repo and branch
        cursor.execute('''
            SELECT w.service_url
            FROM jobs j
            JOIN workers w ON j.worker_id = w.worker_id
            WHERE j.repo = ? AND j.branch = ? AND j.status = 'completed'
            ORDER BY j.finished_at DESC
            LIMIT 1
        ''', (repo, branch))
        worker = cursor.fetchone()

    if worker and worker['service_url']:
        worker_url = worker['service_url']
        try:
            # Proxy the request to the worker
            target_url = f"{worker_url}/fetch_artifact/{local_path}"
            req = requests.get(target_url, stream=True, timeout=10)

            # Return streamed response
            return Response(
                stream_with_context(req.iter_content(chunk_size=1024)),
                content_type=req.headers.get('content-type'),
                status=req.status_code
            )
        except Exception as e:
            return jsonify({"error": f"Failed to proxy to worker: {str(e)}"}), 500

    return jsonify({"error": "Artifact not found locally or on any known worker for this branch"}), 404

@app.route('/notify_cleanup', methods=['POST'])
def notify_cleanup():
    # Fetch all online workers with a service_url
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT service_url FROM workers WHERE status = 'online' AND service_url IS NOT NULL")
        workers = cursor.fetchall()

    notified = 0
    errors = 0
    for worker in workers:
        service_url = worker['service_url']
        try:
            # Send drain request to each worker
            resp = requests.post(f"{service_url}/webhook/drain_request", timeout=5)
            if resp.status_code == 200:
                notified += 1
            else:
                errors += 1
        except Exception:
            errors += 1

    return jsonify({"status": "ok", "notified": notified, "errors": errors})

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000)
