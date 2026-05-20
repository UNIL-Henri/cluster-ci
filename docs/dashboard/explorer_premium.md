# Explorateur d'Artefacts Premium & Navigation Bidirectionnelle

Cette note technique décrit l'implémentation de la refonte premium du Dashboard de Cluster-CI, introduisant la navigation bidirectionnelle inversée (de l'artefact vers les runs) et l'explorateur d'artefacts interactif.

---

## 1. Contexte & Objectifs

L'interface initiale de Cluster-CI était principalement orientée "Runs" : pour accéder à un fichier, l'utilisateur devait obligatoirement trouver la run spécifique qui l'avait généré, puis explorer son arborescence de fichiers.
Cette approche était peu intuitive pour les chercheurs souhaitant :
- Accéder directement aux versions les plus récentes de leurs modèles ou jeux de données (les artefacts).
- Naviguer chronologiquement dans l'historique physique d'un artefact spécifique pour comparer des versions.
- Identifier précisément quelle exécution (run) a produit une version particulière de cet artefact.

**La refonte apporte :**
1. Un **Design Premium** basé sur un thème clair moderne et épuré, des ombres douces et la typographie Google Font **Inter** (et **Fira Code** pour le code/logs).
2. Un **Explorateur d'Artefacts transversaux** affichant l'arborescence complète pliable et interactive du dernier commit réussi.
3. Une **Navigation Bidirectionnelle (Artefact ➔ Runs)** permettant de consulter l'historique d'un fichier DVC, de basculer d'une version physique à une autre et d'accéder instantanément à la run correspondante.

---

## 2. Architecture Technique & APIs

Pour soutenir cette nouvelle navigation, deux routes clés ont été implémentées dans le backend Flask du Headnode (`headnode_service.py`) :

### A. Liste des Artefacts Récents (`/api/projects/<repo>/artifacts/latest`)
Cette route liste de manière récursive tous les fichiers DVC produits dans le dernier run `completed` réussi.
- **Fonctionnement** : 
  1. Le scheduler interroge la base de données SQLite pour trouver la dernière run réussie (`status = 'completed'` et `commit_hash IS NOT NULL`).
  2. Il utilise l'utilitaire DVC local ou distant pour lister récursivement les fichiers : `dvc list <source> --rev <commit_hash> --dvc-only --recursive --json`.
  3. Le résultat JSON brut est renvoyé au frontend.

### B. Historique Physique d'un Artefact (`/api/projects/<repo>/artifact/history`)
Cette route extrait l'historique de toutes les versions d'un fichier DVC spécifique à travers l'historique des commits associés aux runs réussies.
- **Optimisation Git/DVC** : Au lieu d'appeler le lourd outil `dvc list` sur l'ensemble de l'historique (ce qui prendrait plusieurs secondes par version), le backend inspecte directement le fichier `.dvc` correspondant ou le fichier `dvc.lock` dans l'historique Git via `git show <commit_hash>:<file_path>.dvc`.
- **Parsing YAML ultra-résistant** : L'extraction s'appuie sur une double stratégie :
  1. Tentative de parsing YAML propre via `PyYAML`.
  2. Fallback automatique via un parseur regex ligne par ligne en cas de fichier mal formé ou de syntaxe DVC fluctuante.
- **Résultat** : Un tableau contenant l'historique des commits associés aux hashes MD5 uniques du fichier, les tailles, dates, branches et messages de commits.

---

## 3. Implémentation Frontend (dashboard.html)

### A. Design System Premium
- **Variables CSS** : Définition d'un thème à base de tons ardoises (`#f8fafc`, `#e2e8f0`, `#0f172a`), un bleu saphir moderne pour les interactions primaires (`#2563eb`), un vert émeraude pour les succès et un ambre pour les dossiers.
- **Typographie** : Chargement asynchrone des polices **Inter** pour les textes, et **Fira Code** pour les blocs monospécifiques, améliorant drastiquement le rendu visuel.
- **Animations** : Transitions CSS douces au survol (`cubic-bezier`), effets d'élévation sur les cartes de projets et d'artefacts.

### B. Onglets de Navigation Projet
Lors du clic sur un projet, l'affichage se divise en deux onglets élégants :
1. **Runs & Logs** : L'historique traditionnel avec logs segmentés, couleurs d'état, lazy loading, et gestion du stop-job.
2. **Artefacts Actuels** : La vue transversale des fichiers DVC récents.

### C. Arborescence Pliable & Filtrage Bottom-Up
- **Reconstruction d'arbre** : Les artefacts étant renvoyés sous forme de liste de chemins à plat (ex: `data/models/weights.pt`), le frontend reconstruit dynamiquement en JavaScript un arbre imbriqué de dossiers et de fichiers (`buildTreeFromPaths`).
- **Pliage interactif** : Clic sur un dossier pour le replier/déplier avec mise à jour des chevrons.
- **Filtrage Intelligent** : Un champ de recherche instantané filtre les fichiers à la saisie. Si un fichier correspond, ses dossiers parents sont automatiquement dépliés et affichés, tandis que les dossiers ne contenant aucun résultat sont masqués (filtrage bottom-up récursif).

### D. Visualiseur de Versions Physiques Uniques
Lors du clic sur un artefact dans l'arbre :
1. Un modal d'aperçu moderne s'ouvre (gérant les images, fichiers CSV tabulés et fichiers textes tronqués à 100 lignes pour des raisons de performance).
2. L'historique physique de l'artefact est récupéré et **regroupé par hash MD5 unique** afin d'éliminer les doublons physiques (les runs ayant tourné sans modifier l'artefact n'apparaissent pas comme de nouvelles versions physiques).
3. Un sélecteur dynamique en Français liste ces versions uniques chronologiquement avec leur date, taille et commit.
4. Des boutons directionnels "Plus ancienne" / "Plus récente" permettent une navigation rapide et fluide.
5. Un bouton **"Consulter le Run"** permet de fermer instantanément la prévisualisation et d'ouvrir directement le modal de logs segmentés de l'exécution ayant produit cette version physique de l'artefact.
