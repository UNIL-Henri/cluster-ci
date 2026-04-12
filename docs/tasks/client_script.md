# Script d'Installation Client

## 1. Contexte & Discussion (Narratif)
> *Handover* : L'expérience développeur (ou plutôt "expérience agent") doit être irréprochable. Pour simplifier l'intégration du nouveau Cluster CI sur de multiples projets de recherche existants, nous avons décidé de fournir un installateur express (minimaliste).

Ce script client sera exécuté dans les repositories de recherche (ceux sur lesquels travaillent Joules) via un `curl -sSL ... | bash`. Il devra auto-générer les répertoires et fichiers `.github/workflows/` contenant un workflow type `.github/workflows/execute_on_ubuntu.yml` calibré pour cibler les tags self-hosted de notre nouvelle machine de test.

## 2. Fichiers Concernés
- `install.sh` (à la racine du dépôt cluster-ci)
- `templates/execute_on_ubuntu.yml` (template du workflow CI à distribuer)

## 3. Objectifs (Definition of Done)
- Un fichier `install.sh` distribué et robuste.
- Le script détecte la racine du git et injecte silencieusement le dossier `.github/workflows/`.
- Le payload injecté `execute_on_ubuntu.yml` est un workflow GitHub Actions valide, prêt à l'emploi.
- Le workflow client injecté est configuré pour écouter les "Push" ou "Pull Requests", utiliser le tag du "self-hosted" runner, et appeler la sur-couche d'orchestration (qui elle sera pré-déployée sur ledit runner).
- Instructions de log informant Joules sur la marche à suivre si l'installation réussit ou échoue.
