# JIT Garbage Collector (LRU)

## 1. Contexte & Pourquoi
Afin de préserver l'espace disque des machines de calcul tout en profitant de la vitesse du cache local, nous gérons dynamiquement les espaces de travail (workspaces). L'approche choisie est une suppression Just-In-Time (JIT) avant l'exécution de nouveaux jobs.

## 2. Fonctionnement
Le système s'appuie sur un registre local de métadonnées (`repositories/registry.json`) qui suit :
- Le nom du projet (owner/repo).
- La date de dernière exécution (Timestamp).
- La taille sur le disque (octets).
- Le statut (`running`, `idle`, ou `deleted`).

### Politique de nettoyage (LRU)
Avant chaque job, l'orchestrateur vérifie l'espace disque disponible sur la partition des dépôts.
- **Seuil de sécurité :** 100 Go.
- Si l'espace libre est inférieur à ce seuil, le GC identifie les projets marqués comme `idle`.
- Il supprime les dossiers locaux en commençant par le plus ancien (Least Recently Used) jusqu'à ce que le seuil de 100 Go soit respecté.
- Les projets en cours (`running`) ne sont jamais supprimés.

## 3. Composants
- `src/runner/gc_orchestrator.py` : Script Python gérant la logique du GC et du registre.
- `src/runner/run_research_pipeline.sh` : Orchestrateur Shell intégrant les appels au GC.

## 4. Intégration
L'orchestrateur appelle `gc_orchestrator.py` aux moments clés :
1. **Avant le job :** `run-gc` pour libérer l'espace et `update-running` pour marquer le projet actif.
2. **Après le job (via trap EXIT) :** `update-idle` pour mettre à jour la taille occupée et marquer le projet comme inactif.
