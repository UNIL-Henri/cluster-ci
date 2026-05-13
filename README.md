# Cluster CI

L'orchestrateur GitOps minimaliste et décentralisé pour le traitement de données et l'entraînement de modèles.
**État actuel** : Système opérationnel. Le réseau de workers ARM64 NVIDIA Blackwell est fonctionnel avec le conteneur NGC `nvcr.io/nvidia/pytorch:26.04-py3` (Python 3.12, PyTorch 2.12, CUDA 13.2).

Asynchronous continuous integration system for research pipelines, designed as a pull-based replacement for the legacy SlurmRay push-based architecture. This repository hosts the scripts necessary to configure a GitHub Actions Self-Hosted Runner on the target Ubuntu machine, orchestrating `uv run dvc repro` executions in local environments and managing silent authentication with Google Drive. It also provides the client script allowing any research repository to interface with this cluster.

## Cluster Hardware Specifications

| Property | Value |
|----------|-------|
| **GPU** | NVIDIA GB10 (Blackwell architecture) |
| **CPU** | ARM64 — Cortex-X925 + Cortex-A725 (ARMv9) |
| **RAM** | 128 GB unified memory |
| **OS** | Ubuntu 24.04.4 LTS (Noble Numbat) |
| **Docker Image** | `nvcr.io/nvidia/pytorch:26.04-py3` |
| **Python** | 3.12 |
| **PyTorch** | 2.12 (CUDA 13.2) |
| **Storage** | ~3.2 TB |

## Installation

### 1. Client Installation (Projet de recherche)

Exécutez cette commande à la racine de votre dépôt Git pour l'intégration automatique :

```bash
curl -H 'Cache-Control: no-cache, no-store' -sSL "https://raw.githubusercontent.com/UNIL-DESI/cluster-ci/main/install.sh?v=$(date +%s)" | bash
```

Ce script injecte :
1. Le workflow Github Actions (`.github/workflows/cluster-ci.yml`)
2. Le fichier de contrôle DVC (`.cluster-ci`)
3. **Le fichier de directives pour agents (`AGENTS.md`)** contenant les contraintes d'architecture du cluster (Python 3.12, PyTorch 2.12, CUDA 13.2) afin d'éviter les erreurs de dépendances de l'IA sur ce dépôt.
4. **Le Scanner Pre-flight (Git Hook)** : Un hook de pre-commit interactif qui valide la compatibilité locale avec le cluster ARM64 et propose des corrections automatiques.

### Cluster Deployment (Headnode & Workers)

Installation is done via a "One-Liner" curl command that automatically configures the environment and systemd services.

#### 1. Install the Headnode (Scheduler)
The Headnode manages the job queue and ephemeral runners. The script will ask for your **GitHub PAT** and the target to monitor.
```bash
curl -H 'Cache-Control: no-cache, no-store' -sSL "https://raw.githubusercontent.com/UNIL-DESI/cluster-ci/main/install.sh?v=$(date +%s)" | bash -s -- headnode
```

#### 2. Install a Worker (Executor)
Once the Headnode is installed, it will provide a ready-to-use command to run on your Workers. Alternatively, you can start the installation manually:
```bash
curl -H 'Cache-Control: no-cache, no-store' -sSL "https://raw.githubusercontent.com/UNIL-DESI/cluster-ci/main/install.sh?v=$(date +%s)" | bash -s -- worker
```
The script will ask for the **Headnode URL** and the **Cluster Token** generated during Headnode installation.

#### Post-Installation Configuration
Once installed, you can add secrets (GCP, HuggingFace) to the `.env.secrets` file located in the installation folder (default `~/cluster-ci`).

To cleanly uninstall everything (systemd services, local cleanup):
```bash
cd ~/cluster-ci
./src/cluster/uninstall_runner.sh owner/repo
```

## Detailed Description

Cluster CI is based on GitOps principles. Instead of the agent trying to maintain a continuous interactive session on the remote machine (a structural issue with the Joules Agent on long research jobs), execution is delegated to a self-hosted GitHub Actions runner installed as a `systemd` service on the machine.

**Execution Flow**:
1. **Pull Request**: Joules (the coding agent) pushes changes to a GitHub PR.
2. **CI Trigger**: GitHub Actions hooks into the self-hosted runner.
3. **Orchestration**: The setup script switches to an untracked local cache directory (`repositories/$ORG/$REPO_NAME`), performs a `git fetch` and a forced `git checkout` of the branch (to keep DVC state intact across branches).
4. **Execution**: The orchestrator detects the `.cluster-ci` file, prepares the environment via `uv sync`, and runs `uv run dvc repro` with the provided arguments.
5. **Authentication**: The runner silently injects credentials (Google Drive) by sourcing the global cluster `.env` and `.env.secrets` files.
6. **CI Feedback**: Joules receives native failure and success notifications via GitHub PR integration.

## Main Results

- **Status**: Under construction (Last updated: 12 May 2026). The system replaces the legacy synchronous network approach with a robust asynchronous CI/CD loop.

## Documentation Index

| Title (Link) | Description |
|--------------|-------------|
| [Architecture Index](docs/index_architecture.md) | Architecture specifications and design notes |
| [Pre-flight Index](docs/index_preflight.md) | Validation scanner and pre-commit logic |

## Repository Layout

```text
cluster-ci/
├── docs/           # Documentation, Index, and Task Specifications
├── install.sh      # Client-side installation script
└── src/            # Runner and Orchestrator scripts
    ├── cluster/    # Local runner setup and management (systemd)
    ├── runner/     # GitOps Orchestrator (run_research_pipeline.sh)
    └── scheduler/  # Headnode API, Worker Agent, and Persistence (SQLite)
```

## Main Entry Scripts

| Command | Description |
|----------|-------------|
| `install.sh` | Injects the GitHub Actions workflow and `.cluster-ci` file into a client repository |
| `src/cluster/setup_runner.sh` | Installs and configures the GitHub Actions runner as a `systemd` service |
| `src/cluster/uninstall_runner.sh` | Completely uninstalls the runner (Systemd, GitHub, local) |

## Secondary Executable Scripts & Utilities

| Command | Description |
|----------|-------------|
| `src/scheduler/submit_job.py` | Client-side script (CLI) to manually submit a job to the Headnode |
| `src/scheduler/runner_manager.py` | Manages the lifecycle of ephemeral GitHub Actions runners (slot1, slot2) |
| `update_cluster.sh` | Updates the Headnode and Workers via SSH, uses an `.env` file to store credentials |

## Roadmap

**Phase 1 (Foundation — Completed)**
- [x] [Orchestrator Runner Setup](docs/tasks/setup_orchestrator.md)
- [x] [Local Deployment & Runner Test](docs/tasks/deploy_local_cluster.md)
- [x] [Silent DVC Authentication](docs/tasks/dvc_auth.md)
- [x] [Client Installation Script](docs/tasks/client_script.md)
- [x] [Per-Repository Concurrency Management](docs/tasks/concurrency_management.md)

**Phase 2 (Reliability & UX — In Progress)**
- [x] Automated Deployment (`update_cluster.sh`) with E2E tests
- [x] Standard build configuration (`pyproject.toml`)
- [x] GitHub OAuth support for the Dashboard (with reverse proxy and IPv4 fallback support)
- [x] Dashboard UX improvement (date formatting, DVC path corrections under systemd, historical DVC run fixes)
- [x] Migration to Docker Worker execution (NVIDIA/ARM support)
- [x] Real-time Log Streaming via Headnode
- [x] Propagation du jeton d'authentification (GH_TOKEN) de bout en bout en mode Délégation
- [x] Migration vers conteneur NGC moderne (Python 3.12, PyTorch 2.12, CUDA 13.2)
- [x] [Cluster-CI Pre-flight Scanner & Pre-commit Validator](https://github.com/UNIL-DESI/cluster-ci/issues/55)
- [x] [Auto-génération des contraintes ARM64 via CI](https://github.com/UNIL-DESI/cluster-ci/issues/56)
- [x] [Smart Environment Shims & Dynamic Client Sync](https://github.com/UNIL-DESI/cluster-ci/issues/57)
- [x] [Native GitHub Secrets Injection](https://github.com/UNIL-DESI/cluster-ci/issues/58)
- [x] [Isolation stricte des environnements Python et intégration GC](https://github.com/UNIL-DESI/cluster-ci/issues/59)
- [x] [Full Monitoring Dashboard & Real-time Logs](https://github.com/UNIL-DESI/cluster-ci/issues/60)
- [x] Smart Dependency Caching (hash-based skip of `uv pip install` when `pyproject.toml` unchanged)
- [x] Fix false-positive Exit Code -98 (Heartbeat/Worker crash detection race condition)
- [x] Résolution de l'échec du DVC P2P Pull (fichiers résiduels) dans le cache persistant
- [x] Résolution de l'erreur HTTP 404 du Live DVC Viewer derrière le reverse proxy (chemins relatifs & `<base href>`)
- [x] DVC Historical Extraction: Injection dynamique des identifiants (GITHUB_PAT) dans les miroirs Git locaux pour `dvc get`
- [x] Limitation de la prévisualisation des fichiers texte à 100 lignes dans le Dashboard pour optimisation UI
- [x] Restreindre le *Live Viewer* en mode "Lecture Seule" et corriger la détection des étapes DVC s'exécutant dans les wrappers Bash des workers.
- [x] [Robust Docker Container Lifecycle and Orphan Process Eradication](https://github.com/UNIL-DESI/cluster-ci/pull/66)
- [x] [Hybrid Liveness Watchdog — JIT Zombie Detection](https://github.com/UNIL-DESI/cluster-ci/pull/67)
- [x] Fix Scheduler assigning jobs to busy workers (single-threaded worker exclusion)
- [x] Inversion de l'ordre DVC/P2P (Pull avant le Hash) et suppression des erreurs de suppression Docker.
- [x] Segmented Pipeline Logs: Modal de logs interactif avec navigation par étape (Setup, DVC stages, Sync/GC), couleurs d'état, lazy loading progressif, indicateur de lignes, copie presse-papier, bouton d'erreur rapide, animation de chargement pour l'étape en cours, et emoji ☠️ avec raison pour les jobs tués
- [x] Fix Bug: `submit_job.py` lisait `.cluster-ci` depuis le CWD cluster-ci au lieu du repo cible → RAM toujours à 2GB en mode Delegation. Correction via shallow clone du `.cluster-ci` distant.
- [ ] [Implémenter un Global Execution Timeout pour empêcher le gel du worker sur un job bloqué](https://github.com/UNIL-DESI/cluster-ci/issues/63)
