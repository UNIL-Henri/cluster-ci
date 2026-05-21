# Gestion de la Concurrence par Dépôt

## 1. Contexte & Discussion (Narratif)
Lors de l'exécution de jobs de recherche longs, il est possible que plusieurs Pull Requests ou commits soient soumis successivement. Sans gestion de la concurrence, ces jobs s'accumulent dans la file d'attente du self-hosted runner, consommant du temps et retardant le feedback sur les versions les plus récentes.

La discussion avec l'utilisateur a mené à la décision d'implémenter une annulation agressive au niveau du dépôt complet. Comme l'annulation d'un job GitHub Actions ne déclenche pas de notification d'échec (fail), cela permet d'interrompre les jobs obsolètes sans "réveiller" inutilement l'agent Joules, évitant ainsi un effet de ping-pong permanent entre plusieurs branches ou versions.

## 2. Protection Mémoire (Watchdog)
Afin d'offrir une meilleure expérience utilisateur et de s'affranchir des contraintes liées aux privilèges administrateur (`sudo systemd-run`), l'isolation de la RAM est gérée par un watchdog applicatif.

*   **Mécanisme** : Le Worker monitore récursivement l'utilisation de la RAM de l'arbre de processus du job.
*   **Action** : En cas de dépassement de la limite fixée dans `.cluster-ci`, le job est arrêté avec un code d'erreur 137.
*   **UX** : Un message explicite est envoyé sur stderr pour s'afficher en rouge dans l'interface GitHub Actions, indiquant la consommation réelle vs la limite réservée.

## 3. Fichiers Concernés
- `install.sh` : Modèle de workflow GitHub Actions injecté dans les projets clients.

## 4. Objectifs (Definition of Done)
*   Le script `install.sh` injecte désormais un bloc `concurrency` dans le fichier `.github/workflows/cluster-ci.yml`.
*   Le groupe de concurrence est défini sur le dépôt complet (`${{ github.repository }}`).
*   L'option `cancel-in-progress` est activée (`true`).
*   Le déclenchement de la CI reste actif pour les `push` sur les branches principales et les `pull_request`.
*   Un nouveau commit ou une nouvelle action annule immédiatement toute exécution en cours pour le dépôt concerné.

## 5. Relation avec le Garbage Collector JIT
L'annulation agressive des jobs permet au Garbage Collector JIT de marquer plus rapidement les environnements comme `idle` (via le mécanisme de `trap EXIT` dans l'orchestrateur), facilitant ainsi la libération d'espace disque si nécessaire pour d'autres tâches prioritaires.
