---
description: Merge des pull requests ouvertes, résolution de conflits, validation et walkthrough.
---

# Commande Pull — Merge Autonome des PRs Validées 🔀

## Objectif

Quand l'utilisateur tape `/pull`, tu dois **agir en autonomie totale** :
1. **Inventorier** les PRs ouvertes du dépôt actif portant le label `valid`.
2. **Merger** chaque PR éligible en résolvant les conflits intelligemment.
3. **Gérer la Roadmap** : Mettre à jour les issues, en créer de nouvelles si nécessaire, synchroniser la Roadmap du README — en tant qu'**Architecte bis**.
4. **Préparer** un environnement de validation complet pour l'utilisateur.
5. **Générer** un walkthrough orienté **validation** : procédures de test, résultats d'exécution, résultats d'expériences.

**AUTONOMIE TOTALE** : Tu ne demandes JAMAIS confirmation. Tu merges, tu résous, tu déploies. Si quelque chose casse → CRASH (fail-fast). Pas de questions, pas d'hésitations.

## Prérequis

### Accès GitHub
- **Outil principal** : Utiliser les outils MCP `github-mcp-server` (`mcp_github-mcp-server_list_pull_requests`, `mcp_github-mcp-server_merge_pull_request`, etc.) si disponibles.
- **Fallback CLI** : Si les outils MCP ne sont pas disponibles, utiliser `gh` CLI (GitHub CLI).
- **Fallback API** : Si ni MCP ni `gh` ne sont disponibles, utiliser l'API REST GitHub via `curl` avec un token d'authentification.
- **Si aucun accès** : CRASH immédiatement avec un message clair expliquant qu'un accès GitHub authentifié est requis.

### Identification du Dépôt
- Détecter automatiquement `owner` et `repo` depuis `git remote get-url origin`.
- Parser l'URL SSH (`git@github.com:owner/repo.git`) ou HTTPS (`https://github.com/owner/repo.git`).

## Comportement Requis

### Phase 1 : 📋 Inventaire des Pull Requests Éligibles

1. **Lister toutes les PRs ouvertes** du dépôt **actif** (celui dans lequel l'utilisateur travaille).
2. **Filtrer par label** : Seules les PRs portant le label **`valid`** sont éligibles au merge. Ignorer silencieusement toutes les autres.
3. **Pour chaque PR éligible, collecter** :
   - Numéro, titre, auteur, branche source → branche cible
   - Description / body de la PR
   - Issues liées (via `closes #XX`, `fixes #XX`, ou liens manuels)
   - Nombre de fichiers modifiés et diff stat
4. **Si aucune PR `valid` trouvée** :
   - Lister les PRs ouvertes sans le label `valid` et les branches non-mergées.
   - Informer l'utilisateur et **STOP**.

### Phase 2 : 🔀 Merge Autonome

**CRITIQUE** : Les PRs sont mergées **une par une**, en autonomie totale, sans confirmation.

#### Étape 2.1 : Déterminer l'Ordre de Merge

1. **Analyser les dépendances entre PRs** :
   - PR A modifie `file.py` lignes 10-20, PR B modifie `file.py` lignes 50-60 → indépendantes.
   - PR A ajoute une fonction, PR B l'utilise → PR A en premier.
2. **Trier par** :
   - PRs sans conflits d'abord (merge trivial)
   - PRs avec le moins de fichiers modifiés ensuite
   - PRs avec conflits en dernier

#### Étape 2.2 : Merger Chaque PR

Pour chaque PR, dans l'ordre déterminé :

1. **Exécuter le merge** :
   ```bash
   git fetch origin
   git checkout <branche_cible>
   git merge origin/<branche_source> --no-ff -m "Merge PR #XX: <titre>"
   ```

2. **Si conflits** → les résoudre immédiatement (Étape 2.3), puis continuer.

#### Étape 2.3 : Résolution Intelligente de Conflits

Résoudre chaque conflit **au mieux**, de manière autonome :

1. **Lire** le fichier avec les marqueurs de conflit.
2. **Analyser** les deux versions (ours vs theirs).
3. **Résoudre** :
   - Modifications **complémentaires** → fusionner les deux.
   - Modifications **contradictoires** → conserver la version la plus récente/complète.
   - **Jamais** supprimer du code sans comprendre ce qu'il fait.
4. **Committer** la résolution :
   ```bash
   git add <fichiers_résolus>
   git commit -m "Resolve conflicts for PR #XX"
   ```

### Phase 3 : 🏗️ Rôle d'Architecte — Mise à Jour Roadmap & Issues

**Tu agis comme un Architecte bis** (cf. `/architect`). Après les merges, tu dois :

#### 3.1 : Analyser ce qui a été mergé

1. **Lire le contenu** de chaque PR mergée pour comprendre ce qui a changé.
2. **Identifier les impacts** sur le projet : nouvelles fonctionnalités, corrections, refactoring, etc.

#### 3.2 : Mettre à jour les Issues Existantes

1. **Issues liées aux PRs** :
   - Si la PR résout entièrement l'issue → **fermer** l'issue.
   - Si la PR résout partiellement l'issue → **commenter** la progression.
2. **Issues impactées indirectement** :
   - Si les changements mergés rendent une issue obsolète → la fermer avec explication.
   - Si les changements créent de nouvelles contraintes sur une issue existante → la mettre à jour.

#### 3.3 : Proposer de Nouvelles Issues

Si les changements mergés révèlent :
- Du **travail supplémentaire** nécessaire (TODO, code incomplet, tests manquants)
- Des **opportunités d'amélioration** (refactoring, performance, dette technique)
- Des **bugs potentiels** (code fragile, edge cases non couverts)

→ **Créer les issues GitHub** correspondantes avec le format défini dans `src/rules/documentation.md` (Contexte, Fichiers, Objectifs).

#### 3.4 : Synchroniser la Roadmap du README

1. **Mettre à jour** l'état des tâches existantes dans la Roadmap.
2. **Ajouter** les nouvelles tâches identifiées (liées aux nouvelles issues).
3. **Supprimer** les tâches terminées si elles sont entièrement résolues.
4. **Vérifier** la cohérence globale : pas de doublons, pas de tâches fantômes.

### Phase 4 : ✅ Mise en Place de l'Environnement de Validation

**OBJECTIF** : L'utilisateur doit pouvoir **immédiatement** vérifier que tout fonctionne.

#### 4.1 : Détecter le Type de Projet

Analyser le contenu mergé pour déterminer la nature du projet :

| Indicateur | Type | Action de Validation |
|-----------|------|---------------------|
| `requirements.txt`, `setup.py`, `pyproject.toml` | Python App/Lib | `pip install -e .` ou `pip install -r requirements.txt` |
| `package.json` | Node.js App | `npm install && npm run dev` |
| `Dockerfile`, `docker-compose.yml` | Conteneurisé | `docker-compose up` |
| `Makefile` | Build System | `make` ou `make test` |
| Fichiers `.ipynb`, `dvc.yaml` | Expérience / Recherche | Exécuter les notebooks, récupérer les résultats DVC |
| Fichiers `streamlit`, `app.py` | UI Streamlit | `streamlit run app.py` |
| Fichiers `*.test.*`, `pytest.ini` | Tests | `pytest` ou `npm test` |

#### 4.2 : Installer les Dépendances

1. **Détecter et installer** toutes les nouvelles dépendances introduites par les PRs.
2. **En cas d'erreur d'installation** : CRASH avec le log complet.

#### 4.3 : Exécuter les Validations

Selon le type de projet :

- **Application Web / UI Streamlit** : Démarrer le serveur et fournir l'URL d'accès.
- **Expérience / Recherche** :
  - Exécuter les pipelines ou récupérer les résultats existants.
  - **Collecter et présenter** : métriques, graphiques, tableaux de résultats.
- **Tests** : Exécuter la suite de tests complète et **rapporter les résultats dans le walkthrough**.
- **Bibliothèque** : Vérifier que les imports fonctionnent, exécuter les tests unitaires.

#### 4.4 : Vérification Post-Merge

1. **Exécuter les tests existants** pour détecter les régressions.
2. **Vérifier que le build passe**.
3. **Si échec** → CRASH immédiatement avec les logs.

### Phase 5 : 📄 Génération du Walkthrough de Validation

**OBLIGATOIRE** : Générer un walkthrough orienté **validation utilisateur**.

Le walkthrough N'EST PAS un log technique des merges. C'est un **guide de validation** pour l'utilisateur.

#### Structure du Walkthrough

```markdown
# 🔀 Walkthrough de Validation (YYYY-MM-DD)

## Résumé des Merges

| Métrique | Valeur |
|----------|--------|
| PRs mergées | X |
| Fichiers modifiés (total) | Z |
| Issues fermées | W |
| Nouvelles issues créées | V |

## Ce qui a changé

### PR #XX : Titre
- **Ce que ça apporte** : Description fonctionnelle claire
- **Fichiers clés** : liste des fichiers importants modifiés

[Répéter pour chaque PR]

## Procédure de Validation

### Prérequis
[Dépendances à installer, configuration nécessaire]

### Étapes de Test
[Instructions pas-à-pas pour que l'utilisateur puisse valider chaque fonctionnalité mergée]

1. **Tester [fonctionnalité A]** :
   - Commande : `...`
   - Résultat attendu : ...
2. **Tester [fonctionnalité B]** :
   - ...

### Résultats des Tests Automatiques
[Output complet de pytest, npm test, etc. — copié tel quel]

### Résultats d'Expériences (si applicable)
[Métriques collectées, tableaux de résultats, graphiques]

### Application / Interface (si applicable)
[URL d'accès, captures d'écran, instructions de navigation]

## Roadmap Post-Merge

### Issues Fermées
| Issue | Raison | PR |
|-------|--------|-----|

### Nouvelles Issues Créées
| Issue | Description | Priorité |
|-------|-------------|----------|

### État de la Roadmap
[État actuel de la Roadmap après synchronisation]

## Prochaines Étapes
[Recommandations basées sur l'analyse architecturale]
```

## Gestion des Erreurs (Fail-Fast)

**CRITIQUE** : Ce workflow suit la philosophie **fail-fast**. Aucun fallback silencieux.

| Erreur | Action |
|--------|--------|
| Pas d'accès GitHub authentifié | CRASH avec instructions de configuration |
| Échec d'installation de dépendances | CRASH avec log complet de l'erreur |
| Tests échouent après merge | CRASH — rapporter les logs, proposer un revert |
| Aucune PR avec le label `valid` | Informer et STOP (pas un crash, juste un arrêt propre) |

## Notes Importantes

- **Autonomie** : Tu agis seul, sans demander confirmation. Merge, résous, déploie.
- **Label `valid` obligatoire** : Seules les PRs taguées `valid` sont traitées.
- **Fail-Fast** : Toute erreur provoque un arrêt immédiat avec message clair.
- **Architecte bis** : Tu mets à jour la Roadmap, les issues, et proposes de nouvelles issues comme le ferait l'Architecte.
- **Walkthrough orienté validation** : Le walkthrough doit permettre à l'utilisateur de **tester immédiatement** ce qui a été mergé (procédures, résultats, commandes).
- **Français** : Tout le contenu du walkthrough et la communication en français.
- **README** : Le README doit être mis à jour à la fin du workflow.
