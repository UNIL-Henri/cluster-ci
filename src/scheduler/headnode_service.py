from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context, session, url_for, redirect, render_template
from persistence import init_db, get_db_conn
from authlib.integrations.flask_client import OAuth
import uuid
import datetime
import os
import shutil
import requests
import subprocess
import time
import threading
import socket
from urllib.parse import urlparse

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))

oauth = OAuth(app)
oauth.register(
    name='github',
    client_id=os.environ.get('GITHUB_CLIENT_ID'),
    client_secret=os.environ.get('GITHUB_CLIENT_SECRET'),
    access_token_url='https://github.com/login/oauth/access_token',
    access_token_params=None,
    authorize_url='https://github.com/login/oauth/authorize',
    authorize_params=None,
    api_base_url='https://api.github.com/',
    client_kwargs={'scope': 'repo,user:email'},
)

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

@app.route('/workers', methods=['GET'])
def list_workers():
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM workers')
        workers = [dict(row) for row in cursor.fetchall()]
    return jsonify(workers)

@app.route('/job_status/<job_id>', methods=['GET'])
def job_status(job_id):
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT j.*, w.service_url as worker_service_url
            FROM jobs j
            LEFT JOIN workers w ON j.worker_id = w.worker_id
            WHERE j.job_id = ?
        ''', (job_id,))
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
    commit_hash = data.get('commit_hash')
    viewer_port = data.get('viewer_port')

    with get_db_conn() as conn:
        cursor = conn.cursor()
        if status == 'running':
            cursor.execute('''
                UPDATE jobs SET
                    status = ?,
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                    commit_hash = COALESCE(?, commit_hash),
                    viewer_port = COALESCE(?, viewer_port)
                WHERE job_id = ?
            ''', (status, commit_hash, viewer_port, job_id))
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

            cursor.execute('UPDATE jobs SET status = ?, finished_at = CURRENT_TIMESTAMP, exit_code = ?, commit_hash = ? WHERE job_id = ?', (status, exit_code, commit_hash, job_id))
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

@app.route('/artifacts/<repo_owner>/<repo_name>/<rev>/<path:file_path>', methods=['GET'])
def artifacts(repo_owner, repo_name, rev, file_path):
    repo_slug = f"{repo_owner}/{repo_name}"
    repo_url = f"https://github.com/{repo_slug}"

    # --- Case 1: Try to serve via DVC directly from the remote/local cache ---
    # We use dvc get-url or dvc get to fetch the specific revision.
    # dvc get <url> <path> --rev <rev> --to <tmp>
    tmp_dir = os.path.join(REPOS_DIR, "_tmp_artifacts", str(uuid.uuid4()))
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        # We try to use the local repo if it exists for speed, else use the remote URL
        local_repo_path = os.path.join(REPOS_DIR, repo_slug)
        source = local_repo_path if os.path.exists(local_repo_path) else repo_url

        cmd = ["dvc", "get", source, file_path, "--rev", rev, "--out", tmp_dir]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            # Successfully extracted the file
            filename = os.path.basename(file_path)
            extracted_file = os.path.join(tmp_dir, filename)
            if os.path.isfile(extracted_file):
                resp = send_from_directory(tmp_dir, filename)
                # Cleanup after response is sent might be tricky with Flask's streaming,
                # but for small files it's fine. A better way is to stream it then delete.
                return resp
    except Exception as e:
        print(f"DVC get failed: {e}")
    finally:
        # Note: In a production environment, we'd need a more robust cleanup mechanism
        # or use a streaming response to delete the file after sending.
        pass

    # --- Case 2: Proxy to Worker if DVC get failed (e.g. not pushed to headnode yet) ---
    with get_db_conn() as conn:
        cursor = conn.cursor()
        # Find the last worker that completed a job for this repo and revision (commit_hash or branch)
        cursor.execute('''
            SELECT w.service_url
            FROM jobs j
            JOIN workers w ON j.worker_id = w.worker_id
            WHERE j.repo = ? AND (j.commit_hash = ? OR j.branch = ?) AND j.status = 'completed'
            ORDER BY j.finished_at DESC
            LIMIT 1
        ''', (repo_slug, rev, rev))
        worker = cursor.fetchone()

    if worker and worker['service_url']:
        worker_url = worker['service_url']
        try:
            # Proxy the request to the worker
            # Note: The worker's fetch_artifact currently doesn't support 'rev'.
            # It only serves from its current workspace.
            # However, for the 'most recent' completed job, it might be correct.
            local_path = os.path.join(repo_slug, file_path)
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

# --- History & DVC Exploration APIs ---

@app.route('/api/projects', methods=['GET'])
def api_list_projects():
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    token = session.get('token')
    target_config = os.environ.get("TARGET_REPO", "UNIL-DESI").lower()

    try:
        repos_resp = oauth.github.get('user/repos?per_page=100&sort=updated', token=token)
        if not repos_resp.ok:
            return jsonify({"error": "Failed to fetch repositories from GitHub", "details": repos_resp.text}), 502

        repos = repos_resp.json()
        if not isinstance(repos, list):
            return jsonify({"error": "Unexpected response from GitHub"}), 502

        allowed_repos = set()
        for r in repos:
            full_name = r['full_name'].lower()
            owner = r.get('owner', {}).get('login', '').lower()
            # User must have push permission
            if not r.get('permissions', {}).get('push', False):
                continue

            # Match against target organization OR specific repository
            if owner == target_config or full_name == target_config:
                allowed_repos.add(r['full_name'])

        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT DISTINCT repo FROM jobs')
            projects_in_db = [row['repo'] for row in cursor.fetchall()]

        # Only return projects that are in the database AND the user has access to
        projects = [p for p in projects_in_db if p in allowed_repos]
        return jsonify(projects)
    except Exception as e:
        print(f"Error fetching repos in API: {e}")
        return jsonify({"error": "Internal server error", "details": str(e)}), 500

@app.route('/api/projects/<path:repo>/runs', methods=['GET'])
def api_list_runs(repo):
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT job_id, branch, status, commit_hash, created_at, started_at, finished_at, exit_code
            FROM jobs
            WHERE repo = ?
            ORDER BY created_at DESC
        ''', (repo,))
        runs = [dict(row) for row in cursor.fetchall()]
    return jsonify(runs)

@app.route('/api/runs/<job_id>/files', methods=['GET'])
def api_run_files(job_id):
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT repo, commit_hash FROM jobs WHERE job_id = ?', (job_id,))
        job = cursor.fetchone()

    if not job:
        return jsonify({"error": "Job not found"}), 404

    repo = job['repo']
    commit_hash = job['commit_hash']

    if not commit_hash:
        return jsonify({"error": "Commit hash not found for this job. Historical exploration is unavailable."}), 400

    repo_url = f"https://github.com/{repo}"

    try:
        env = os.environ.copy()
        # If GITHUB_PAT is available, dvc list might be able to use it if configured,
        # though dvc list usually uses git credentials.

        cmd = ["dvc", "list", repo_url, "--rev", commit_hash, "--json"]
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)

        if result.returncode != 0:
            return jsonify({
                "error": "Failed to list DVC files",
                "details": result.stderr
            }), 500

        return Response(result.stdout, mimetype='application/json')
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- Portal & OAuth Routes ---

@app.route('/')
def dashboard():
    if 'user' not in session:
        return render_template('login.html')

    return render_template('dashboard.html', user=session['user'])

@app.route('/login')
def login():
    redirect_uri = url_for('authorize', _external=True)
    return oauth.github.authorize_redirect(redirect_uri)

@app.route('/authorize')
def authorize():
    token = oauth.github.authorize_access_token()
    resp = oauth.github.get('user', token=token)
    user = resp.json()
    session['user'] = user
    session['token'] = token
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.pop('user', None)
    session.pop('token', None)
    return redirect(url_for('dashboard'))

# --- Proxy & DVC-Viewer Management ---

DVC_VIEWER_PORT = int(os.environ.get("DVC_VIEWER_PORT", 8686))
DVC_VIEWER_TIMEOUT_MIN = int(os.environ.get("DVC_VIEWER_TIMEOUT_MIN", 30))

# Registry for local dvc-viewer processes
# { repo_full_name: { 'proc': subprocess.Popen, 'port': int, 'last_access': float } }
local_viewers = {}
local_viewers_lock = threading.Lock()

def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port

def cleanup_inactive_viewers():
    """Background task to kill local dvc-viewer processes after inactivity."""
    while True:
        time.sleep(60)
        now = time.time()
        to_delete = []
        with local_viewers_lock:
            for repo_name, viewer in local_viewers.items():
                if now - viewer['last_access'] > (DVC_VIEWER_TIMEOUT_MIN * 60):
                    print(f"Terminating inactive dvc-viewer for {repo_name} (port {viewer['port']})")
                    try:
                        viewer['proc'].terminate()
                        viewer['proc'].wait(timeout=5)
                    except Exception as e:
                        print(f"Error terminating process: {e}")
                        try:
                            viewer['proc'].kill()
                        except:
                            pass
                    to_delete.append(repo_name)

            for repo_name in to_delete:
                del local_viewers[repo_name]

@app.route('/view/<owner>/<repo>/')
@app.route('/view/<owner>/<repo>/<path:path>')
def view_project(owner, repo, path=''):
    if 'user' not in session:
        return redirect(url_for('dashboard'))

    repo_full_name = f"{owner}/{repo}"

    # --- Case 1: Live (Running on a worker) ---
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT w.service_url, j.viewer_port
            FROM jobs j
            JOIN workers w ON j.worker_id = w.worker_id
            WHERE j.repo = ? AND j.status = 'running'
            ORDER BY j.started_at DESC LIMIT 1
        ''', (repo_full_name,))
        job = cursor.fetchone()

    if job and job['service_url']:
        worker_base_url = job['service_url']
        # Use dynamic port if available, otherwise fallback to default
        viewer_port = job.get('viewer_port') or DVC_VIEWER_PORT
        # Extract hostname/IP from service_url (e.g., http://worker1:6000 -> worker1)
        parsed = urlparse(worker_base_url)
        target_host = parsed.hostname
        target_url = f"http://{target_host}:{viewer_port}/{path}"
        return proxy_request(target_url)

    # --- Case 2: Historical (Local) ---
    repo_path = os.path.join(REPOS_DIR, owner, repo)
    if not os.path.exists(repo_path):
        return f"Projet {repo_full_name} non trouvé localement et non actif.", 404

    with local_viewers_lock:
        if repo_full_name in local_viewers:
            viewer = local_viewers[repo_full_name]
            # Check if process is still alive
            if viewer['proc'].poll() is None:
                viewer['last_access'] = time.time()
                target_url = f"http://localhost:{viewer['port']}/{path}"
                return proxy_request(target_url)
            else:
                del local_viewers[repo_full_name]

        # Start a new dvc-viewer process
        port = get_free_port()
        try:
            # We assume dvc-viewer is available in the environment
            # and it supports a --port argument.
            # Using 'uv run' if possible or direct call
            cmd = ["uv", "run", "dvc-viewer", "serve", "--port", str(port)]
            # If dvc-viewer is not a uv project, might need just ["dvc-viewer", "serve", ...]
            # Given the context of the project, it's likely uv-managed or installed as a tool.
            proc = subprocess.Popen(cmd, cwd=repo_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # Wait a bit for the server to start
            time.sleep(2)

            local_viewers[repo_full_name] = {
                'proc': proc,
                'port': port,
                'last_access': time.time()
            }
            target_url = f"http://localhost:{port}/{path}"
            return proxy_request(target_url)
        except Exception as e:
            return f"Failed to start dvc-viewer: {str(e)}", 500

def proxy_request(target_url):
    """Simple proxy that forwards the request to the target_url."""
    try:
        resp = requests.request(
            method=request.method,
            url=target_url,
            headers={key: value for (key, value) in request.headers if key != 'Host'},
            data=request.get_data(),
            cookies=request.cookies,
            allow_redirects=False,
            params=request.args,
            stream=True
        )

        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        headers = [(name, value) for (name, value) in resp.raw.headers.items()
                   if name.lower() not in excluded_headers]

        response = Response(stream_with_context(resp.iter_content(chunk_size=1024)),
                            status=resp.status_code,
                            headers=headers)
        return response
    except Exception as e:
        return f"Proxy Error: {str(e)}", 502

if __name__ == '__main__':
    init_db()
    # Start cleanup thread
    threading.Thread(target=cleanup_inactive_viewers, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)
