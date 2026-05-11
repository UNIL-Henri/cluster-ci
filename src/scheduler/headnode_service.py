import socket
# Force IPv4 to prevent infinite hangs on broken IPv6 networks (common on headless servers)
old_getaddrinfo = socket.getaddrinfo
def new_getaddrinfo(*args, **kwargs):
    responses = old_getaddrinfo(*args, **kwargs)
    return [response for response in responses if response[0] == socket.AF_INET]
socket.getaddrinfo = new_getaddrinfo
# Set a global timeout for all socket operations to prevent infinite hangs
socket.setdefaulttimeout(20.0)

from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context, session, url_for, redirect, render_template
from persistence import init_db, get_db_conn
from authlib.integrations.flask_client import OAuth
import uuid
import datetime
import os
import shutil
import requests
import subprocess
import sys
import time
import threading
import re
import json
import tempfile
from urllib.parse import urlparse
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

# Helper to find executables
def get_executable(name):
    """Finds an executable in system PATH, local bin, or current venv."""
    cmd = shutil.which(name)
    if cmd: return cmd
    # Fallback to local user installation
    local_path = os.path.expanduser(f"~/.local/bin/{name}")
    if os.path.exists(local_path): return local_path
    # Fallback to virtual environment
    venv_path = os.path.join(os.path.dirname(sys.executable), name)
    if os.path.exists(venv_path): return venv_path
    return name

DVC_CMD = get_executable("dvc")
UV_CMD = get_executable("uv")

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
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
    client_kwargs={'scope': 'repo,user:email', 'timeout': 10.0},
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
    total_storage_gb = data.get('total_storage_gb')
    available_storage_gb = data.get('available_storage_gb')

    with get_db_conn() as conn:
        cursor = conn.cursor()
        # On first registration, we initialize available_ram_gb to total_ram_gb
        cursor.execute('''
            INSERT INTO workers (worker_id, hostname, service_url, total_ram_gb, available_ram_gb, total_storage_gb, available_storage_gb, last_seen, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 'online')
            ON CONFLICT(worker_id) DO UPDATE SET
                hostname = ?,
                service_url = ?,
                total_ram_gb = ?,
                total_storage_gb = ?,
                available_storage_gb = ?,
                last_seen = CURRENT_TIMESTAMP,
                status = 'online'
        ''', (worker_id, hostname, service_url, total_ram_gb, total_ram_gb, total_storage_gb, available_storage_gb, hostname, service_url, total_ram_gb, total_storage_gb, available_storage_gb))
        
        # If a worker re-registers (is_startup=True), it means it restarted and lost any running jobs.
        # We must mark any 'running' or 'assigned' jobs for this worker as 'failed'.
        is_startup = data.get('is_startup', False)
        if is_startup:
            cursor.execute('''
                UPDATE jobs
                SET status = 'failed', exit_code = COALESCE(exit_code, -98)
                WHERE worker_id = ? AND status IN ('running', 'assigned')
            ''', (worker_id,))
        
        conn.commit()

    return jsonify({"status": "ok"})

@app.route('/submit_job', methods=['POST'])
def submit_job():
    data = request.json
    repo = data.get('repo')
    branch = data.get('branch')
    ram_required_gb = data.get('ram_required_gb', 0)
    gh_token = data.get('gh_token')
    env_vars = data.get('env_vars') # Dictionary of secrets
    job_id = str(uuid.uuid4())

    # Metadata extraction (Pre-flight check)
    required_hashes = []
    repo_url = f"https://github.com/{repo}.git"
    pat = os.environ.get("GITHUB_PAT")
    if pat:
        repo_url = f"https://x-access-token:{pat}@github.com/{repo}.git"

    tmp_dir = tempfile.mkdtemp()
    try:
        # Shallow clone to get dvc.lock
        subprocess.run(["git", "clone", "--depth", "1", "--branch", branch, "--no-checkout", repo_url, tmp_dir],
                       check=True, capture_output=True, timeout=30)
        # Checkout dvc.lock
        res = subprocess.run(["git", "checkout", "origin/" + branch, "--", "dvc.lock"],
                             cwd=tmp_dir, capture_output=True, timeout=10)
        if res.returncode == 0:
            lock_path = os.path.join(tmp_dir, "dvc.lock")
            if os.path.exists(lock_path):
                with open(lock_path, 'r') as f:
                    content = f.read()
                    # Extract MD5 hashes
                    required_hashes = list(set(re.findall(r'md5:\s*([a-f0-9]{32})', content)))
    except Exception as e:
        app.logger.error(f"Metadata extraction failed for {repo}@{branch}: {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO jobs (job_id, repo, branch, ram_required_gb, required_hashes, gh_token, env_vars, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        ''', (job_id, repo, branch, ram_required_gb, json.dumps(required_hashes), gh_token, json.dumps(env_vars) if env_vars else None))
        conn.commit()

    return jsonify({"job_id": job_id, "status": "pending", "required_hashes_count": len(required_hashes)})

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

def find_local_repo(repo_slug):
    """Find the local clone of a repo, handling owner name mismatches.
    
    The DB might store 'UNIL-DESI/llm-as-recommender' but the clone could
    be under 'UNIL-Henri/llm-as-recommender'. We check exact match first,
    then search by repo name across all owner directories.
    """
    # Try exact match first
    exact = os.path.join(REPOS_DIR, repo_slug)
    if os.path.exists(exact):
        return exact
    
    # Fallback: search by repo name only across all owner dirs
    repo_name = repo_slug.split('/')[-1] if '/' in repo_slug else repo_slug
    if os.path.exists(REPOS_DIR):
        for owner_dir in os.listdir(REPOS_DIR):
            if owner_dir.startswith('_'):  # Skip _tmp_artifacts etc.
                continue
            candidate = os.path.join(REPOS_DIR, owner_dir, repo_name)
            if os.path.isdir(candidate):
                return candidate
    
    return None

@app.route('/artifacts/<repo_owner>/<repo_name>/<rev>/<path:file_path>', methods=['GET'])
def artifacts(repo_owner, repo_name, rev, file_path):
    """
    Unified artifact access API. Extracts files from local cache or remote GitHub
    using DVC at a specific revision (commit hash or branch).
    """
    repo_slug = f"{repo_owner}/{repo_name}"
    repo_url = f"https://github.com/{repo_slug}"

    # --- Case 1: REAL DVC EXTRACTION (Historical Integrity) ---
    # We extract the file exactly as it was at the given revision
    request_id = str(uuid.uuid4())
    tmp_dir = os.path.join(REPOS_DIR, "_tmp_artifacts", request_id)
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        local_repo_path = find_local_repo(repo_slug)
        source = local_repo_path if local_repo_path else repo_url
        cmd = [DVC_CMD, "get", source, file_path, "--rev", rev, "--out", tmp_dir]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            filename = os.path.basename(file_path)
            full_path = os.path.join(tmp_dir, filename)

            def generate():
                try:
                    with open(full_path, 'rb') as f:
                        while True:
                            chunk = f.read(4096)
                            if not chunk:
                                break
                            yield chunk
                finally:
                    # Robust cleanup: delete the whole tmp directory for this request
                    shutil.rmtree(tmp_dir, ignore_errors=True)

            return Response(generate(), mimetype='application/octet-stream',
                            headers={"Content-Disposition": f"attachment; filename={filename}"})

        # If DVC get failed, we DO NOT fallback to worker for historical revisions.
        # We consider any request with a 'rev' as a historical request needing integrity.
        return jsonify({"error": f"Failed to extract historical artifact: {result.stderr}"}), 404

    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": f"Internal error during extraction: {str(e)}"}), 500

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
        repos_resp = oauth.github.get('user/repos?per_page=100&sort=updated', token=token, timeout=15.0)
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
                allowed_repos.add(r['full_name'].lower())

        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT DISTINCT repo FROM jobs')
            projects_in_db = [row['repo'] for row in cursor.fetchall()]

        # Only return projects that are in the database AND the user has access to (case-insensitive)
        projects = [p for p in projects_in_db if p.lower() in allowed_repos]
        return jsonify(projects)
    except Exception as e:
        app.logger.error(f"Error fetching repos in API: {e}")
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

    local_repo_path = find_local_repo(repo)
    
    if local_repo_path:
        hashes = [run['commit_hash'] for run in runs if run.get('commit_hash')]
        if hashes:
            try:
                res = subprocess.run(
                    ["git", "--no-pager", "show", "-s", "--format=%H|%s"] + hashes,
                    cwd=local_repo_path,
                    capture_output=True,
                    text=True
                )
                title_map = {}
                for line in res.stdout.strip().split('\n'):
                    if '|' in line:
                        h, t = line.split('|', 1)
                        title_map[h] = t
                for run in runs:
                    run['commit_title'] = title_map.get(run.get('commit_hash'), "")
            except Exception:
                for run in runs: run['commit_title'] = ""
    else:
        for run in runs: run['commit_title'] = ""

    return jsonify(runs)
    
@app.route('/api/jobs/<job_id>/logs', methods=['GET'])
def api_get_run_logs(job_id):
    offset = request.args.get('offset', 0)
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT w.service_url
            FROM jobs j
            JOIN workers w ON j.worker_id = w.worker_id
            WHERE j.job_id = ?
        ''', (job_id,))
        job = cursor.fetchone()
        
    if not job or not job['service_url']:
        return jsonify({"logs": "Log source not found (worker might be offline or job not assigned)", "offset": offset})
        
    worker_url = f"{job['service_url']}/job_logs/{job_id}?offset={offset}"
    try:
        resp = requests.get(worker_url, timeout=5)
        if resp.status_code == 200:
            return jsonify(resp.json())
        else:
            return jsonify({"logs": f"Error fetching logs from worker: {resp.text}", "offset": offset}), 500
    except Exception as e:
        return jsonify({"logs": f"Connection error to worker: {str(e)}", "offset": offset}), 500

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
        commit_hash = job['branch']

    pat = os.environ.get("GITHUB_PAT")
    repo_url = f"https://x-access-token:{pat}@github.com/{repo}.git" if pat else f"https://github.com/{repo}.git"

    local_repo_path = find_local_repo(repo)
    source = local_repo_path if local_repo_path else repo_url

    try:
        env = os.environ.copy()

        # Support subfolder navigation via optional 'path' query parameter
        sub_path = request.args.get('path', '')
        cmd = [DVC_CMD, "list", source, "--rev", commit_hash, "--dvc-only", "--json"]
        if sub_path:
            cmd = [DVC_CMD, "list", source, sub_path, "--rev", commit_hash, "--dvc-only", "--json"]
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

@app.route('/api/runs/active', methods=['GET'])
def api_active_runs():
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM jobs 
                WHERE status IN ('running', 'assigned')
                ORDER BY created_at DESC
            ''')
            runs = [dict(row) for row in cursor.fetchall()]
        return jsonify(runs)
    except Exception as e:
        app.logger.error(f"Error fetching active runs: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/')
def dashboard():
    if 'user' not in session:
        return render_template('login.html')

    return render_template('dashboard.html', user=session['user'])

@app.route('/login')
def login():
    redirect_uri = url_for('authorize', _external=True)
    print(f"DEBUG: Redirecting to GitHub. redirect_uri={redirect_uri}", flush=True)
    return oauth.github.authorize_redirect(redirect_uri)

@app.route('/authorize')
def authorize():
    print(f"DEBUG: /authorize reached. Args: {request.args}", flush=True)
    try:
        print("DEBUG: Fetching access token...", flush=True)
        token = oauth.github.authorize_access_token()
        print(f"DEBUG: Token received. Fetching user info...", flush=True)
        resp = oauth.github.get('user', token=token)
        user = resp.json()
        print(f"DEBUG: User info received: {user.get('login')}. Setting session...", flush=True)
        session['user'] = user
        session['token'] = token
        print("DEBUG: Redirecting to dashboard.", flush=True)
        return redirect(url_for('dashboard'))
    except Exception as e:
        print(f"DEBUG ERROR in /authorize: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        return f"Authentication Error: {str(e)}", 500

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
        return redirect(url_for('dashboard'), code=302)

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
        viewer_port = job['viewer_port'] if ('viewer_port' in job.keys() and job['viewer_port'] is not None) else DVC_VIEWER_PORT
        # Extract hostname/IP from service_url (e.g., http://worker1:6000 -> worker1)
        parsed = urlparse(worker_base_url)
        target_host = parsed.hostname
        target_url = f"http://{target_host}:{viewer_port}/{path}"
        return proxy_request(target_url)

    # --- Case 2: Historical (Local) ---
    repo_path = find_local_repo(repo_full_name)
    if not repo_path:
        return f"Project {repo_full_name} not found locally and not active.", 404

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
            cmd = [UV_CMD, "run", "dvc-viewer", "serve", "--port", str(port)]
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
