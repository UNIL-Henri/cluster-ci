import requests
import os
import sys
import time
import argparse
import signal

def get_ram_requirement():
    """
    Reads RAM requirement from the .cluster-ci file.
    Expected format in .cluster-ci: --ram 16 or REQUIRED_RAM=16GB
    """
    if not os.path.exists(".cluster-ci"):
        return 2.0  # Default 2GB

    with open(".cluster-ci", 'r') as f:
        content = f.read()
        import re
        
        # Try REQUIRED_RAM=16GB or REQUIRED_RAM=16.5
        match_env = re.search(r'REQUIRED_RAM\s*=\s*(\d+(?:\.\d+)?)(?:GB|G)?', content)
        if match_env:
            return float(match_env.group(1))
            
        # Try --ram 16
        match = re.search(r'--ram\s+(\d+(?:\.\d+)?)', content)
        if match:
            return float(match.group(1))
    return 2.0 # Default

def submit_job(headnode_url, repo, branch, gh_token=None, env_vars=None):
    """Submits a research job to the headnode scheduler."""
    ram_req = get_ram_requirement()
    print(f"🚀 Submitting job for {repo}@{branch} (Required RAM: {ram_req}GB)")

    token = os.environ.get("CLUSTER_TOKEN")
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.post(f"{headnode_url}/submit_job", json={
            "repo": repo,
            "branch": branch,
            "ram_required_gb": ram_req,
            "gh_token": gh_token,
            "env_vars": env_vars
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
                elif exit_code == 137:
                    print(f"\n❌ Job {job_id} failed: Out of Memory (OOM killed by Docker).")
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
                    # Provide fallback for HF_TOKEN
                    if k.upper() in ["HUGGINGFACE_TOKEN", "HUGGING_FACE_TOKEN", "HUGGING_FACE_HUB_TOKEN"] and "HF_TOKEN" not in env_vars:
                        env_vars["HF_TOKEN"] = v
        except Exception as e:
            print(f"⚠️ Failed to parse ALL_GITHUB_SECRETS: {e}")

    job_id = submit_job(args.headnode, args.repo, args.branch, args.gh_token, env_vars)
    exit_code = wait_for_job(args.headnode, job_id)
    sys.exit(exit_code)
