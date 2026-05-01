# Documentation de l'Architecture

| Titre de la note | Courte Description | Dernière modif | Tag |
|------------------|-------------------|----------------|-----|
| [Topologie Headnode/Worker](#topologie-headnode-worker-et-ordonnancement-dynamique) | Description de la nouvelle architecture de calcul distribué | 2024-05-23 | Architecture |
| [JIT GC](tasks/jit_gc.md) | Garbage Collector basé sur LRU pour gérer l'espace disque. | 2024-05-22 | Infrastructure |
| [Concurrency Management](tasks/concurrency_management.md) | Gestion de l'annulation des jobs obsolètes. | 2024-05-22 | Infrastructure |

## Topologie Headnode/Worker et Ordonnancement Dynamique

### Contexte
Afin d'optimiser l'utilisation des ressources matérielles (notamment les machines Lenovo ThinkStation), le système évolue vers une topologie asymétrique.

### Rôles
- **Headnode (desi) :** Agit comme point d'entrée unique pour la CI. Il héberge l'ordonnanceur (Scheduler) et gère la file d'attente des jobs.
- **Workers (Lenovo) :** Machines dédiées à l'exécution. Elles font remonter leur état (RAM disponible) au Headnode et exécutent les jobs assignés.

### Ordonnancement (Bin-Packing)
L'allocation des jobs repose sur un algorithme de **Bin-Packing** basé sur la mémoire unifiée (RAM).
1. Le job déclare son besoin en RAM (via `.cluster-ci`).
2. L'ordonnanceur maintient une vue en temps réel de la RAM disponible sur chaque Worker.
3. Le job est assigné au premier Worker ayant assez de RAM disponible, ou mis en attente si aucune ressource n'est libre.

### Isolation et Protection (Watchdog Mémoire Logiciel)
Pour garantir la stabilité des Workers, chaque job est surveillé par un **Watchdog Logiciel** basé sur la librairie `psutil`.

- **Mesure Récursive :** À intervalles réguliers (toutes les 2 secondes), le Worker calcule la somme de la mémoire physique (RSS) du processus de calcul et de tous ses processus enfants (par ex. sous-processus DVC).
- **Interruption UX :** Si la somme dépasse la limite de RAM déclarée dans `.cluster-ci`, le Worker tue immédiatement l'arbre de processus.
- **Feedback GitHub :** Un message d'erreur explicite est affiché sur la sortie standard d'erreur (stderr), permettant au chercheur de savoir exactement pourquoi son job a échoué et combien de RAM a été consommée par rapport à sa réservation.
