"""
Tests avec un GristClient simulé en mémoire : valide la LOGIQUE applicative
(sécurité, calcul des reprises du jour, masquage de Est_correcte) sans
dépendre d'un vrai document Grist. C'est ce niveau de test qui a de la
valeur ici — pas un simple test de démarrage du serveur.
"""
import os
from datetime import date

os.environ["PROGRAMEMO_JWT_SECRET"] = "test-secret"

import pytest
from fastapi.testclient import TestClient

from app import grist_client
from app.cache import cache_reprises
from app.grist_types import date_vers_ts
from app.main import app
from app.security import hacher_mot_de_passe


class GristClientSimule:
    """Reproduit juste assez de comportement de GristClient pour les tests,
    en mémoire, avec des id auto-incrémentés par table."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self._next_id: dict[str, int] = {}

    async def list_records(self, table, filter_=None):
        rows = self.tables.get(table, [])
        if not filter_:
            return list(rows)
        out = []
        for r in rows:
            if all(r.get(k) in v for k, v in filter_.items()):
                out.append(r)
        return out

    async def add_records(self, table, records):
        self.tables.setdefault(table, [])
        self._next_id.setdefault(table, 1)
        ids = []
        for rec in records:
            new_id = self._next_id[table]
            self._next_id[table] += 1
            row = {"id": new_id, **rec}
            self.tables[table].append(row)
            ids.append(new_id)
        return ids

    async def update_records(self, table, records):
        for maj in records:
            for row in self.tables.get(table, []):
                if row["id"] == maj["id"]:
                    row.update({k: v for k, v in maj.items() if k != "id"})

    async def delete_records(self, table, ids):
        self.tables[table] = [r for r in self.tables.get(table, []) if r["id"] not in ids]


@pytest.fixture
def client():
    simule = GristClientSimule()
    grist_client.grist = simule  # remplace l'instance module-level

    # ré-injecte l'instance simulée dans les routers déjà importés
    from app.routers import auth, eleve, prof

    auth.grist = simule
    eleve.grist = simule
    prof.grist = simule

    # Le cache /eleve/reprises-du-jour est un singleton en mémoire au niveau
    # du module : sans ce nettoyage, un test peut lire le résultat mis en
    # cache par un test précédent (même classe/élève/jour) au lieu de
    # recalculer sur le scénario Grist simulé fraîchement peuplé ci-dessous.
    cache_reprises._store.clear()

    return TestClient(app), simule


def _peupler_scenario_minimal(simule: GristClientSimule):
    """Une classe, un enseignant, un élève, une notion reprise aujourd'hui,
    une question ouverte + une question QCM."""
    simule.tables["Classes"] = [{"id": 1, "Nom": "3e B"}]
    simule.tables["Enseignants"] = [{"id": 1, "Nom": "Mme Rossi", "Email": "rossi@test.fr",
                                      "Mot_de_passe_hash": hacher_mot_de_passe("profpass")}]
    simule.tables["Enseignants_Classes"] = [{"id": 1, "Enseignant": 1, "Classe": 1}]
    simule.tables["Eleves"] = [{"id": 1, "Nom": "Dupont", "Prenom": "Léa", "Classe": 1,
                                 "Identifiant_connexion": "lea.dupont",
                                 "Mot_de_passe_hash": hacher_mot_de_passe("elevepass")}]
    simule.tables["Frequences"] = [
        {"id": 1, "Label": "J-0", "Offset_jours_ouvres": 0},
        {"id": 2, "Label": "J-1", "Offset_jours_ouvres": 1},
    ]
    aujourdhui = date.today()
    simule.tables["Notions"] = [{
        "id": 1, "Classe": 1, "Titre": "La Révolution française",
        "Date_jour": date_vers_ts(aujourdhui),
        "Dates_de_reprise": [date_vers_ts(aujourdhui)],
        "Paliers_applicables": [1],
    }]
    simule.tables["Questions"] = [{
        "id": 1, "Notion": 1, "Ordre": 1, "Enonce": "En quelle année ?",
        "Reponse_attendue": "1789", "Format": "natif", "Contenu_h5p_url": None,
    }]
    simule.tables["QCM_Options"] = [
        {"id": 1, "Question": 1, "Texte_option": "1789", "Est_correcte": True},
        {"id": 2, "Question": 1, "Texte_option": "1792", "Est_correcte": False},
    ]
    simule.tables["Reponses_Ouvertes"] = []
    simule.tables["Reponses_QCM"] = []


def test_login_eleve_ok(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    r = app_client.post("/auth/login", json={"identifiant": "lea.dupont", "mot_de_passe": "elevepass"})
    assert r.status_code == 200
    assert r.json()["role"] == "eleve"


def test_login_mauvais_mot_de_passe(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    r = app_client.post("/auth/login", json={"identifiant": "lea.dupont", "mot_de_passe": "faux"})
    assert r.status_code == 401


def test_reprises_du_jour_masque_est_correcte(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    login = app_client.post("/auth/login", json={"identifiant": "lea.dupont", "mot_de_passe": "elevepass"})
    jeton = login.json()["access_token"]

    r = app_client.get("/eleve/reprises-du-jour", headers={"Authorization": f"Bearer {jeton}"})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["titre"] == "La Révolution française"

    options = data[0]["questions"][0]["options_qcm"]
    assert len(options) == 2
    # Le point de sécurité central : aucune trace de "est_correcte" nulle part.
    for option in options:
        assert "est_correcte" not in option


def test_envoi_reponse_qcm_correcte(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    login = app_client.post("/auth/login", json={"identifiant": "lea.dupont", "mot_de_passe": "elevepass"})
    jeton = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {jeton}"}

    r = app_client.post(
        "/eleve/reponses/qcm",
        json={"question_id": 1, "palier_id": 1, "option_choisie_id": 1},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["est_correcte"] is True
    assert len(simule.tables["Reponses_QCM"]) == 1
    assert simule.tables["Reponses_QCM"][0]["Eleve"] == 1


def test_eleve_ne_peut_pas_repondre_pour_une_autre_classe(client):
    """Vérifie la règle de sécurité centrale : une question qui n'appartient
    pas à la classe de l'élève connecté doit être refusée."""
    app_client, simule = client
    _peupler_scenario_minimal(simule)
    # deuxième classe + question hors du périmètre de l'élève de test
    simule.tables["Classes"].append({"id": 2, "Nom": "5e A"})
    simule.tables["Notions"].append({
        "id": 2, "Classe": 2, "Titre": "Autre notion",
        "Dates_de_reprise": [], "Paliers_applicables": [],
    })
    simule.tables["Questions"].append({
        "id": 2, "Notion": 2, "Ordre": 1, "Enonce": "Question d'une autre classe",
        "Reponse_attendue": "x", "Format": "natif", "Contenu_h5p_url": None,
    })

    login = app_client.post("/auth/login", json={"identifiant": "lea.dupont", "mot_de_passe": "elevepass"})
    jeton = login.json()["access_token"]

    r = app_client.post(
        "/eleve/reponses/ouverte",
        json={"question_id": 2, "palier_id": 1, "texte_reponse": "tentative"},
        headers={"Authorization": f"Bearer {jeton}"},
    )
    assert r.status_code == 403


def test_reprises_du_jour_ignore_une_notion_dont_la_reprise_est_dans_le_futur(client):
    """Vérifie que la conversion timestamp <-> date fonctionne dans les deux
    sens : une notion dont la seule date de reprise est dans le FUTUR ne doit
    pas apparaître (contrairement à une date passée, désormais proposée en
    rattrapage — cf. test_reprises_du_jour_inclut_une_reprise_en_retard_non_faite)."""
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    from datetime import timedelta
    demain = date.today() + timedelta(days=1)
    simule.tables["Notions"].append({
        "id": 2, "Classe": 1, "Titre": "Notion reprise demain, pas encore due",
        "Dates_de_reprise": [date_vers_ts(demain)],
        "Paliers_applicables": [1],
    })

    login = app_client.post("/auth/login", json={"identifiant": "lea.dupont", "mot_de_passe": "elevepass"})
    jeton = login.json()["access_token"]

    r = app_client.get("/eleve/reprises-du-jour", headers={"Authorization": f"Bearer {jeton}"})
    titres = [n["titre"] for n in r.json()]
    assert "La Révolution française" in titres
    assert "Notion reprise demain, pas encore due" not in titres


def test_reprises_du_jour_inclut_une_reprise_en_retard_non_faite(client):
    """Une reprise dont la date est déjà passée, et jamais faite, doit être
    proposée en rattrapage plutôt que perdue (demande explicite de l'utilisateur)."""
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    from datetime import timedelta
    hier = date.today() - timedelta(days=1)
    simule.tables["Notions"].append({
        "id": 2, "Classe": 1, "Titre": "Notion en retard, jamais faite",
        "Dates_de_reprise": [date_vers_ts(hier)],
        "Paliers_applicables": [1],
    })
    simule.tables["Questions"].append({
        "id": 2, "Notion": 2, "Ordre": 1, "Enonce": "Question en retard",
        "Reponse_attendue": "peu importe", "Format": "natif", "Contenu_h5p_url": None,
    })

    login = app_client.post("/auth/login", json={"identifiant": "lea.dupont", "mot_de_passe": "elevepass"})
    jeton = login.json()["access_token"]

    r = app_client.get("/eleve/reprises-du-jour", headers={"Authorization": f"Bearer {jeton}"})
    data = r.json()
    titres = {n["titre"]: n for n in data}
    assert "Notion en retard, jamais faite" in titres
    assert titres["Notion en retard, jamais faite"]["en_retard"] is True
    assert titres["La Révolution française"]["en_retard"] is False


def test_reprises_du_jour_exclut_une_reprise_en_retard_deja_faite(client):
    """Si l'élève a déjà répondu à cette reprise en retard, elle ne doit pas
    être re-proposée indéfiniment."""
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    from datetime import timedelta
    hier = date.today() - timedelta(days=1)
    simule.tables["Notions"].append({
        "id": 2, "Classe": 1, "Titre": "Notion en retard, déjà faite",
        "Dates_de_reprise": [date_vers_ts(hier)],
        "Paliers_applicables": [1],
    })
    simule.tables["Questions"].append({
        "id": 2, "Notion": 2, "Ordre": 1, "Enonce": "Question en retard",
        "Reponse_attendue": "peu importe", "Format": "natif", "Contenu_h5p_url": None,
    })
    # L'élève a déjà répondu à cette question pour ce palier.
    simule.tables["Reponses_Ouvertes"].append(
        {"id": 1, "Question": 2, "Eleve": 1, "Palier": 1, "Texte_reponse": "déjà fait"}
    )

    login = app_client.post("/auth/login", json={"identifiant": "lea.dupont", "mot_de_passe": "elevepass"})
    jeton = login.json()["access_token"]

    r = app_client.get("/eleve/reprises-du-jour", headers={"Authorization": f"Bearer {jeton}"})
    titres = [n["titre"] for n in r.json()]
    assert "Notion en retard, déjà faite" not in titres


def test_entrainement_liste_les_notions_passees(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    login = app_client.post("/auth/login", json={"identifiant": "lea.dupont", "mot_de_passe": "elevepass"})
    jeton = login.json()["access_token"]

    r = app_client.get("/eleve/entrainement", headers={"Authorization": f"Bearer {jeton}"})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["notion_id"] == 1


def test_entrainement_expose_la_reponse_attendue_et_masque_est_correcte(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    login = app_client.post("/auth/login", json={"identifiant": "lea.dupont", "mot_de_passe": "elevepass"})
    jeton = login.json()["access_token"]

    r = app_client.get("/eleve/entrainement/1", headers={"Authorization": f"Bearer {jeton}"})
    assert r.status_code == 200
    data = r.json()
    assert data["questions"][0]["reponse_attendue"] == "1789"
    for option in data["questions"][0]["options_qcm"]:
        assert "est_correcte" not in option


def test_entrainement_qcm_ne_persiste_aucune_reponse(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    login = app_client.post("/auth/login", json={"identifiant": "lea.dupont", "mot_de_passe": "elevepass"})
    jeton = login.json()["access_token"]

    r = app_client.post(
        "/eleve/entrainement/reponse-qcm",
        json={"question_id": 1, "option_choisie_id": 1},
        headers={"Authorization": f"Bearer {jeton}"},
    )
    assert r.status_code == 200
    assert r.json()["est_correcte"] is True
    assert simule.tables["Reponses_QCM"] == []  # rien n'est écrit, contrairement au flux officiel


def test_entrainement_refuse_une_notion_dune_autre_classe(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)
    simule.tables["Classes"].append({"id": 2, "Nom": "5e A"})
    simule.tables["Notions"].append({
        "id": 2, "Classe": 2, "Titre": "Notion d'une autre classe",
        "Dates_de_reprise": [], "Paliers_applicables": [],
    })

    login = app_client.post("/auth/login", json={"identifiant": "lea.dupont", "mot_de_passe": "elevepass"})
    jeton = login.json()["access_token"]

    r = app_client.get("/eleve/entrainement/2", headers={"Authorization": f"Bearer {jeton}"})
    assert r.status_code == 403


def test_parser_liste_dates_gere_le_format_texte_brut_grist():
    """Reproduit le format réellement observé contre un vrai document Grist :
    la colonne Dates_de_reprise, mal typée côté Grist, renvoie une chaîne
    unique contenant la représentation Python d'une liste de dates plutôt
    qu'une vraie liste JSON."""
    from app.grist_types import parser_liste_dates, parser_liste_ids

    brut = "[datetime.date(2026, 9, 3), datetime.date(2026, 9, 4), datetime.date(2026, 9, 9), datetime.date(2026, 9, 25)]"
    resultat = parser_liste_dates(brut)
    assert resultat == [
        date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 9), date(2026, 9, 25)
    ]

    # Forme "encodée" observée pour Paliers_applicables : ['L', 1, 2, 3, 4]
    assert parser_liste_ids(["L", 1, 2, 3, 4]) == [1, 2, 3, 4]
    # Doit aussi tolérer une vraie liste JSON simple, sans le tag 'L'
    assert parser_liste_ids([1, 2, 3, 4]) == [1, 2, 3, 4]


def test_prof_liste_questions_dune_notion_avec_options(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    login = app_client.post("/auth/login", json={"identifiant": "rossi@test.fr", "mot_de_passe": "profpass"})
    jeton = login.json()["access_token"]

    r = app_client.get("/prof/notions/1/questions", headers={"Authorization": f"Bearer {jeton}"})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert len(data[0]["options"]) == 2
    # Côté prof, contrairement à la vue élève, est_correcte est bien visible
    assert any(o["est_correcte"] for o in data[0]["options"])


def test_prof_liste_ses_classes(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    login = app_client.post("/auth/login", json={"identifiant": "rossi@test.fr", "mot_de_passe": "profpass"})
    jeton = login.json()["access_token"]

    r = app_client.get("/prof/mes-classes", headers={"Authorization": f"Bearer {jeton}"})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["id"] == 1
    assert data[0]["nom"] == "3e B"


def test_prof_liste_ses_classes_tolere_un_nom_numerique(client):
    """Reproduit le bug réel rencontré côté utilisateur : la colonne Nom de
    Classes contenait un nombre (1, 2, 3...) plutôt qu'un texte, ce qui
    faisait planter la validation Pydantic de ClasseOut (nom: str) avec un
    entier brut en entrée."""
    app_client, simule = client
    _peupler_scenario_minimal(simule)
    simule.tables["Classes"][0]["Nom"] = 1  # entier brut, pas une chaîne

    login = app_client.post("/auth/login", json={"identifiant": "rossi@test.fr", "mot_de_passe": "profpass"})
    jeton = login.json()["access_token"]

    r = app_client.get("/prof/mes-classes", headers={"Authorization": f"Bearer {jeton}"})
    assert r.status_code == 200
    assert r.json()[0]["nom"] == "1"


def test_prof_modifie_une_notion(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    login = app_client.post("/auth/login", json={"identifiant": "rossi@test.fr", "mot_de_passe": "profpass"})
    jeton = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {jeton}"}

    r = app_client.patch("/prof/notions/1", json={"titre": "Titre corrigé"}, headers=headers)
    assert r.status_code == 200
    assert simule.tables["Notions"][0]["Titre"] == "Titre corrigé"


def test_prof_remplace_les_options_qcm_dune_question(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    login = app_client.post("/auth/login", json={"identifiant": "rossi@test.fr", "mot_de_passe": "profpass"})
    jeton = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {jeton}"}

    assert len(simule.tables["QCM_Options"]) == 2  # état initial du scénario

    r = app_client.patch(
        "/prof/questions/1",
        json={"enonce": "Nouvel énoncé", "options_qcm": ["A", "B", "C"], "index_bonne_option": 2},
        headers=headers,
    )
    assert r.status_code == 200
    assert simule.tables["Questions"][0]["Enonce"] == "Nouvel énoncé"
    options = simule.tables["QCM_Options"]
    assert len(options) == 3  # les 2 anciennes ont bien été remplacées, pas cumulées
    assert options[2]["Texte_option"] == "C"
    assert options[2]["Est_correcte"] is True


def test_prof_genere_mot_de_passe_et_hash_stocke(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    login = app_client.post("/auth/login", json={"identifiant": "rossi@test.fr", "mot_de_passe": "profpass"})
    jeton = login.json()["access_token"]

    r = app_client.post(
        "/prof/eleves/1/generer-mot-de-passe",
        headers={"Authorization": f"Bearer {jeton}"},
    )
    assert r.status_code == 200
    nouveau_mdp = r.json()["mot_de_passe_clair"]
    assert len(nouveau_mdp) == 6

    # Le mot de passe en clair n'est jamais stocké : seule sa forme hachée existe.
    eleve_maj = simule.tables["Eleves"][0]
    assert eleve_maj["Mot_de_passe_hash"] != nouveau_mdp
    from app.security import verifier_mot_de_passe
    assert verifier_mot_de_passe(nouveau_mdp, eleve_maj["Mot_de_passe_hash"])


def test_prof_genere_mots_de_passe_en_masse_pour_toute_la_classe(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)
    # deuxième élève dans la même classe, pour vérifier que TOUS sont traités
    simule.tables["Eleves"].append({
        "id": 2, "Nom": "Martin", "Prenom": "Noé", "Classe": 1,
        "Identifiant_connexion": "noe.martin",
        "Mot_de_passe_hash": hacher_mot_de_passe("ancien"),
    })

    login = app_client.post("/auth/login", json={"identifiant": "rossi@test.fr", "mot_de_passe": "profpass"})
    jeton = login.json()["access_token"]

    r = app_client.post(
        "/prof/classes/1/generer-mots-de-passe",
        headers={"Authorization": f"Bearer {jeton}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    identifiants = {d["identifiant_connexion"] for d in data}
    assert identifiants == {"lea.dupont", "noe.martin"}

    from app.security import verifier_mot_de_passe
    for d in data:
        eleve = next(e for e in simule.tables["Eleves"] if e["id"] == d["eleve_id"])
        assert verifier_mot_de_passe(d["mot_de_passe_clair"], eleve["Mot_de_passe_hash"])


def test_prof_ne_peut_pas_generer_en_masse_pour_une_classe_non_rattachee(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)
    simule.tables["Classes"].append({"id": 2, "Nom": "5e A"})

    login = app_client.post("/auth/login", json={"identifiant": "rossi@test.fr", "mot_de_passe": "profpass"})
    jeton = login.json()["access_token"]

    r = app_client.post(
        "/prof/classes/2/generer-mots-de-passe",
        headers={"Authorization": f"Bearer {jeton}"},
    )
    assert r.status_code == 403


def test_prof_change_son_propre_mot_de_passe_genere(client):
    """Endpoint self-service : sans mot de passe fourni, un mot de passe est
    généré automatiquement (même logique que pour un élève)."""
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    login = app_client.post("/auth/login", json={"identifiant": "rossi@test.fr", "mot_de_passe": "profpass"})
    jeton = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {jeton}"}

    r = app_client.post("/prof/mon-mot-de-passe", json={}, headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["identifiant_connexion"] == "rossi@test.fr"
    nouveau_mdp = data["mot_de_passe_clair"]
    assert len(nouveau_mdp) == 6

    from app.security import verifier_mot_de_passe
    enseignant_maj = simule.tables["Enseignants"][0]
    # L'ancien mot de passe ne fonctionne plus, le nouveau si.
    assert not verifier_mot_de_passe("profpass", enseignant_maj["Mot_de_passe_hash"])
    assert verifier_mot_de_passe(nouveau_mdp, enseignant_maj["Mot_de_passe_hash"])

    # Et la connexion avec le nouveau mot de passe fonctionne bien.
    login2 = app_client.post("/auth/login", json={"identifiant": "rossi@test.fr", "mot_de_passe": nouveau_mdp})
    assert login2.status_code == 200


def test_prof_choisit_son_propre_mot_de_passe(client):
    """L'enseignant peut aussi choisir lui-même son nouveau mot de passe."""
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    login = app_client.post("/auth/login", json={"identifiant": "rossi@test.fr", "mot_de_passe": "profpass"})
    jeton = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {jeton}"}

    r = app_client.post(
        "/prof/mon-mot-de-passe",
        json={"nouveau_mot_de_passe": "monchoix2026"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["mot_de_passe_clair"] == "monchoix2026"

    login2 = app_client.post("/auth/login", json={"identifiant": "rossi@test.fr", "mot_de_passe": "monchoix2026"})
    assert login2.status_code == 200


def test_prof_ne_peut_pas_choisir_un_mot_de_passe_trop_court(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    login = app_client.post("/auth/login", json={"identifiant": "rossi@test.fr", "mot_de_passe": "profpass"})
    jeton = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {jeton}"}

    r = app_client.post(
        "/prof/mon-mot-de-passe",
        json={"nouveau_mot_de_passe": "ab"},
        headers=headers,
    )
    assert r.status_code == 422

    # Et le mot de passe d'origine fonctionne toujours (rien n'a été modifié).
    login2 = app_client.post("/auth/login", json={"identifiant": "rossi@test.fr", "mot_de_passe": "profpass"})
    assert login2.status_code == 200


def test_comparaison_ne_mélange_pas_les_classes(client):
    """Reproduit le bug signalé en V1 : /prof/classes/{id}/comparaison
    agrégeait toutes les réponses QCM du document, sans vérifier qu'elles
    appartenaient bien à la classe demandée. Ici, une notion + question +
    réponse QCM d'une AUTRE classe (2) ne doit jamais apparaître dans la
    comparaison calculée pour la classe 1."""
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    # Deuxième classe, avec sa propre notion/question/réponse QCM, toutes
    # incorrectes — si elles fuitent dans le calcul de la classe 1 (qui n'a
    # que des réponses correctes), le taux de réussite de la classe 1 chutera.
    simule.tables["Classes"].append({"id": 2, "Nom": "5e A"})
    simule.tables["Notions"].append({
        "id": 2, "Classe": 2, "Titre": "Notion classe 2",
        "Dates_de_reprise": [], "Paliers_applicables": [],
    })
    simule.tables["Questions"].append({
        "id": 2, "Notion": 2, "Ordre": 1, "Enonce": "Question classe 2",
        "Reponse_attendue": "x", "Format": "natif", "Contenu_h5p_url": None,
    })
    simule.tables["Reponses_QCM"].append(
        {"id": 1, "Question": 2, "Eleve": 99, "Palier": 1, "Est_correcte": False}
    )
    # Une vraie réponse, correcte celle-ci, pour la classe 1 (Palier J-0, id 1).
    simule.tables["Reponses_QCM"].append(
        {"id": 2, "Question": 1, "Eleve": 1, "Palier": 1, "Est_correcte": True}
    )

    login = app_client.post("/auth/login", json={"identifiant": "rossi@test.fr", "mot_de_passe": "profpass"})
    jeton = login.json()["access_token"]

    r = app_client.get("/prof/classes/1/comparaison", headers={"Authorization": f"Bearer {jeton}"})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1  # un seul palier concerné, celui de la classe 1
    assert data[0]["nb_reponses"] == 1  # la réponse de la classe 2 est bien exclue
    assert data[0]["taux_reussite_pct"] == 100.0


def test_administrateur_liste_les_enseignants(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)
    simule.tables["Enseignants"][0]["Est_administrateur"] = True
    simule.tables["Enseignants"].append({
        "id": 2, "Nom": "M. Petit", "Email": "petit@test.fr",
        "Mot_de_passe_hash": hacher_mot_de_passe("petitpass"),
    })

    login = app_client.post("/auth/login", json={"identifiant": "rossi@test.fr", "mot_de_passe": "profpass"})
    assert login.json()["est_administrateur"] is True
    jeton = login.json()["access_token"]

    r = app_client.get("/prof/enseignants", headers={"Authorization": f"Bearer {jeton}"})
    assert r.status_code == 200
    emails = {e["email"] for e in r.json()}
    assert emails == {"rossi@test.fr", "petit@test.fr"}


def test_non_administrateur_ne_peut_pas_lister_les_enseignants(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)
    # Rossi n'est PAS administrateur ici (champ absent = False par défaut).
    simule.tables["Enseignants"].append({
        "id": 2, "Nom": "M. Petit", "Email": "petit@test.fr",
        "Mot_de_passe_hash": hacher_mot_de_passe("petitpass"),
    })

    login = app_client.post("/auth/login", json={"identifiant": "rossi@test.fr", "mot_de_passe": "profpass"})
    assert login.json()["est_administrateur"] is False
    jeton = login.json()["access_token"]

    r = app_client.get("/prof/enseignants", headers={"Authorization": f"Bearer {jeton}"})
    assert r.status_code == 403


def test_administrateur_genere_le_mot_de_passe_dun_collegue(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)
    simule.tables["Enseignants"][0]["Est_administrateur"] = True
    simule.tables["Enseignants"].append({
        "id": 2, "Nom": "M. Petit", "Email": "petit@test.fr",
        "Mot_de_passe_hash": hacher_mot_de_passe("petitpass"),
    })

    login = app_client.post("/auth/login", json={"identifiant": "rossi@test.fr", "mot_de_passe": "profpass"})
    jeton = login.json()["access_token"]

    r = app_client.post(
        "/prof/enseignants/2/generer-mot-de-passe",
        headers={"Authorization": f"Bearer {jeton}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["identifiant_connexion"] == "petit@test.fr"
    nouveau_mdp = data["mot_de_passe_clair"]

    # L'ancien mot de passe de Petit ne fonctionne plus, le nouveau si.
    login_petit = app_client.post("/auth/login", json={"identifiant": "petit@test.fr", "mot_de_passe": "petitpass"})
    assert login_petit.status_code == 401
    login_petit2 = app_client.post("/auth/login", json={"identifiant": "petit@test.fr", "mot_de_passe": nouveau_mdp})
    assert login_petit2.status_code == 200


def test_non_administrateur_ne_peut_pas_generer_le_mot_de_passe_dun_collegue(client):
    app_client, simule = client
    _peupler_scenario_minimal(simule)
    simule.tables["Enseignants"].append({
        "id": 2, "Nom": "M. Petit", "Email": "petit@test.fr",
        "Mot_de_passe_hash": hacher_mot_de_passe("petitpass"),
    })

    login = app_client.post("/auth/login", json={"identifiant": "rossi@test.fr", "mot_de_passe": "profpass"})
    jeton = login.json()["access_token"]

    r = app_client.post(
        "/prof/enseignants/2/generer-mot-de-passe",
        headers={"Authorization": f"Bearer {jeton}"},
    )
    assert r.status_code == 403
    # Le mot de passe de Petit n'a pas bougé.
    login_petit = app_client.post("/auth/login", json={"identifiant": "petit@test.fr", "mot_de_passe": "petitpass"})
    assert login_petit.status_code == 200


def test_eleve_ne_peut_pas_utiliser_lendpoint_prof(client):
    """Vérifie que l'endpoint reste bien réservé aux enseignants."""
    app_client, simule = client
    _peupler_scenario_minimal(simule)

    login = app_client.post("/auth/login", json={"identifiant": "lea.dupont", "mot_de_passe": "elevepass"})
    jeton = login.json()["access_token"]

    r = app_client.post(
        "/prof/mon-mot-de-passe",
        json={},
        headers={"Authorization": f"Bearer {jeton}"},
    )
    assert r.status_code == 403
