# Authentification Silencieuse DVC

## 1. Contexte & Discussion (Narratif)
> *Handover* : Pour que l'intégration continue via le self-hosted runner soit fluide, le pipeline risque de bloquer lors de l'authentification DVC avec le cloud (typiquement Google Drive). Le flux en local repose habituellement sur OAuth qui exige l'ouverture d'un navigateur, provoquant un timeout de la CI.

Il s'agit donc d'adjoindre à l'orchestrateur de l'interface un mécanisme robuste d'authentification "silencieuse" (Headless). L'hypothèse validée avec l'utilisateur est d'injecter des Service Account Credentials via les GitHub Secrets, que la CI viendra transformer ou configurer localement afin que DVC authentifie les pull/push de dataseth sans aucune incitation à fournir de token. Il s'agit d'un point névralgique pour parachever notre intégration continue isolée.

## 2. Fichiers Concernés
- `src/runner/setup_dvc_auth.sh` (ou mécanisme inclus dans le repo ciblé)
- `docs/tasks/setup_orchestrator.md` (pour intégration de l'auth au script principal)

## 3. Objectifs (Definition of Done)
- Un script ou snipet exportable capable de récupérer les secrets GitHub depuis l'environnement.
- Configuration asynchrone parfaite de `dvc remote modify` en injectant le path d'un JSON Service Account temporaire afin que l'interblocage d'ouverture de navigateur ne se déclenche jamais lors de l'accès à GDrive.
- Le système ne doit laisser aucune credential valide hors du cycle de vie strict de l'exécution CI (sécurité).
