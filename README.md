# Cluster CI

Système d'intégration continue asynchrone pour pipeline de recherche, conçu comme un remplaçant "pull-based" à l'ancienne architecture "push-based" SlurmRay. Ce dépôt héberge les scripts nécessaires à la configuration du GitHub Actions Self-Hosted Runner sur la machine Ubuntu cible, orchestrant les exécutions de `uv run dvc repro` dans des environnements locaux et gérant l'authentification silencieuse à Google Drive. Il fournit également le script client permettant à tout dépôt de recherche de s'interfacer avec ce cluster.

## Installation

```bash
# Côté Agent-Sim / Projet Client
curl -sSL https://raw.githubusercontent.com/hjamet/cluster-ci/main/install.sh | bash
```

*(La configuration du Runner sur la machine Ubuntu se fera via les scripts de la roadmap ci-dessous. Le process précis sera documenté ultérieurement.)*

## Description détaillée

Cluster CI est fondé sur le principe de GitOps. Au lieu que l'agent cherche à maintenir une session interactive continue sur la machine distancée (problème structurel avec l'Agent Joules sur des jobs de recherche longs), on délègue l'exécution à un self-hosted runner GitHub Actions installé en tant que service `systemd` sur la machine.

**Flux d'exécution** :
1. **Pull Request** : Joules (l'agent codeur) pousse ses changements sur une PR GitHub.
2. **Déclenchement CI** : GitHub Actions accroche le self-hosted runner.
3. **Orchestration** : Le script de setup bascule dans un répertoire de cache persistant (`/data/research_workspaces/$REPO_NAME`), fait un `git pull` de la branche, et lance l'environnement (`uv sync`, puis `dvc repro`).
4. **Authentification** : Le runner injecte les credentials silencieusement, évitant l'interblocage OAuth2 interactif pour DVC via Google Drive.
5. **Feedbacks CI** : Joules reçoit les échecs et succès natifs via l'intégration GitHub PR, pouvant ajuster le code sans saturer sa mémoire contextuelle. L'agent superviseur (Eugène) monitore uniquement la complétion globale.

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
├── install.sh      # (À venir) Script curlable d'installation côté client 
└── src/            # (À venir) Scripts exécutables du Runner
```

## Scripts d'entrée principaux

| Commande | Description |
|----------|-------------|
| `install.sh` | Injecte le workflow d'exécution GitHub Actions dans un dépôt client |

## Scripts exécutables secondaires & Utilitaires

*(En construction)*

## Roadmap

- [ ] [Setup Orchestrateur Runner](docs/tasks/setup_orchestrator.md)
- [ ] [Authentification Silencieuse DVC](docs/tasks/dvc_auth.md)
- [ ] [Script d'Installation Client](docs/tasks/client_script.md)
