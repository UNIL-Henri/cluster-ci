import requests
import os
import sys
import time
import argparse

def get_ram_requirement():
    """
    Reads RAM requirement from .cluster-ci file.
    Format expected in .cluster-ci: --ram 16
    """
    if not os.path.exists(".cluster-ci"):
        return 2.0 # Default 2GB

    with open(".cluster-ci", 'r') as f:
        content = f.read()
        import re
        match = re.search(r'--ram\s+(\d+(?:\.\d+)?)', content)
        if match:
            return float(match.group(1))
    return 2.0 # Default

def submit_job(headnode_url, repo, branch):
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
            "ram_required_gb": ram_req
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
    print(f"⏳ Waiting for job {job_id} to complete...")
    while True:
        try:
            resp = requests.get(f"{headnode_url}/job_status/{job_id}")
            resp.raise_for_status()
            job = resp.json()
            status = job['status']

            if status == 'completed':
                print(f"✅ Job {job_id} completed successfully!")
                return 0
            elif status == 'failed':
                print(f"❌ Job {job_id} failed with exit code {job.get('exit_code')}")
                return job.get('exit_code', 1)
            elif status == 'running':
                # Optional: could stream logs if we had a log service
                pass

            sys.stdout.write(f"\rStatus: {status}... ")
            sys.stdout.flush()

        except Exception as e:
            print(f"\n⚠️ Error checking status: {e}")

        time.sleep(10)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Submit a job to Cluster-CI Scheduler")
    parser.add_argument("repo", help="Target repository (owner/repo)")
    parser.add_argument("branch", help="Target branch")
    parser.add_argument("--headnode", default=os.environ.get("HEADNODE_URL", "http://localhost:5000"), help="Headnode URL")

    args = parser.parse_args()

    job_id = submit_job(args.headnode, args.repo, args.branch)
    exit_code = wait_for_job(args.headnode, job_id)
    sys.exit(exit_code)
