---
description: Générer un prompt de passation (Handover) narratif pour maintenir le contexte.
---

# Workflow: Context Handover

Ce workflow sert à générer un **"Prompt de Passation"** à la fin d'une conversation. L'objectif est de transmettre l'histoire de la session de manière naturelle mais **extrêmement précise**, comme si tu faisais une passation de dossier critique à un collègue.

## Pré-requis : Recherche de Contexte

**OBLIGATOIRE** : Avant de rédiger le handover, tu DOIS effectuer au minimum **1 recherche dans ta mémoire long-terme** (recall, get_recent_memories, ou équivalent) pour récupérer le contexte pertinent de la session et des sessions précédentes. Cela garantit que le handover est complet et précis.

## Philosophie
*   **Narratif ET Structuré** : On veut l'histoire, mais aussi les faits durs.
*   **Contenu Inclus** : ⚠️ **INTERDICTION DE CITER DES ARTEFACTS**. Le prochain agent n'y a PAS accès. Tu dois RÉ-EXPLIQUER ici tout ce qui était dans tes plans ou notes. N'aie pas peur de faire long.
*   **User-Centric** : Ce sur quoi l'utilisateur a *insisté* est sacré.
*   **Pas de Plan d'Implémentation** : ⚠️ Tu donnes le but, le brainstorming et les contraintes, mais **JAMAIS le plan d'exécution**. C'est au prochain agent de construire son plan.
*   **Pas de Blocs de Code** : ⚠️ **INTERDICTION d'inclure des blocs de code** (``` ou extraits de code). Raisons : (1) le handover est lui-même dans un bloc Markdown, donc imbriquer des blocs casse le rendu, et (2) l'implémentation n'est PAS ton rôle — c'est au prochain agent de décider du code à écrire.

## Structure du Prompt
Le prompt doit être généré dans un bloc de code Markdown.

### 1. 👋 Relai : [Titre de l'Action]
Un titre accrocheur résumant la mission immédiate.

### 2. Le Contexte & La Discussion (Narratif Détaillé)
Raconte l'histoire de la session.
*   **Le "Pourquoi"** : Quel était le problème initial ?
*   **Le "Comment"** : Quelles pistes avons-nous explorées ? (Explique les idées, ne dis pas "voir plan").
*   **Les Fichiers** : Cite les fichiers clés modifiés (ex: `server.py`).

### 3. Décisions Actées & Brainstorming
Liste clairement (tu as le droit aux listes ici pour la clarté) :
*   **Décisions Techniques** : "On a décidé d'utiliser X plutôt que Y car..."
*   **Insistance de l'Utilisateur** : "L'utilisateur a REFUSÉ qu'on touche à..." ou "Il veut ABSOLUMENT que..." (C'est crucial).
*   **Points identifiés** : Détails techniques importants trouvés pendant l'analyse (IDs, noms de variables, conflits...).

### 4. La Mission & L'Ordre de Marche
**CRITIQUE : INSTRUCTIONS OBLIGATOIRES POUR LE PROCHAIN AGENT**
Tu DOIS inclure ces instructions en gras :
> **⚠️ ATTENTION : Ne pars PAS directement dans le code. AVANT TOUTE CHOSE, effectue au minimum 3 recherches (dans ta mémoire long-terme et/ou dans le codebase) pour explorer le projet et comprendre le périmètre. Ensuite, établis un PLAN d'implémentation clair et soumets-le à l'utilisateur. Discute des détails ambigus AVANT de toucher à quoi que ce soit.**

**Référence au fichier de tâche** : Si un fichier de spécification de tâche existe (dans `docs/tasks/`), tu **DOIS** le mentionner explicitement :
> **📋 Un fichier de spécification existe pour cette tâche : `docs/tasks/[nom-du-fichier].md`. Lis-le en priorité avant de commencer ton plan.**

Donne ensuite le cap général de la mission (le "Quoi", pas le "Comment faire").

## Exemple de Sortie
```markdown
# 👋 Relai : Stabilisation des Logs & Refonte Config

### Contexte & Discussion
On est partis d'un problème de logs muets. On a découvert que `logging_config.py` était ignoré par `main.py`. J'ai commencé le fix, mais on est tombés sur un conflit de versions des libs. J'avais noté dans mon plan que le module `urllib3` posait problème, il faudra vérifier ça spécifiquement car la version installée est la 1.26 et on a besoin de la 2.0.

### Décisions & Points d'Attention
*   **INSISTANCE USER** : Ne JAMAIS modifier `custom_logger.py` (c'est une lib partagée).
*   **Décision** : On passe toutes les configs par variables d'env plutôt que par fichier .ini.
*   **A Discuter** : L'utilisateur n'est pas sûr de vouloir garder `Loguru`, il faut lui en reparler avant de l'intégrer partout.

### Mission
Finaliser la migration vers les variables d'env pour les logs.
**⚠️ STOP ! Ne code pas tout de suite. Explore d'abord le projet (mémoire long-terme + codebase), puis fais un plan et valide-le avec l'utilisateur. Point bloquant : confirmer avec l'utilisateur s'il veut garder Loguru avant d'aller plus loin.**
```
