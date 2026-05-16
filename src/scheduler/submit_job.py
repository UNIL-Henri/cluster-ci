import requests
import os
import sys
import time
import argparse
import signal

def get_ram_requirement(repo=None, branch=None):
    """
    Reads RAM requirement from the .cluster-ci file.
    First tries to fetch the file from the remote repo (shallow clone),
    then falls back to reading from the current working directory.
    Expected format in .cluster-ci: --ram 16 or REQUIRED_RAM=16GB
    """
    content = None

    # Strategy 1: Fetch .cluster-ci from the remote repo
    if repo and branch:
        import tempfile, subprocess
        tmp_dir = tempfile.mkdtemp()
        try:
            gh_token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_PAT")
            if gh_token:
                repo_url = f"https://x-access-token:{gh_token}@github.com/{repo}.git"
            else:
                repo_url = f"https://github.com/{repo}.git"
            subprocess.run(["git", "clone", "--depth", "1", "--branch", branch, "--no-checkout", repo_url, tmp_dir],
                           check=True, capture_output=True, timeout=30)
            subprocess.run(["git", "checkout", f"origin/{branch}", "--", ".cluster-ci"],
                           cwd=tmp_dir, check=True, capture_output=True, timeout=10)
            ci_file = os.path.join(tmp_dir, ".cluster-ci")
            if os.path.exists(ci_file):
                with open(ci_file, 'r') as f:
                    content = f.read()
        except Exception as e:
            print(f"⚠️ Could not fetch .cluster-ci from {repo}@{branch}: {e}")
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # Strategy 2: Fallback to local CWD
    if content is None:
        if os.path.exists(".cluster-ci"):
            with open(".cluster-ci", 'r') as f:
                content = f.read()
        else:
            return 2.0  # Default 2GB

    import re
    # Try REQUIRED_RAM=16GB or REQUIRED_RAM=16.5
    match_env = re.search(r'REQUIRED_RAM\s*=\s*(\d+(?:\.\d+)?)(?:GB|G)?', content)
    if match_env:
        return float(match_env.group(1))

    # Try --ram 16
    match = re.search(r'--ram\s+(\d+(?:\.\d+)?)', content)
    if match:
        return float(match.group(1))
    return 2.0  # Default

def get_config_value(pattern, content, default=None, is_float=False):
    import re
    match = re.search(pattern, content)
    if match:
        val = match.group(1)
        return float(val) if is_float else val
    return default

def submit_job(headnode_url, repo, branch, gh_token=None, env_vars=None):
    """Submits a research job to the headnode scheduler."""
    # Strategy: Fetch .cluster-ci content first to parse all requirements
    content = None
    import tempfile, subprocess, shutil, os
    tmp_dir = tempfile.mkdtemp()
    try:
        gh_token_inner = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_PAT")
        if gh_token_inner:
            repo_url = f"https://x-access-token:{gh_token_inner}@github.com/{repo}.git"
        else:
            repo_url = f"https://github.com/{repo}.git"
        subprocess.run(["git", "clone", "--depth", "1", "--branch", branch, "--no-checkout", repo_url, tmp_dir],
                       check=True, capture_output=True, timeout=30)
        subprocess.run(["git", "checkout", f"origin/{branch}", "--", ".cluster-ci"],
                       cwd=tmp_dir, check=True, capture_output=True, timeout=10)
        ci_file = os.path.join(tmp_dir, ".cluster-ci")
        if os.path.exists(ci_file):
            with open(ci_file, 'r') as f:
                content = f.read()
    except Exception as e:
        print(f"⚠️ Could not fetch .cluster-ci from {repo}@{branch}: {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if content is None and os.path.exists(".cluster-ci"):
        with open(".cluster-ci", 'r') as f:
            content = f.read()

    if content is None:
        content = ""

    # Parse RAM
    import re
    ram_req = 2.0
    match_env = re.search(r'REQUIRED_RAM\s*=\s*(\d+(?:\.\d+)?)(?:GB|G)?', content)
    if match_env:
        ram_req = float(match_env.group(1))
    else:
        match_ram = re.search(r'--ram\s+(\d+(?:\.\d+)?)', content)
        if match_ram:
            ram_req = float(match_ram.group(1))

    # Parse MAX_RUNTIME_HOURS (Fail-Fast)
    runtime_match = re.search(r'MAX_RUNTIME_HOURS\s*=\s*(\d+(?:\.\d+)?)', content)
    if not runtime_match:
        print("❌ Error: MAX_RUNTIME_HOURS is missing in .cluster-ci. This parameter is mandatory (max 24h).")
        sys.exit(1)

    max_runtime = float(runtime_match.group(1))
    if max_runtime <= 0 or max_runtime > 24:
        print(f"❌ Error: MAX_RUNTIME_HOURS must be between 0 and 24 hours (found: {max_runtime}).")
        sys.exit(1)

    # Parse EXPOSED_PORT
    exposed_port = None
    port_match = re.search(r'EXPOSED_PORT\s*=\s*(\d+)', content)
    if port_match:
        exposed_port = int(port_match.group(1))

    # Parse CUSTOM_WEB_APP
    custom_web_app = False
    custom_app_match = re.search(r'CUSTOM_WEB_APP\s*=\s*(true|1)', content, re.IGNORECASE)
    if custom_app_match:
        custom_web_app = True

    print(f"🚀 Submitting job for {repo}@{branch} (RAM: {ram_req}GB, Timeout: {max_runtime}h, Custom App: {custom_web_app})")

    token = os.environ.get("CLUSTER_TOKEN")
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.post(f"{headnode_url}/submit_job", json={
            "repo": repo,
            "branch": branch,
            "ram_required_gb": ram_req,
            "max_runtime_hours": max_runtime,
            "exposed_port": exposed_port,
            "custom_web_app": custom_web_app,
            "gh_run_id": os.environ.get("GITHUB_RUN_ID"),
            "gh_token": gh_token,
            "env_vars": env_vars,
            "username": os.environ.get("GITHUB_ACTOR", "unknown")
        }, headers=headers)
        resp.raise_for_status()
        job_data = resp.json()
        job_id = job_data['job_id']
        print(f"✅ Job submitted successfully! ID: {job_id}")
        return job_id
    except Exception as e:
        print(f"❌ Failed to submit job: {e}")
        sys.exit(1)

def wait_for_job(headnode_url, job_id):
    """Polls the headnode for job status and streams logs from the worker."""
    print(f"⏳ Waiting for job {job_id} to complete...")

    def signal_handler(sig, frame):
        print(f"\n🛑 Signal received ({signal.Signals(sig).name}). Propagating cancellation...")
        try:
            resp = requests.get(f"{headnode_url}/job_status/{job_id}")
            resp.raise_for_status()
            job = resp.json()
            worker_url = job.get('worker_service_url')

            if worker_url:
                print(f"📡 Sending cancellation to worker: {worker_url}")
                requests.post(f"{worker_url}/cancel/{job_id}", timeout=10)
                print("✅ Cancellation signal sent.")
            else:
                print("⚠️ Job was not yet assigned to a worker or worker info missing.")

            # Mark job as failed/cancelled on headnode
            token = os.environ.get("CLUSTER_TOKEN")
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            requests.post(f"{headnode_url}/update_job_status", json={
                "job_id": job_id,
                "status": "failed",
                "exit_code": -signal.SIGTERM
            }, headers=headers)

        except Exception as e:
            print(f"⚠️ Error during cancellation: {e}")
        sys.exit(128 + sig)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    log_offset = 0
    status_printed = False
    oom_detected = False

    while True:
        try:
            resp = requests.get(f"{headnode_url}/job_status/{job_id}")
            resp.raise_for_status()
            job = resp.json()
            status = job['status']
            worker_url = job.get('worker_service_url')

            if worker_url:
                try:
                    logs_resp = requests.get(f"{worker_url}/job_logs/{job_id}?offset={log_offset}", timeout=5)
                    if logs_resp.status_code == 200:
                        logs_data = logs_resp.json()
                        new_logs = logs_data.get('logs', '')
                        if new_logs:
                            import re
                            if re.search(r'Exit code 137|OOM|Out of Memory|exited with -9', new_logs, re.IGNORECASE):
                                oom_detected = True
                            if not status_printed:
                                print(f"\n\n[Streaming logs from {worker_url}]")
                                status_printed = True
                            sys.stdout.write(new_logs)
                            sys.stdout.flush()
                            log_offset = logs_data.get('offset', log_offset)
                except Exception as e:
                    pass

            if status == 'completed':
                print(f"\n✅ Job {job_id} completed successfully!")
                return 0
            elif status == 'failed':
                exit_code = job.get('exit_code')
                if exit_code is None or exit_code == 0:
                    exit_code = 1  # Ensure non-zero exit on failure

                # Infrastructure-level failure messages
                if exit_code == -99:
                    print(f"\n❌ Job {job_id} failed: Worker became unreachable (timeout/offline). The job was orphaned.")
                elif exit_code == -98:
                    print(f"\n❌ Job {job_id} failed: Worker restarted while the job was running/assigned.")
                elif exit_code == 137 or oom_detected:
                    print(f"\n❌ Job {job_id} failed: Out of Memory (OOM Kill detected).")
                else:
                    print(f"\n❌ Job {job_id} failed with exit code {exit_code}")
                return exit_code

            if not status_printed:
                sys.stdout.write(f"\rStatus: {status}... ")
                sys.stdout.flush()

        except Exception as e:
            print(f"\n⚠️ Error checking status: {e}")

        time.sleep(2)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Submit a job to Cluster-CI Scheduler")
    parser.add_argument("repo", help="Target repository (owner/repo)")
    parser.add_argument("branch", help="Target branch")
    parser.add_argument("--headnode", default=os.environ.get("HEADNODE_URL", "http://localhost:5000"), help="Headnode URL")
    parser.add_argument("--gh-token", default=None, help="GitHub token for cloning private repos")
    parser.add_argument("-e", "--env", action="append", help="Environment variables to pass (KEY=VALUE)", default=[])

    args = parser.parse_args()

    env_vars = {}
    
    # Process explicit -e flags
    for e in args.env:
        if "=" in e:
            k, v = e.split("=", 1)
            env_vars[k] = v

    # Process automatic GitHub Secrets injection
    all_secrets_json = os.environ.get("ALL_GITHUB_SECRETS")
    if all_secrets_json:
        try:
            import json
            secrets_dict = json.loads(all_secrets_json)
            for k, v in secrets_dict.items():
                if k.lower() != 'github_token':
                    env_vars[k] = v
        except Exception as e:
            print(f"⚠️ Failed to parse ALL_GITHUB_SECRETS: {e}")

    job_id = submit_job(args.headnode, args.repo, args.branch, args.gh_token, env_vars)
    exit_code = wait_for_job(args.headnode, job_id)
    sys.exit(exit_code)
