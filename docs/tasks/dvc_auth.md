# Authentification Silencieuse DVC

## 1. Contexte & Discussion (Narratif)
> *Handover* : Pour que l'intégration continue via le self-hosted runner soit fluide, le pipeline risque de bloquer lors de l'authentification DVC avec le cloud (typiquement Google Drive). Le flux en local repose habituellement sur OAuth qui exige l'ouverture d'un navigateur, provoquant un timeout de la CI.

Il s'agit donc d'adjoindre à l'orchestrateur un mécanisme robuste d'authentification "silencieuse". Après discussion architecturale, l'hypothèse des GitHub Secrets a été abandonnée au profit de l'**Option 1 : Cluster Global**. Les credentials (comme `GCP_CREDENTIALS` ou les tokens d'API) seront stockés sur la machine hôte du runner (par exemple dans un `.env.secrets` ou `.env`) et sourcés dynamiquement par le script `run_research_pipeline.sh` avant l'exécution de `uv run dvc repro`. Cela évite la duplication des clés pour tous les projets de recherche du labo.

## 2. Fichiers Concernés
- `src/runner/run_research_pipeline.sh` (modification pour inclure un source sur le `.env` de cluster-ci)
- `docs/tasks/dvc_auth.md`

## 3. Objectifs (Definition of Done)
- L'orchestrateur `run_research_pipeline.sh` doit charger les variables d'environnement présentes dans un fichier `.env` ou `.env.secrets` global (situé à la racine de `cluster-ci`).
- Les variables doivent être correctement exportées pour que `uv run dvc repro` y ait accès de façon transparente.
- Valider que l'interblocage d'ouverture de navigateur ne se déclenche jamais lors de l'accès à GDrive si les variables GCP sont présentes.

## 4. Implémentation réalisée (2026-04-13)
L'orchestrateur `src/runner/run_research_pipeline.sh` a été modifié pour sourcer dynamiquement `.env` et `.env.secrets` à l'aide de :
```bash
set -a
source "$BASE_DIR/.env"
set +a
```
Cela garantit que toutes les variables (comme `GCP_CREDENTIALS` ou `GCP_TOKEN`) sont exportées vers les processus fils, notamment `dvc repro`.
