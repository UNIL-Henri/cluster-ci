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

on:
  push:
    branches: [ main, master ]
  pull_request:
    branches: [ main, master ]
  workflow_dispatch:

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
        run: /usr/local/bin/cluster-ci-run "\${{ github.repository }}" "\${{ github.ref_name }}" "\${{ secrets.GITHUB_TOKEN }}"
EOF

# 3. Injection du fichier de configuration .cluster-ci
if [ ! -f ".cluster-ci" ]; then
    echo "📄 Création du fichier .cluster-ci..."
    cat <<EOF > .cluster-ci
# Paramètres d'exécution Cluster-CI
# Laissez ce fichier vide pour exécuter tout le pipeline 'dvc repro' par défaut.
# Pour exécuter des étapes spécifiques, listez-les ci-dessous (une par ligne ou séparées par des espaces).
# Exemple :
# step_train step_eval
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
