import os
import subprocess
import time
import requests
import logging
import threading
import sys
from pathlib import Path

# Configuration de logging
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
        """Récupère un nouveau token d'enregistrement via l'API GitHub."""
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

    def run_slot(self, slot_id):
        """Gère le cycle de vie d'un runner éphémère pour un slot donné."""
        slot_dir = self.runners_dir / f"slot{slot_id}"
        logger = logging.LoggerAdapter(logging.getLogger(__name__), {'slot': slot_id})

        while True:
            try:
                logger.info("Préparation d'un nouveau runner éphémère...")

                # 1. Obtenir un token
                token = self.get_registration_token()

                # 2. Configurer le runner
                runner_name = f"{self.runner_name_prefix}-slot{slot_id}"
                config_cmd = [
                    "./config.sh",
                    "--url", f"https://github.com/{self.target_repo}",
                    "--token", token,
                    "--unattended",
                    "--replace",
                    "--name", runner_name,
                    "--labels", "self-hosted,cluster-ci",
                    "--ephemeral"
                ]

                logger.info(f"Configuration du runner {runner_name}...")
                subprocess.run(config_cmd, cwd=slot_dir, check=True, capture_output=True)

                # 3. Lancer le runner
                logger.info("Lancement du runner...")
                # run.sh bloque jusqu'à ce que le job soit fini ou que le runner soit arrêté
                process = subprocess.Popen(["./run.sh"], cwd=slot_dir)
                process.wait()

                logger.info(f"Le runner s'est arrêté avec le code {process.returncode}. Redémarrage imminent...")

            except Exception as e:
                logger.error(f"Erreur dans le cycle du runner : {e}")
                time.sleep(10) # Attendre un peu avant de réessayer en cas d'erreur fatale

    def start(self):
        threads = []
        for i in range(1, self.num_slots + 1):
            t = threading.Thread(target=self.run_slot, args=(i,))
            t.daemon = True
            t.start()
            threads.append(t)

        logger = logging.LoggerAdapter(logging.getLogger(__name__), {'slot': 'MANAGER'})
        logger.info(f"Gestionnaire de runners démarré avec {self.num_slots} slots pour {self.target_repo}")

        # Maintenir le thread principal vivant
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Arrêt du gestionnaire...")

if __name__ == "__main__":
    target = os.environ.get("TARGET_REPO")
    pat = os.environ.get("GITHUB_PAT")

    if not target or not pat:
        print("Erreur : TARGET_REPO et GITHUB_PAT doivent être définis en variables d'environnement.")
        sys.exit(1)

    # On assume que le script est dans src/scheduler/
    script_dir = Path(__file__).resolve().parent
    base_dir = script_dir.parent.parent

    manager = RunnerManager(target, pat, base_dir)
    manager.start()
