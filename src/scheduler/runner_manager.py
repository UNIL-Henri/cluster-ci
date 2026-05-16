import os
import subprocess
import time
import requests
import logging
import threading
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] slot=%(slot)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

class RunnerManager:
    def __init__(self, target_repo, github_pat, base_dir, num_slots=2):
        self.target_repo = target_repo
        self.github_pat = github_pat
        self.base_dir = Path(base_dir)
        self.runners_dir = self.base_dir / "runners"
        self.num_slots = num_slots
        self.runner_name_prefix = f"cluster-local-{target_repo.replace('/', '-')}"

    def get_registration_token(self):
        """Retrieves a new registration token via the GitHub API."""
        if "/" in self.target_repo:
            api_url = f"https://api.github.com/repos/{self.target_repo}/actions/runners/registration-token"
        else:
            api_url = f"https://api.github.com/orgs/{self.target_repo}/actions/runners/registration-token"

        response = requests.post(
            api_url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.github_pat}",
                "X-GitHub-Api-Version": "2022-11-28"
            }
        )
        response.raise_for_status()
        return response.json()["token"]

    def run_slot(self, slot_id, labels="self-hosted,cluster-ci"):
        """Manages the lifecycle of an ephemeral runner for a given slot."""
        if slot_id == "admin":
            slot_dir = self.runners_dir / "admin"
        else:
            slot_dir = self.runners_dir / f"slot{slot_id}"
        logger = logging.LoggerAdapter(logging.getLogger(__name__), {'slot': slot_id})

        while True:
            try:
                logger.info(f"Preparing a new ephemeral runner (labels: {labels})...")

                # 1. Obtain a token
                token = self.get_registration_token()

                # 2. Configure the runner
                runner_name = f"{self.runner_name_prefix}-slot{slot_id}"
                config_cmd = [
                    "./config.sh",
                    "--url", f"https://github.com/{self.target_repo}",
                    "--token", token,
                    "--unattended",
                    "--replace",
                    "--name", runner_name,
                    "--labels", labels,
                    "--ephemeral"
                ]

                logger.info(f"Configuring runner {runner_name}...")
                subprocess.run(config_cmd, cwd=slot_dir, check=True, capture_output=True)

                # 3. Launch the runner
                logger.info("Launching runner...")
                # run.sh blocks until the job is finished or the runner is stopped
                process = subprocess.Popen(["./run.sh"], cwd=slot_dir)
                process.wait()

                logger.info(f"Runner stopped with code {process.returncode}. Imminent restart...")

            except Exception as e:
                logger.error(f"Error in runner cycle: {e}")
                time.sleep(10) # Wait a bit before retrying in case of fatal error

    def start(self):
        threads = []
        # Standard slots
        for i in range(1, self.num_slots + 1):
            t = threading.Thread(target=self.run_slot, args=(i,))
            t.daemon = True
            t.start()
            threads.append(t)

        # Exclusive Admin slot
        t_admin = threading.Thread(target=self.run_slot, args=("admin", "self-hosted,cluster-ci-admin"))
        t_admin.daemon = True
        t_admin.start()
        threads.append(t_admin)

        logger = logging.LoggerAdapter(logging.getLogger(__name__), {'slot': 'MANAGER'})
        logger.info(f"Runner manager started with {self.num_slots} slots + 1 admin slot for {self.target_repo}")

        # Keep main thread alive
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Stopping manager...")

if __name__ == "__main__":
    target = os.environ.get("TARGET_REPO")
    pat = os.environ.get("GITHUB_PAT")

    if not target or not pat:
        print("Error: TARGET_REPO and GITHUB_PAT must be defined as environment variables.")
        sys.exit(1)

    # On assume que le script est dans src/scheduler/
    script_dir = Path(__file__).resolve().parent
    base_dir = script_dir.parent.parent

    manager = RunnerManager(target, pat, base_dir)
    manager.start()
