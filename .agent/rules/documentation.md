---
trigger: always_on
glob: "**/*"
description: "documentation"
---
# Documentation & README Rule

Cette règle définit la gouvernance de la documentation et du README du projet. L'agent doit maintenir une documentation structurée, vivante et parfaitement indexée.

## 1. README.md : Source de Vérité

Le fichier [README.md](mdc:README.md) doit rester parfaitement synchronisé avec l'état actuel du dépôt. **Chaque conversation doit se conclure par une mise à jour explicite du README**, même si les changements sont purement documentaires.

### Principes directeurs
- **Actualisation continue** : Mettre à jour au moindre changement (code, dépendance, feature).
- **Structure immuable** : La structure des sections (1 à 9) est obligatoire.
- **Précision factuelle** : Aucune information obsolète tolérée.

### Structure imposée du README

1. **Paragraphe de présentation** (Objectif, état, features)
2. **# Installation** (Commande unique, pré-requis)
3. **# Description détaillée** (Cœur, Flux, Rôles, Direction)
4. **# Principaux résultats** (Tableaux/Graphes)
5. **# Documentation Index** (NOUVEAU)
   - Doit lister tous les fichiers d'index de documentation.
   - Format : Tableau à 2 colonnes.
     | Titre (Lien) | Description |
     |--------------|-------------|
     | [Index Sujet A](docs/index_sujeta.md) | Description du sujet A |
6. **# Plan du repo** (Arborescence)
7. **# Scripts d'entrée principaux** (Tableau des commandes)
8. **# Scripts exécutables secondaires & Utilitaires**
9. **# Roadmap**

## 2. Documentation Détaillée (`docs/`)

L'agent doit proactivement créer de la documentation dans le dossier `docs/` pour tout sujet nécessitant des détails (architecture, algorithmes, guides, décisions techniques).

### Structure des dossiers
La structure doit suivre ce modèle rigoureux :
```
docs/
    subject1/           # Dossier contenant les notes atomiques
        file1.md
        file2.md
    subject2/
        ...
    index_subject1.md   # Index racine pour le sujet 1
    index_subject2.md   # Index racine pour le sujet 2
```

### Règles de Gestion
1. **Création proactive** : Ne pas surcharger le README. Créer des fichiers spécifiques dans `docs/sujet/` et les lier.
2. **Maintien des Index** :
   - À chaque création/édition de fichier dans `docs/sujet/`, l'agent **DOIT** mettre à jour le fichier `docs/index_sujet.md` correspondant.
   - Si un nouveau sujet est créé, créer son `index_unique.md` et l'ajouter au "Documentation Index" du README.

### Format des Fichiers Index (`docs/index_*.md`)
Les fichiers index listent le contenu de leur sujet sous forme de tableau à 4 colonnes strictes :

| Titre de la note | Courte Description | Dernière modif | Tag |
|------------------|-------------------|----------------|-----|
| [Nom du Fichier](sujet/fichier.md) | Résumé concis | YYYY-MM-DD | `Up to date` / `Legacy` |

- **Date** : À mettre à jour à chaque modification du fichier cible.
- **Tag** : Indiquer si l'information est toujours d'actualité (`Up to date` ou `Legacy`).

## 3. Tâches de la Roadmap (`docs/tasks/`)

Toute tâche ajoutée à la **Roadmap** dans le README doit **OBLIGATOIREMENT** être liée à un fichier de spécification unique dans `docs/tasks/`.

### Règle d'Or
> **"Pas de fichier de tâche = Pas de ligne dans la Roadmap"**

### Structure du Fichier de Tâche (`docs/tasks/xxx.md`)
Ce fichier doit servir de point d'entrée pour l'agent qui réalisera la tâche.

#### Modèle Obligatoire :
```markdown
# [Titre de la Tâche]

## 1. Contexte & Discussion (Narratif)
> *Inspire-toi du style "Handover" : Raconte pourquoi on fait ça.*
- Résumé de la discussion avec l'utilisateur.
- Historique des décisions (pourquoi on a choisi cette approche ?).
- Liens vers les anciennes conversations ou KIs si pertinent.

## 2. Fichiers Concernés
- `src/chemin/vers/fichier_A.py`
- `docs/chemin/vers/doc_B.md`

## 3. Objectifs (Definition of Done)
*Décris ce que l'on veut obtenir À LA FIN (High Level).*
* **NE PAS** écrire de plan d'implémentation détaillé (pas de "créer fonction x").
* **NE PAS** écrire de pseudo-code.
* **FOCUS** sur le résultat attendu et la valeur ajoutée.
```

## Bonnes pratiques forcées
- **Toujours finir par le README**.
- **Vérification croisée** : Vérifier les liens morts dans les index après suppression ou renommage.
- **Fail-fast documentaire** : Corriger immédiatement une doc fausse vue en passant.
