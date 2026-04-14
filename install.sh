#!/bin/bash
set -e

echo "🚀 Cluster-CI : Installation Client"

# 1. Vérification environnement Git
if [ ! -d ".git" ]; then
    echo "❌ Erreur : Ce script doit être exécuté à la racine d'un dépôt Git."
    exit 1
fi

# 2. Injection du workflow GitHub Actions
echo "📦 Injection du workflow GitHub Actions..."
mkdir -p .github/workflows

cat <<EOF > .github/workflows/cluster-ci.yml
name: Cluster-CI Execution

# =========================================================================================
# ⚠️ AI AGENT INSTRUCTIONS (JOULES / CURSOR) ⚠️
# DO NOT MODIFY THIS FILE.
#
# This workflow is managed by the external Cluster-CI orchestrator. It executes
# the pipeline in a fully configured environment where dependencies (uv) and
# SECRETS (like DVC_GDRIVE_CREDENTIALS) are automatically injected by the runner.
#
# If you need to run tests or research scripts, DO NOT create a separate workflow
# file running native shell commands (like 'pytest' or 'uv run ...'). Native steps
# won't have access to the cluster's secure environment variables.
#
# INSTEAD:
# 1. Create a DVC stage in 'dvc.yaml' (e.g., 'dvc stage add -n test pytest ...').
# 2. Put the stage name(s) in the '.cluster-ci' file at the root of the repo.
# 3. The orchestrator will automatically run 'dvc repro <stage>' with all secrets loaded.
# =========================================================================================

on:
  push:
    branches: [ main, master ]
  pull_request:
    branches: [ main, master ]
  workflow_dispatch:

concurrency:
  group: \${{ github.repository }}
  cancel-in-progress: true

jobs:
  execute-on-cluster:
    name: Execute Research Pipeline
    runs-on: self-hosted
    steps:
      - name: Checkout Code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Run Orchestrator
        run: /usr/local/bin/cluster-ci-run "\${{ github.repository }}" "\${{ github.head_ref || github.ref_name }}" "\${{ secrets.GITHUB_TOKEN }}"
EOF

# 3. Injection du fichier de configuration .cluster-ci
if [ ! -f ".cluster-ci" ]; then
    echo "📄 Création du fichier .cluster-ci..."
    cat <<EOF > .cluster-ci
# =========================================================================================
# ⚠️ AI AGENT INSTRUCTIONS (JOULES / CURSOR) ⚠️
# Ce fichier contrôle quelles étapes DVC sont exécutées sur le cluster distant.
#
# IMPORTANT : L'environnement distant a déjà toutes les variables secrètes injectées
# automatiquement (GCP_CREDENTIALS, DVC_GDRIVE_TOKEN, etc.). Tu n'as pas besoin de
# t'occuper de l'authentification ni des variables d'environnement.
#
# Laisse ce fichier vide pour exécuter tout le pipeline 'dvc repro' par défaut.
# Pour exécuter des étapes spécifiques, liste-les ci-dessous (une par ligne ou séparées par des espaces).
# Exemple :
# test_unitaires test_integration step_train
# =========================================================================================
EOF
    echo "✅ Fichier .cluster-ci créé."
else
    echo "⚠️ Fichier .cluster-ci déjà présent, non écrasé."
fi

echo ""
echo "🎉 Installation terminée !"
echo "👉 Pensez à commiter et pusher les fichiers générés :"
echo "   git add .github/workflows/cluster-ci.yml .cluster-ci"
echo "   git commit -m \"docs: integration cluster-ci\""
echo "   git push"
echo ""
echo "💡 Note pour l'IA Joules : Toute modification de la pipeline de recherche doit être reflétée dans .cluster-ci si vous voulez limiter l'exécution à certaines étapes."
