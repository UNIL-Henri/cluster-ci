# Cluster CI

L'orchestrateur GitOps minimaliste et décentralisé pour le traitement de données et l'entraînement de modèles.
**État actuel** : Système opérationnel. Le réseau de workers hybrides (x86_64 et ARM64) est fonctionnel. L'orchestration ARM garantit des performances GPU/TensorRT optimales sur L4T via une stratégie d'héritage natif `python3 -m pip install --user`.

Asynchronous continuous integration system for research pipelines, designed as a pull-based replacement for the legacy SlurmRay push-based architecture. This repository hosts the scripts necessary to configure a GitHub Actions Self-Hosted Runner on the target Ubuntu machine, orchestrating `uv run dvc repro` executions in local environments and managing silent authentication with Google Drive. It also provides the client script allowing any research repository to interface with this cluster.

## Installation

### Client-Side (Research Project)
To integrate a research project with the cluster, execute the following command at the root of your repository:
```bash
curl -sSL https://raw.githubusercontent.com/UNIL-DESI/cluster-ci/main/install.sh | bash
```

### Cluster Deployment (Headnode & Workers)

Installation is done via a "One-Liner" curl command that automatically configures the environment and systemd services.

#### 1. Install the Headnode (Scheduler)
The Headnode manages the job queue and ephemeral runners. The script will ask for your **GitHub PAT** and the target to monitor.
```bash
curl -sSL https://raw.githubusercontent.com/UNIL-DESI/cluster-ci/main/install.sh | bash -s -- headnode
```

#### 2. Install a Worker (Executor)
Once the Headnode is installed, it will provide a ready-to-use command to run on your Workers. Alternatively, you can start the installation manually:
```bash
curl -sSL https://raw.githubusercontent.com/UNIL-DESI/cluster-ci/main/install.sh | bash -s -- worker
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

- **Status**: Under construction. The system replaces the legacy synchronous network approach with a robust asynchronous CI/CD loop.

## Documentation Index

| Title (Link) | Description |
|--------------|-------------|
| [Architecture Index](docs/index_architecture.md) | Architecture specifications and design notes |

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
- [x] Dashboard UX improvement (date formatting, DVC path corrections under systemd)
- [x] Migration to Docker Worker execution (NVIDIA/ARM support)
- [x] Real-time Log Streaming via Headnode
- [x] Propagation du jeton d'authentification (GH_TOKEN) de bout en bout en mode Délégation
- [ ] Full Monitoring and Healthcheck
