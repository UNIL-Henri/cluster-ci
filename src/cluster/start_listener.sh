#!/bin/bash

echo "=========================================="
echo "🚀 CLUSTER-CI : Démarrage du Runner"
echo "=========================================="

echo "🛑 1/2 Nettoyage des anciennes instances..."
pkill -SIGINT -f "Runner.Listener" || true
# On attend un instant pour le graceful shutdown
sleep 2
pkill -9 -f "Runner.Listener" || true
pkill -9 -f "Runner.Worker" || true
pkill -9 -f "run_research_pipeline" || true
sleep 1

# Trouver le chemin de notre runner
BASE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/../.." >/dev/null 2>&1 && pwd )"
RUNNER_DIR="$BASE_DIR/runners/hjamet-cluster-ci"

if [ ! -d "$RUNNER_DIR" ]; then
    echo "❌ Erreur : Le runner n'existe pas dans $RUNNER_DIR"
    echo "Avez-vous bien lancé setup_runner.sh d'abord ?"
    exit 1
fi

cd "$RUNNER_DIR"

if [ "$1" == "dev" ] || [ "$1" == "--dev" ]; then
    echo "🛠️ 2/2 Lancement en mode DEV (premier plan)..."
    
    # On démarre le suivi du fichier log de CI en arrière-plan
    LOG_FILE="$BASE_DIR/cluster-ci-runs.log"
    touch "$LOG_FILE"
    tail -f "$LOG_FILE" &
    TAIL_PID=$!
    
    # On lance le runner (qui va bloquer jusqu'à la fin du run)
    ./run.sh --once
    
    # On coupe le tail une fois le runner arrêté
    kill $TAIL_PID 2>/dev/null
else
    echo "⚙️ 2/2 Lancement en arrière-plan (daemon permanent)..."
    # On lance l'écoute des jobs en boucle continue (sans --once)
    nohup ./run.sh > ./runner_daemon.log 2>&1 &
    
    echo "✅ Le runner surveille les jobs Github en continu !"
    echo "📄 Les logs du runner sont disponibles dans : $RUNNER_DIR/runner_daemon.log"
    echo "(Astuce: tail -f $RUNNER_DIR/runner_daemon.log pour suivre l'activité)"
fi
