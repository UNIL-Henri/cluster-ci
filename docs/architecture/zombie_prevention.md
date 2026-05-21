# Prévention des Processus Orphelins dvc-viewer

Ce document détaille la stratégie et les mécanismes d'éradication des processus orphelins (zombies) `dvc-viewer` sur les workers du cluster.

## 1. Contexte & Problématique

Le `dvc-viewer` est un composant interactif démarré à la demande pour chaque job DVC afin de permettre la visualisation des métriques et des résultats de recherche. Cependant, plusieurs facteurs provoquaient l'accumulation de ces processus en tâche de fond sur les workers :
1. **Partage d'espace de nommage de PID Docker fragile** : L'option `--pid="container:${MAIN_CONTAINER_NAME}"` entraînait des conflits de destruction et empêchait l'arrêt propre du conteneur de viewer lorsque le conteneur principal se terminait brutalement.
2. **Filtrage de chemin pwdx inopérant** : Le script de nettoyage initial utilisait `pwdx "$pid"` sur l'hôte en le comparant avec le dossier de travail interne du conteneur `/workspace`, ce qui échouait systématiquement à identifier les processus orphelins du conteneur s'exécutant sur l'hôte.
3. **Cancellations abruptes de jobs** : L'utilisation de `psutil` pour tuer l'arbre de processus lors d'une annulation de job court-circuitait les gestionnaires de signaux Bash (`trap EXIT`), empêchant ainsi l'exécution des routines de nettoyage classiques.

## 2. Solutions Appliquées

### A. Suppression de la dépendance de PID Docker
Nous avons retiré l'option `--pid="container:${MAIN_CONTAINER_NAME}"` lors du démarrage du conteneur `dvc-viewer`. Ce conteneur n'a besoin que d'un accès en lecture au volume partagé du workspace pour lire les métriques DVC, et non de partager l'espace de nommage PID du conteneur d'exécution. Cela permet au conteneur de viewer d'être géré et détruit de manière totalement isolée et robuste.

### B. Purge proactive et globale sur l'hôte
Puisqu'un worker n'exécute qu'un seul job de recherche à la fois, tout processus `dvc-viewer` résiduel détecté sur l'hôte au démarrage ou à la fin d'un job est par définition un orphelin d'une exécution précédente. 
Nous avons remplacé la vérification restrictive `pwdx` par un balayage proactif global sur l'hôte :
- **Au démarrage du job** : Nettoyage systématique de tout processus `dvc-viewer` résiduel.
- **Pendant le nettoyage Bash (`cleanup_job_resources`)** : Cible explicitement le port du viewer (`--port $VIEWER_PORT`) et force le signal `kill -9` sur tout processus viewer résiduel.

### C. Watchdog d'Agent Worker et GC Hybride (`psutil`)
Pour contrer les cas où Bash est brutalement interrompu (ex: annulation via le Headnode), nous avons doté l'Agent Worker et le Zombie GC de routines actives en Python via `psutil` :
1. **Worker Agent** :
   - `kill_dvc_viewer_processes()` scanne les processus de l'hôte et élimine impérativement tout binaire ou commande contenant `dvc-viewer`.
   - Exécuté systématiquement au démarrage de `execute_job`, dans le bloc `finally` de fin d'exécution de job (que le job soit en succès, en échec ou expiré), ainsi que dans la route de cancellation du job (`/cancel/<job_id>`).
2. **Zombie GC Watchdog (`gc_orchestrator.py`)** :
   - Scan et élimination active de tout processus `dvc-viewer` sur l'hôte si aucun conteneur de job n'est actif sur le worker, assurant une hygiène absolue et continue des ressources.
