# Analyse des Risques de Sécurité : Architecture `cluster-run`

L'architecture actuelle de `cluster-run` (exécution de jobs via SSH et requêtes HTTP proxifiées) présente plusieurs failles de sécurité importantes inhérentes à son design "Proof of Concept". Voici une analyse détaillée :

## 1. Mots de passe en dur (Hardcoded Secrets)
- **Statut** : **Entièrement Résolu**. Tous les mots de passe et identifiants en dur ont été purgés de `cluster-run.sh`. 
- **Description** : L'accès SSH direct au Headnode et les requêtes SQLite locales ont été supprimés. Le client s'appuie désormais uniquement sur les API de GitHub (`gh`) pour récupérer de manière transparente et sécurisée les tunnels interactifs éphémères (via tmate) ou streamer les logs GHA classiques, sans aucun partage de secrets ou d'accès root.

## 2. Validation stricte des clés hôtes (`StrictHostKeyChecking=accept-new`)
- **Risque** : Par le passé, l'option `StrictHostKeyChecking=no` était utilisée, exposant le client aux attaques Man-in-the-Middle (MitM).
- **Statut** : **Corrigé**. Le paramètre est désormais configuré sur `accept-new`. Cela préserve l'ergonomie (acceptation automatique silencieuse lors de la toute première connexion sans bloquer les scripts automatisés) tout en empêchant strictement l'usurpation (MitM) lors des connexions suivantes.

## 3. Absence de contrôle d'accès individuel (RBAC) pour les Logs
- **Risque** : Tous les logs sont interrogés par le même compte `henri`.
- **Analyse Modérée** : L'exécution des jobs n'est pas déclenchée par SSH, mais par un push Git capté par **GitHub Actions**. C'est donc le système RBAC natif de GitHub qui authentifie l'auteur du code et assure la traçabilité des exécutions. L'accès SSH local via `cluster-run` n'a qu'un rôle en *lecture seule* pour streamer les logs.
- **Mitigation Future** : Exposer une API Web pour les logs, évitant le besoin de partager les identifiants SSH de lecture.

## 4. Vulnérabilité aux Injections SQL via SSH
- **Risque** : Le script exécute des requêtes SQLite via SSH en concaténant des variables : `sqlite3 ... "SELECT status FROM jobs WHERE job_id = '$job_id';"`.
- **Impact** : Modéré. Si la variable `$job_id` (bien qu'actuellement générée par le serveur) peut être manipulée ou injectée par un attaquant, cela peut mener à une injection SQL permettant d'altérer la base `cluster_scheduler.db`.
- **Mitigation** : Passer par un endpoint API dédié sur le Headnode au lieu de faire exécuter des commandes `sqlite3` brutes par le client SSH.

## 5. Exécution de code arbitraire dans les conteneurs (Container Breakout)
- **Risque** : Les jobs exécutent du code Python arbitraire soumis par les chercheurs via GitHub (Shadow Commits).
- **Analyse Modérée** : L'objectif de la plateforme est précisément d'offrir la liberté totale aux chercheurs d'installer des paquets et d'opérer avec des droits root **à l'intérieur** de leur conteneur.
- **Mitigation** : Grâce à l'isolation native de Docker (namespaces, cgroups, bridge réseau), ces privilèges internes ne débordent pas sur l'hôte. La seule exigence absolue est de **ne jamais lancer les conteneurs en `--privileged`** et de ne pas monter `/var/run/docker.sock`, garantissant qu'ils ne pourront ni accéder aux processus hôtes ni annuler les jobs de la CI ou des autres chercheurs.

---

> **Conclusion** : L'approche actuelle est excellente pour une équipe de confiance (prototypage rapide), mais elle n'est pas "Zero Trust". Pour une mise en production ouverte, il est impératif de remplacer les requêtes SSH encapsulées par des appels à une API REST sécurisée par HTTPS et authentifiée (Tokens).
