# Déploiement et Test du Cluster Local

## 1. Contexte & Discussion (Narratif)
> *Handover* : Pour valider le bon fonctionnement de Cluster-CI, nous avons décidé de transformer la machine de développement actuelle en "Cluster" cible. Cela permet de tester toute la chaîne en conditions réelles (Push sur Github -> CI Trigger -> Runner Local -> Orchestrateur -> `dvc repro`).

L'objectif est d'installer effectivement un Self-Hosted Runner GitHub Actions sur cette machine, rattaché soit au dépôt `cluster-ci` ou à l'organisation complète, et de valider que la boucle locale s'exécute correctement sans conflits.

## 2. Fichiers Concernés
- `src/cluster/install_runner.sh` (Nouveau script spécifique à l'hôte)
- `.github/workflows/test_runner.yml` (CI de validation interne pour `cluster-ci`)

## 3. Objectifs (Definition of Done)
- Un script capable de télécharger et de builder le Github Actions Runner en local.
- L'installation inclut la vérification/installation de `uv`.
- Configurer le runner en mode service système pour écouter les jobs avec le tag self-hosted.
- Lancer un `git push` déclenchant une CI de test basique via un workflow `.github/workflows/test_runner.yml`, s'assurant que le runner local répond et exécute correctement le script d'orchestration dans le dossier `workspaces/`.
