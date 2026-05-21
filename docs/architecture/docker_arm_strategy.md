# Stratégie Docker & Dépendances sur ARM64 (Jetson)

## 1. Contexte & Le Problème du "Lockfile" sur ARM
L'écosystème Python (PyPI) ne fournit pas toujours de versions pré-compilées (wheels) pour l'architecture `aarch64` (ARM), particulièrement pour des bibliothèques lourdes en calcul comme PyTorch, Ray, grpcio ou SciPy.
Tenter d'utiliser `uv sync` (qui se repose sur un `uv.lock` souvent généré sous x86_64) sur un worker Jetson provoque des erreurs irrémédiables car il isole l'environnement dans un `.venv` et tente de télécharger des dépendances introuvables.

## 2. La Solution : Le Mariage "Golden Image" + Dépendances Dynamiques

Pour résoudre ce problème sans brider les chercheurs, Cluster-CI implémente une stratégie hybride :

### A. La "Golden Image" (Le Noyau Lourd)
Plutôt que d'installer les bibliothèques dures à compiler au moment de l'exécution, nous utilisons une image Docker de base (la "Golden Image").
- **Exemple** : `nvcr.io/nvidia/l4t-pytorch:r35.2.1-pth2.0-py3`
- **Rôle** : Fournir l'OS, les drivers CUDA/TensorRT, et une version nativement optimisée de bibliothèques critiques (comme PyTorch).
- Si les chercheurs ont besoin de bibliothèques complexes supplémentaires (e.g. `ray`), un administrateur peut créer une image `unildesi/cluster-ci-gold` qui hérite de l'image L4T et y compile `ray` une seule fois. Le paramètre `DOCKER_BASE_IMAGE` dans le `.env` de Cluster-CI pointera vers cette image.

### B. L'Injection Dynamique via `pyproject.toml` (Les Plugins Légers)
Les chercheurs développent leur code en listant leurs dépendances (légères ou wrappers) dans leur `pyproject.toml`.
- **Mécanisme** : Au lancement du job sur ARM, l'orchestrateur (`run_research_pipeline.sh`) détecte l'architecture (`aarch64`) et court-circuite `uv sync`.
- Il exécute à la place : `uv pip install --system . || uv pip install --system -r pyproject.toml`
- **Avantage** : Cela installe les bibliothèques manquantes (pandas, tqdm, requests...) directement dans l'environnement Python principal du conteneur (qui est éphémère). 
- **Résultat** : Les dépendances du chercheur "s'ajoutent" par dessus le noyau lourd. L'absence du `.venv` permet au code d'utiliser le PyTorch optimisé de NVIDIA (situé dans `/usr/local/lib/python...`).

## 3. Gouvernance pour les Chercheurs

1. **Aucune configuration spéciale requise** : Les chercheurs peuvent utiliser `uv.lock` sur leur PC Windows/Mac/Linux (x86_64) sans souci.
2. **Priorité au pyproject.toml** : Sur le cluster ARM, seul le `pyproject.toml` est lu. Les dépendances sont résolues dynamiquement pour ARM.
3. **Mise à jour de la Golden Image** : Si un projet échoue systématiquement sur une dépendance introuvable (C++ bloquant), le chercheur doit demander l'intégration de cette dépendance dans la Golden Image du labo.

> **💡 Note pour les exécutions x86_64** : Si un worker Cluster-CI tourne sur un serveur classique x86_64 (e.g., avec un GPU RTX 4090 standard), l'orchestrateur appliquera le comportement natif (`uv sync` et création du `.venv` strict), car PyPI supporte parfaitement cette architecture.
