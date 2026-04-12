# Setup Orchestrateur Runner

## 1. Contexte & Discussion (Narratif)
> *Handover* : Remplacement de l'architecture "push" de SlurmRay par un modèle "pull" GitOps. Les agents avaient du mal à maintenir un contexte actif pendant de longues exécutions sur des jobs de recherche. L'idée est d'abandonner l'attente active pour un Self-Hosted Runner GitHub Actions en tant que service systemd sur notre machine Ubuntu.

L'objectif est d'implémenter le script d'orchestration `run_research_pipeline.sh` (ou équivalent) qui sera appelé par le runner à chaque événement CI. Ce script doit absolument gérer ses espaces de travail dans un dossier persistant absolu (`/data/research_workspaces/$REPO_NAME`) sur la machine pour garantir la réutilisation du cache `.dvc/cache` local, optimiser les téléchargements, et réaliser le `uv sync && uv run dvc repro`.

## 2. Fichiers Concernés
- `src/runner/run_research_pipeline.sh`
- (potentiellement un service template systemd si besoin de supervision avancée, mais le Runner de base GitHub l'intègre déjà)

## 3. Objectifs (Definition of Done)
- Un script bash de pipeline capable de recevoir le chemin du dépôt et/ou la branche cible de la PR depuis le runner.
- Le script doit orchestrer : 
  - La vérification/création du directory persistant `/data/research_workspaces/$REPO_NAME`.
  - Le git clone/pull sur la branche spécifique de manière propre et isolée.
  - La configuration et le lancement de l'exécution avec `uv sync` puis `uv run dvc repro`.
- La sortie standard et d'erreur doit être clairement repoussée vers l'output GitHub Actions pour que Joules puisse en analyser les traces.
