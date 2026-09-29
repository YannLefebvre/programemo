from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import UtilisateurSession, exiger_enseignant, exiger_enseignant_administrateur
from app.grist_client import grist
from app.grist_types import date_vers_ts, ts_vers_date
from app.config import settings
from app.models import (
    ChangerMonMotDePasseIn,
    ClasseOut,
    ComparaisonPalier,
    EnseignantOut,
    GenerationMotDePasseEnseignantOut,
    GenerationMotDePasseOut,
    MotDePasseOut,
    NotionIn,
    NotionOut,
    NotionUpdateIn,
    QCMOptionOutProf,
    QuestionIn,
    QuestionOutProf,
    QuestionUpdateIn,
    ResultatLigne,
)
from app.security import generer_mot_de_passe, hacher_mot_de_passe

router = APIRouter(prefix="/prof", tags=["prof"])


async def _verifier_rattachement_classe(enseignant_id: int, classe_id: int) -> None:
    """Revérifie côté back-end le rattachement enseignant/classe — ne se
    repose pas uniquement sur les Access Rules Grist, puisque c'est le
    compte de service (Owner, jamais filtré par ces règles) qui exécute
    la requête réelle. Cf. spécifications back-end, section 3.3."""
    liens = await grist.list_records(
        "Enseignants_Classes",
        filter_={"Enseignant": [enseignant_id], "Classe": [classe_id]},
    )
    if not liens:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Vous n'êtes pas rattaché à cette classe."
        )


@router.get("/mes-classes", response_model=list[ClasseOut])
async def mes_classes(
    utilisateur: UtilisateurSession = Depends(exiger_enseignant),
) -> list[ClasseOut]:
    """Liste les classes auxquelles l'enseignant connecté est rattaché —
    évite d'avoir à connaître et saisir manuellement un id de classe côté
    front."""
    liens = await grist.list_records(
        "Enseignants_Classes", filter_={"Enseignant": [utilisateur.id]}
    )
    classes = {c["id"]: c for c in await grist.list_records("Classes")}
    return [
        ClasseOut(
            id=l["Classe"],
            # str(...) défensif : la colonne Nom peut être un nombre côté
            # Grist (ex. "1", "2", "3" comme identifiants de classe plutôt
            # que des libellés texte) — ClasseOut.nom attend une chaîne, on
            # ne veut pas planter toute la liste pour un souci d'affichage.
            nom=str(classes.get(l["Classe"], {}).get("Nom", "?")),
            role=l.get("Role", "?"),
        )
        for l in liens
        if l["Classe"] in classes
    ]


@router.get("/classes/{classe_id}/notions", response_model=list[NotionOut])
async def lister_notions(
    classe_id: int,
    utilisateur: UtilisateurSession = Depends(exiger_enseignant),
) -> list[NotionOut]:
    await _verifier_rattachement_classe(utilisateur.id, classe_id)

    notions = await grist.list_records("Notions", filter_={"Classe": [classe_id]})
    enseignants = {e["id"]: e for e in await grist.list_records("Enseignants")}

    return [
        NotionOut(
            id=n["id"],
            date_jour=ts_vers_date(n["Date_jour"]),
            titre=n["Titre"],
            enseignant_nom=enseignants.get(n["Enseignant"], {}).get("Nom", "?"),
        )
        for n in notions
    ]


@router.post("/classes/{classe_id}/notions", status_code=status.HTTP_201_CREATED)
async def creer_notion(
    classe_id: int,
    payload: NotionIn,
    utilisateur: UtilisateurSession = Depends(exiger_enseignant),
) -> dict:
    await _verifier_rattachement_classe(utilisateur.id, classe_id)

    ids = await grist.add_records(
        "Notions",
        [
            {
                "Date_jour": date_vers_ts(payload.date_jour),
                "Classe": classe_id,
                "Titre": payload.titre,
                "Enseignant": utilisateur.id,  # l'auteur = l'enseignant connecté, jamais transmis par le client
            }
        ],
    )
    return {"id": ids[0]}


@router.get("/notions/{notion_id}/questions", response_model=list[QuestionOutProf])
async def lister_questions_notion(
    notion_id: int,
    utilisateur: UtilisateurSession = Depends(exiger_enseignant),
) -> list[QuestionOutProf]:
    notions = await grist.list_records("Notions", filter_={"id": [notion_id]})
    if not notions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notion introuvable.")
    await _verifier_rattachement_classe(utilisateur.id, notions[0]["Classe"])

    questions = await grist.list_records("Questions", filter_={"Notion": [notion_id]})
    resultat = []
    for q in sorted(questions, key=lambda x: x["Ordre"]):
        options_brutes = await grist.list_records("QCM_Options", filter_={"Question": [q["id"]]})
        resultat.append(QuestionOutProf(
            id=q["id"], ordre=q["Ordre"], enonce=q["Enonce"],
            reponse_attendue=q.get("Reponse_attendue", ""), format=q.get("Format", "natif"),
            contenu_h5p_url=q.get("Contenu_h5p_url"),
            options=[QCMOptionOutProf(id=o["id"], texte_option=o["Texte_option"], est_correcte=bool(o["Est_correcte"])) for o in options_brutes],
        ))
    return resultat


@router.patch("/notions/{notion_id}")
async def modifier_notion(
    notion_id: int,
    payload: NotionUpdateIn,
    utilisateur: UtilisateurSession = Depends(exiger_enseignant),
) -> dict:
    notions = await grist.list_records("Notions", filter_={"id": [notion_id]})
    if not notions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notion introuvable.")
    await _verifier_rattachement_classe(utilisateur.id, notions[0]["Classe"])

    champs: dict = {"id": notion_id}
    if payload.titre is not None:
        champs["Titre"] = payload.titre
    if payload.date_jour is not None:
        champs["Date_jour"] = date_vers_ts(payload.date_jour)
    if len(champs) > 1:  # au moins un vrai champ à modifier, en plus de "id"
        await grist.update_records("Notions", [champs])
    return {"statut": "modifie"}


@router.post("/notions/{notion_id}/questions", status_code=status.HTTP_201_CREATED)
async def ajouter_question(
    notion_id: int,
    payload: QuestionIn,
    utilisateur: UtilisateurSession = Depends(exiger_enseignant),
) -> dict:
    notions = await grist.list_records("Notions", filter_={"id": [notion_id]})
    if not notions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notion introuvable.")
    await _verifier_rattachement_classe(utilisateur.id, notions[0]["Classe"])

    champs_question: dict = {
        "Notion": notion_id,
        "Ordre": payload.ordre,
        "Enonce": payload.enonce,
        "Reponse_attendue": payload.reponse_attendue,
        "Format": payload.format,
    }
    # On n'envoie ce champ que s'il a une vraie valeur : une colonne
    # optionnelle manquante ou mal nommée côté Grist (piège déjà rencontré
    # avec Mot_de_passe_hash) ferait échouer TOUTE la requête, y compris
    # les champs obligatoires, si on l'envoyait systématiquement même à None.
    if payload.contenu_h5p_url:
        champs_question["Contenu_h5p_url"] = payload.contenu_h5p_url

    question_ids = await grist.add_records("Questions", [champs_question])
    question_id = question_ids[0]

    if payload.options_qcm:
        await grist.add_records(
            "QCM_Options",
            [
                {
                    "Question": question_id,
                    "Texte_option": texte,
                    "Est_correcte": (i == payload.index_bonne_option),
                }
                for i, texte in enumerate(payload.options_qcm)
            ],
        )

    return {"id": question_id}


@router.patch("/questions/{question_id}")
async def modifier_question(
    question_id: int,
    payload: QuestionUpdateIn,
    utilisateur: UtilisateurSession = Depends(exiger_enseignant),
) -> dict:
    questions = await grist.list_records("Questions", filter_={"id": [question_id]})
    if not questions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question introuvable.")
    question = questions[0]
    notions = await grist.list_records("Notions", filter_={"id": [question["Notion"]]})
    if not notions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notion associée introuvable.")
    await _verifier_rattachement_classe(utilisateur.id, notions[0]["Classe"])

    champs: dict = {"id": question_id}
    for champ_payload, champ_grist in [
        ("ordre", "Ordre"), ("enonce", "Enonce"),
        ("reponse_attendue", "Reponse_attendue"), ("format", "Format"),
    ]:
        valeur = getattr(payload, champ_payload)
        if valeur is not None:
            champs[champ_grist] = valeur
    if payload.contenu_h5p_url:
        champs["Contenu_h5p_url"] = payload.contenu_h5p_url
    if len(champs) > 1:
        await grist.update_records("Questions", [champs])

    if payload.options_qcm is not None:
        # Remplacement complet plutôt qu'une fusion fine option par option :
        # plus simple et plus robuste, au prix de perdre l'historique des
        # anciens id d'options (sans conséquence, elles ne sont référencées
        # que par leurs propres réponses passées, qui gardent leur propre id).
        anciennes = await grist.list_records("QCM_Options", filter_={"Question": [question_id]})
        await grist.delete_records("QCM_Options", [o["id"] for o in anciennes])
        if payload.options_qcm:
            await grist.add_records(
                "QCM_Options",
                [
                    {
                        "Question": question_id,
                        "Texte_option": texte,
                        "Est_correcte": (i == payload.index_bonne_option),
                    }
                    for i, texte in enumerate(payload.options_qcm)
                ],
            )

    return {"statut": "modifie"}


@router.get("/classes/{classe_id}/resultats", response_model=list[ResultatLigne])
async def resultats(
    classe_id: int,
    notion_id: int,
    utilisateur: UtilisateurSession = Depends(exiger_enseignant),
) -> list[ResultatLigne]:
    await _verifier_rattachement_classe(utilisateur.id, classe_id)

    questions = await grist.list_records("Questions", filter_={"Notion": [notion_id]})
    question_ids = [q["id"] for q in questions]
    eleves = {e["id"]: e for e in await grist.list_records("Eleves", filter_={"Classe": [classe_id]})}

    reponses_ouvertes = await grist.list_records("Reponses_Ouvertes")
    reponses_qcm = await grist.list_records("Reponses_QCM")

    par_eleve: dict[int, ResultatLigne] = {
        eid: ResultatLigne(eleve_nom=f"{e['Prenom']} {e['Nom']}")
        for eid, e in eleves.items()
    }

    for r in reponses_ouvertes:
        if r["Question"] in question_ids and r["Eleve"] in par_eleve:
            par_eleve[r["Eleve"]].reponse_ouverte = r["Texte_reponse"]

    scores: dict[int, list[bool]] = defaultdict(list)
    for r in reponses_qcm:
        if r["Question"] in question_ids and r["Eleve"] in par_eleve:
            scores[r["Eleve"]].append(bool(r["Est_correcte"]))

    for eid, resultats_qcm in scores.items():
        nb_correct = sum(resultats_qcm)
        par_eleve[eid].score_qcm = f"{nb_correct}/{len(resultats_qcm)}"

    return list(par_eleve.values())


@router.get("/classes/{classe_id}/comparaison", response_model=list[ComparaisonPalier])
async def comparaison_par_palier(
    classe_id: int,
    utilisateur: UtilisateurSession = Depends(exiger_enseignant),
) -> list[ComparaisonPalier]:
    await _verifier_rattachement_classe(utilisateur.id, classe_id)

    paliers = {p["id"]: p for p in await grist.list_records("Frequences")}

    # Reconstitue le périmètre de la classe demandée en remontant la chaîne
    # Question -> Notion -> Classe, plutôt que d'agréger toutes les réponses
    # QCM du document sans distinction de classe (bug V1 corrigé ici : deux
    # classes ne doivent jamais se mélanger dans cette vue).
    notions_classe = await grist.list_records("Notions", filter_={"Classe": [classe_id]})
    notion_ids = [n["id"] for n in notions_classe]
    if notion_ids:
        questions_classe = await grist.list_records("Questions", filter_={"Notion": notion_ids})
    else:
        questions_classe = []
    question_ids_classe = {q["id"] for q in questions_classe}

    reponses_qcm = await grist.list_records("Reponses_QCM")

    par_palier: dict[int, list[bool]] = defaultdict(list)
    for r in reponses_qcm:
        if r["Question"] not in question_ids_classe:
            continue
        par_palier[r["Palier"]].append(bool(r["Est_correcte"]))

    resultat = []
    for palier_id, valeurs in par_palier.items():
        palier = paliers.get(palier_id, {})
        resultat.append(
            (
                palier.get("Offset_jours_ouvres", 0),
                ComparaisonPalier(
                    palier_label=palier.get("Label", "?"),
                    taux_reussite_pct=round(100 * sum(valeurs) / len(valeurs), 1),
                    nb_reponses=len(valeurs),
                ),
            )
        )
    resultat.sort(key=lambda t: t[0])
    return [c for _, c in resultat]


@router.post(
    "/eleves/{eleve_id}/generer-mot-de-passe", response_model=GenerationMotDePasseOut
)
async def generer_mot_de_passe_eleve(
    eleve_id: int,
    utilisateur: UtilisateurSession = Depends(exiger_enseignant),
) -> GenerationMotDePasseOut:
    """Cf. décision actée dans les spécifications : le mot de passe est
    généré ici, affiché UNE SEULE FOIS dans la réponse, et seul son hash
    est conservé dans Grist ensuite."""
    eleves = await grist.list_records("Eleves", filter_={"id": [eleve_id]})
    if not eleves:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Élève introuvable.")
    eleve = eleves[0]
    await _verifier_rattachement_classe(utilisateur.id, eleve["Classe"])

    mot_de_passe_clair = generer_mot_de_passe()
    await grist.update_records(
        "Eleves",
        [{"id": eleve_id, settings.colonne_hash_eleves: hacher_mot_de_passe(mot_de_passe_clair)}],
    )

    return GenerationMotDePasseOut(
        eleve_id=eleve_id,
        identifiant_connexion=eleve["Identifiant_connexion"],
        mot_de_passe_clair=mot_de_passe_clair,
    )


@router.post(
    "/classes/{classe_id}/generer-mots-de-passe",
    response_model=list[GenerationMotDePasseOut],
)
async def generer_mots_de_passe_classe(
    classe_id: int,
    utilisateur: UtilisateurSession = Depends(exiger_enseignant),
) -> list[GenerationMotDePasseOut]:
    """Génère en une fois un nouveau mot de passe pour CHAQUE élève de la
    classe, même logique que l'endpoint élève par élève : chaque mot de
    passe en clair n'est renvoyé qu'ici, une seule fois, seul son hash est
    conservé ensuite dans Grist."""
    await _verifier_rattachement_classe(utilisateur.id, classe_id)

    eleves = await grist.list_records("Eleves", filter_={"Classe": [classe_id]})

    resultats: list[GenerationMotDePasseOut] = []
    mises_a_jour: list[dict] = []
    for eleve in eleves:
        mot_de_passe_clair = generer_mot_de_passe()
        mises_a_jour.append(
            {"id": eleve["id"], settings.colonne_hash_eleves: hacher_mot_de_passe(mot_de_passe_clair)}
        )
        resultats.append(
            GenerationMotDePasseOut(
                eleve_id=eleve["id"],
                identifiant_connexion=eleve["Identifiant_connexion"],
                mot_de_passe_clair=mot_de_passe_clair,
            )
        )

    if mises_a_jour:
        await grist.update_records("Eleves", mises_a_jour)

    return resultats


LONGUEUR_MIN_MOT_DE_PASSE = 4


@router.post("/mon-mot-de-passe", response_model=MotDePasseOut)
async def changer_mon_mot_de_passe(
    payload: ChangerMonMotDePasseIn,
    utilisateur: UtilisateurSession = Depends(exiger_enseignant),
) -> MotDePasseOut:
    """Self-service : l'enseignant connecté génère ou choisit lui-même son
    propre mot de passe, sans passer par la manipulation manuelle (hash en
    REPL Python + collage dans Grist) utilisée jusqu'ici. L'id modifié est
    TOUJOURS `utilisateur.id`, jamais un id transmis par le client — même
    principe de sécurité que partout ailleurs dans ce fichier."""
    enseignants = await grist.list_records("Enseignants", filter_={"id": [utilisateur.id]})
    if not enseignants:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Enseignant introuvable.")
    enseignant = enseignants[0]

    if payload.nouveau_mot_de_passe:
        if len(payload.nouveau_mot_de_passe) < LONGUEUR_MIN_MOT_DE_PASSE:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Le mot de passe doit comporter au moins {LONGUEUR_MIN_MOT_DE_PASSE} caractères.",
            )
        mot_de_passe_clair = payload.nouveau_mot_de_passe
    else:
        mot_de_passe_clair = generer_mot_de_passe()

    await grist.update_records(
        "Enseignants",
        [{"id": utilisateur.id, settings.colonne_hash_enseignants: hacher_mot_de_passe(mot_de_passe_clair)}],
    )

    return MotDePasseOut(
        identifiant_connexion=enseignant["Email"],
        mot_de_passe_clair=mot_de_passe_clair,
    )


# ---------------------------------------------------------------------------
# Administration — réservé aux enseignants marqués administrateurs
# (colonne Est_administrateur sur Enseignants). Pas de souci de
# confidentialité entre collègues au sein d'une même équipe pédagogique
# (décision actée) : un administrateur peut agir sur le compte de n'importe
# quel enseignant du document, sans passer par un rattachement de classe.
# ---------------------------------------------------------------------------
@router.get("/enseignants", response_model=list[EnseignantOut])
async def lister_enseignants(
    utilisateur: UtilisateurSession = Depends(exiger_enseignant_administrateur),
) -> list[EnseignantOut]:
    enseignants = await grist.list_records("Enseignants")
    return [
        EnseignantOut(
            id=e["id"],
            nom=e.get("Nom", "?"),
            email=e.get("Email", "?"),
            est_administrateur=bool(e.get(settings.colonne_administrateur_enseignants, False)),
        )
        for e in enseignants
    ]


@router.post(
    "/enseignants/{enseignant_id}/generer-mot-de-passe",
    response_model=GenerationMotDePasseEnseignantOut,
)
async def generer_mot_de_passe_enseignant(
    enseignant_id: int,
    utilisateur: UtilisateurSession = Depends(exiger_enseignant_administrateur),
) -> GenerationMotDePasseEnseignantOut:
    """Équivalent, pour un collègue enseignant, de
    /prof/eleves/{eleve_id}/generer-mot-de-passe — réservé aux
    administrateurs, cf. note ci-dessus."""
    enseignants = await grist.list_records("Enseignants", filter_={"id": [enseignant_id]})
    if not enseignants:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Enseignant introuvable.")
    enseignant = enseignants[0]

    mot_de_passe_clair = generer_mot_de_passe()
    await grist.update_records(
        "Enseignants",
        [{"id": enseignant_id, settings.colonne_hash_enseignants: hacher_mot_de_passe(mot_de_passe_clair)}],
    )

    return GenerationMotDePasseEnseignantOut(
        enseignant_id=enseignant_id,
        identifiant_connexion=enseignant["Email"],
        mot_de_passe_clair=mot_de_passe_clair,
    )
