# Pre-flight Scanner Logic

Le Pre-flight Scanner est conçu pour garantir la compatibilité entre un projet de recherche local et l'environnement d'exécution distant (Cluster).

## Fonctionnement

1. **Extraction des Contraintes** : Le script `scripts/update_cluster_constraints.sh` génère un fichier `cluster_constraints.txt` à partir de l'image Docker de référence.
2. **Validation Locale (Pre-commit)** : Le hook Git lance `validate_pyproject.py --interactive`.
   - Il vérifie `requires-python` (doit accepter 3.12).
   - Il vérifie l'absence de pinning strict sur `torch` (ex: `==1.12`).
   - Il simule la résolution via `uv pip compile --os linux --arch aarch64`.
3. **Auto-correction** : En mode interactif, le script propose de modifier le `pyproject.toml` en utilisant `tomlkit` pour préserver les commentaires.
4. **Validation CI** : Le runner (`run_research_pipeline.sh`) exécute le scanner avec le flag `--ci` avant de lancer l'installation.

## Dépendances Critiques
- **Python 3.12** : Version imposée par le conteneur NGC.
- **PyTorch 2.12** : Déjà présent dans l'image, ne doit pas être réinstallé via PyPI sur ARM64.
