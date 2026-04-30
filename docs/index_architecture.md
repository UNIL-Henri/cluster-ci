# Documentation de l'Architecture

| Titre de la note | Courte Description | Dernière modif | Tag |
|------------------|-------------------|----------------|-----|
| [Topologie Headnode/Worker](#topologie-headnode-worker-et-ordonnancement-dynamique) | Description de la nouvelle architecture de calcul distribué | 2024-05-23 | Architecture |

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

### Isolation et Protection (Protection OOM Artificielle)
Pour garantir la stabilité des Workers, chaque job est exécuté via `systemd-run`.
- **Cgroups :** Utilisation des limites `MemoryMax` et `MemorySwapMax`.
- **Comportement :** Si un job dépasse la RAM qu'il a déclarée, le kernel (via systemd) tue immédiatement le processus. Cela évite les saturations système ("OOM Killer" global) et protège les autres jobs ou services tournant sur la même machine.
