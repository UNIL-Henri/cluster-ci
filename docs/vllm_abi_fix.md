# vLLM ABI Fix

La documentation complète et détaillée de la résolution du problème d'incompatibilité C++ ABI de vLLM avec PyTorch (NVIDIA NGC) se trouve dans la section dédiée à la documentation structurée de vLLM :

👉 **[Guide de Résolution de l'incompatibilité vLLM C++ ABI (docs/vllm/abi_fix.md)](vllm/abi_fix.md)**

---

## Résumé de la solution de contournement

1. **Diagnostic** : Incompatibilité entre l'ABI C++ legacy de PyTorch dans le conteneur NGC (`_GLIBCXX_USE_CXX11_ABI=False` / `0`) et l'ABI C++11 attendue par les wheels PyPI de vLLM (`_GLIBCXX_USE_CXX11_ABI=True` / `1`).
2. **Action** : Compilation de vLLM (`v0.7.3`) à partir des sources directement au sein du conteneur de destination pour cibler l'architecture GPU Blackwell (`sm_100` / Compute Capability `10.0`) avec `MAX_JOBS=32`.
3. **Shadowing Clean-up** : Purge systématique des répertoires locaux de site-packages `/home/user/.local/lib/python3.12/site-packages/vllm*` pour éviter que les packages cassés de PyPI ne masquent l'installation système propre.
4. **Intégration Cluster-CI** : Mise à jour du script `src/runner/smart_install.sh` pour exclure automatiquement `vllm`, `torch`, `torchvision`, `torchaudio` lors des installations automatiques de dépendances, et nettoyage post-installation.

Pour plus de détails, veuillez consulter le fichier [abi_fix.md](vllm/abi_fix.md).
