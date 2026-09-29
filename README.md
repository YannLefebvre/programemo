# PrograMemo — back-end

Couche applicative entre le front élève/professeur et Grist. Voir
`specifications_backend_programemo.md` pour le détail des choix
d'architecture ; ce dépôt en est l'implémentation de référence.

## Démarrage local

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# éditez .env avec vos propres identifiants Grist

uvicorn app.main:app --reload
```

L'API est alors disponible sur `http://localhost:8000`, avec une
documentation interactive auto-générée sur `http://localhost:8000/docs`.

## Tests

```bash
pytest tests/ -v
```

Les tests utilisent un client Grist **simulé en mémoire** (`GristClientSimule`
dans `tests/test_flux_eleve.py`) — aucun appel réseau réel vers Grist n'est
nécessaire pour les faire tourner. Ils couvrent en particulier les deux
points de sécurité les plus sensibles du projet :
- qu'aucune réponse d'API envoyée à un élève ne contient jamais le champ
  `est_correcte` avant qu'il n'ait répondu ;
- qu'un élève ne peut pas répondre à une question qui n'appartient pas à
  sa propre classe, même en forgeant directement une requête.

## Structure du projet

```
app/
  config.py        variables d'environnement (Settings)
  grist_client.py  UNIQUE point de passage vers l'API Grist
  security.py      hachage de mot de passe (argon2) + JWT
  dependencies.py  authentification par requête (utilisateur_courant, exiger_eleve, exiger_enseignant)
  cache.py         cache TTL mémoire pour /eleve/reprises-du-jour
  models.py        schémas Pydantic des requêtes/réponses
  routers/
    auth.py        POST /auth/login
    eleve.py        reprises du jour (+ rattrapage des retards), envoi des réponses, mode entraînement
    prof.py          planning, résultats, comparaison, mots de passe (élèves, self-service enseignant, administration)
tests/
  test_flux_eleve.py
```

## Déploiement (LaForgeEdu + Scalingo)

Le dépôt est prêt à être poussé tel quel : `Procfile`, `runtime.txt`,
`.gitlab-ci.yml` et `programemo_app.html` (le front) sont déjà à la racine.
Le pipeline CI/CD (`.gitlab-ci.yml`) lance les tests, déploie l'API sur
Scalingo par SSH, et publie le front en GitLab Pages à chaque push sur `main`.

Marche à suivre complète : voir `deploiement_laforge_scalingo.md`.

## Ce qui reste à faire avant une mise en production

- Créer réellement le document Grist de production à partir de
  `structure_tables_grist_programemo.md`, avec ses Access Rules
  (`access_rules_grist_programemo.md`), et y importer le calendrier
  (`generer_calendrier_grist.py`).
- Renseigner les variables d'environnement de production sur Scalingo (clé
  API Grist, secret JWT généré aléatoirement — jamais la valeur d'exemple),
  cf. `deploiement_laforge_scalingo.md` section 4.
- Une fois l'URL du front connue (GitLab Pages), resserrer
  `PROGRAMEMO_CORS_ALLOW_ORIGINS` à cette URL au lieu de `"*"`.
