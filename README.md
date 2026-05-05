# Cluster CI

Système d'intégration continue asynchrone pour pipeline de recherche, conçu comme un remplaçant "pull-based" à l'ancienne architecture "push-based" SlurmRay. Ce dépôt héberge les scripts nécessaires à la configuration du GitHub Actions Self-Hosted Runner sur la machine Ubuntu cible, orchestrant les exécutions de `uv run dvc repro` dans des environnements locaux et gérant l'authentification silencieuse à Google Drive. Il fournit également le script client permettant à tout dépôt de recherche de s'interfacer avec ce cluster.

## Installation

### Côté Client (Projet de recherche)
Pour intégrer un projet de recherche au cluster, exécutez la commande suivante à la racine de votre dépôt :
```bash
curl -sSL https://raw.githubusercontent.com/UNIL-DESI/cluster-ci/main/install.sh | bash
```

### Déploiement du Cluster (Headnode & Workers)

L'installation se fait via un "One-Liner" curl qui configure automatiquement l'environnement et les services systemd.

#### 1. Installer le Headnode (Ordonnanceur)
Le Headnode gère la file d'attente des jobs et les runners éphémères. Le script vous demandera votre **GitHub PAT** et la cible à surveiller.
```bash
curl -sSL https://raw.githubusercontent.com/UNIL-DESI/cluster-ci/main/install.sh | bash -s -- headnode
```

#### 2. Installer un Worker (Exécuteur)
Une fois le Headnode installé, il vous fournira une commande prête à l'emploi à exécuter sur vos Workers. Alternativement, vous pouvez lancer l'installation manuellement :
```bash
curl -sSL https://raw.githubusercontent.com/UNIL-DESI/cluster-ci/main/install.sh | bash -s -- worker
```
Le script vous demandera l'**URL du Headnode** et le **Token du Cluster** généré lors de l'installation du Headnode.

#### Configuration Post-Installation
Une fois installé, vous pouvez ajouter des secrets (GCP, HuggingFace) dans le fichier `.env.secrets` situé dans le dossier d'installation (par défaut `~/cluster-ci`).

Pour tout désinstaller proprement (services systemd, nettoyage local) :
```bash
cd ~/cluster-ci
./src/cluster/uninstall_runner.sh owner/repo
```

## Description détaillée

Cluster CI est fondé sur le principe de GitOps. Au lieu que l'agent cherche à maintenir une session interactive continue sur la machine distancée (problème structurel avec l'Agent Joules sur des jobs de recherche longs), on délègue l'exécution à un self-hosted runner GitHub Actions installé en tant que service `systemd` sur la machine.

**Flux d'exécution** :
1. **Pull Request** : Joules (l'agent codeur) pousse ses changements sur une PR GitHub.
2. **Déclenchement CI** : GitHub Actions accroche le self-hosted runner.
3. **Orchestration** : Le script de setup bascule dans un répertoire de cache local non-tracké (`repositories/$ORG/$REPO_NAME`), fait un `git fetch` et un `git checkout` forcé de la branche (pour conserver l'état DVC intact inter-branches).
4. **Exécution** : L'orchestrateur détecte le fichier `.cluster-ci`, prépare l'environnement via `uv sync` et lance `uv run dvc repro` avec les arguments fournis.
5. **Authentification** : Le runner injecte les credentials silencieusement (Google Drive) en sourçant les fichiers `.env` et `.env.secrets` globaux du cluster.
6. **Feedbacks CI** : Joules reçoit les échecs et succès natifs via l'intégration GitHub PR.

## Principaux résultats

- **Statut** : En construction. Le système remplace l'ancienne approche réseau synchrone par une boucle asynchrone robuste CI/CD.

## Documentation Index

| Titre (Lien) | Description |
|--------------|-------------|
| [Index Architecture](docs/index_architecture.md) | Spécifications et notes de conception de l'architecture |

## Plan du repo

```text
cluster-ci/
├── docs/           # Documentation, Index et Spécifications des tâches
├── install.sh      # Script d'installation côté client 
└── src/            # Scripts du Runner et de l'Orchestrateur
    ├── cluster/    # Setup et gestion du runner local (systemd)
    ├── runner/     # Orchestrateur GitOps (run_research_pipeline.sh)
    └── scheduler/  # Headnode API, Worker Agent et Persistence (SQLite)
```

## Scripts d'entrée principaux

| Commande | Description |
|----------|-------------|
| `install.sh` | Injecte le workflow GitHub Actions et le fichier `.cluster-ci` dans un dépôt client |
| `src/cluster/setup_runner.sh` | Installe et configure le runner GitHub Actions en tant que service `systemd` |
| `src/cluster/uninstall_runner.sh` | Désinstalle le runner complétement (Systemd, GitHub, local) |

## Scripts exécutables secondaires & Utilitaires

| Commande | Description |
|----------|-------------|
| `src/scheduler/submit_job.py` | Script client (CLI) pour soumettre manuellement un job au Headnode |
| `src/scheduler/runner_manager.py` | Gère le cycle de vie des runners GitHub Actions éphémères (slot1, slot2) |
| `update_cluster.sh` | Met à jour le Headnode et les Workers via SSH, utilise un fichier `.env` pour stocker les identifiants |

## Roadmap

**Phase 1 (Fondation — Complétée)**
- [x] [Setup Orchestrateur Runner](docs/tasks/setup_orchestrator.md)
- [x] [Déploiement Local & Test Runner](docs/tasks/deploy_local_cluster.md)
- [x] [Authentification Silencieuse DVC](docs/tasks/dvc_auth.md)
- [x] [Script d'Installation Client](docs/tasks/client_script.md)
- [x] [Gestion de la Concurrence par Dépôt](docs/tasks/concurrency_management.md)

**Phase 2 (Fiabilité & UX — En cours)**
- [ ] Refonte des tests unitaires & E2E (Pytest)
- [ ] Configuration de build standard (`pyproject.toml`)
- [ ] Log Streaming temps réel via le Headnode
- [ ] Monitoring complet et Healthcheck
