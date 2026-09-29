from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.cache import cache_reprises
from app.dependencies import UtilisateurSession, exiger_eleve
from app.grist_client import grist
from app.grist_types import datetime_vers_ts, parser_liste_dates, parser_liste_ids, ts_vers_date
from app.models import (
    NotionEntrainementOut,
    NotionEntrainementResumeOut,
    NotionRepriseOut,
    QCMOptionOut,
    QuestionEntrainementOut,
    QuestionOut,
    ReponseEntrainementQCMIn,
    ReponseOuverteIn,
    ReponseQCMIn,
    ReponseQCMOut,
)

router = APIRouter(prefix="/eleve", tags=["eleve"])


async def _reponses_deja_faites(eleve_id: int) -> set[tuple[int, int | None]]:
    """Ensemble des (Question, Palier) déjà répondus par l'élève, ouvertes et
    QCM confondues — sert uniquement à ne pas re-proposer indéfiniment en
    rattrapage une reprise déjà traitée en retard."""
    faites: set[tuple[int, int | None]] = set()
    for r in await grist.list_records("Reponses_Ouvertes", filter_={"Eleve": [eleve_id]}):
        faites.add((r["Question"], r.get("Palier")))
    for r in await grist.list_records("Reponses_QCM", filter_={"Eleve": [eleve_id]}):
        faites.add((r["Question"], r.get("Palier")))
    return faites


async def _notions_a_reprendre_aujourdhui(classe_id: int, eleve_id: int) -> list[dict]:
    """Interroge Grist et calcule, avec la date du jour RÉELLE (pas une
    formule Grist TODAY(), cf. spécifications section 0), quelles notions de
    la classe doivent être reprises aujourd'hui — et à quel palier — ainsi
    que les reprises passées (dates antérieures à aujourd'hui) que l'élève
    n'a encore jamais faites, autorisées en RATTRAPAGE plutôt que perdues
    (ex. absence, notion découverte tardivement)."""
    aujourdhui = date.today()

    cle_cache = f"reprises:{classe_id}:{eleve_id}:{aujourdhui.isoformat()}"
    en_cache = cache_reprises.get(cle_cache)
    if en_cache is not None:
        return en_cache

    notions = await grist.list_records("Notions", filter_={"Classe": [classe_id]})
    reponses_faites = await _reponses_deja_faites(eleve_id)

    a_reprendre = []
    for notion in notions:
        dates_reprise = parser_liste_dates(notion.get("Dates_de_reprise"))
        paliers = parser_liste_ids(notion.get("Paliers_applicables"))
        questions_notion: list[dict] | None = None  # récupérées seulement si besoin (cas retard)
        # Les deux listes sont générées dans la même boucle côté Grist
        # (cf. structure_tables_grist_programemo.md, section 7) donc alignées
        # par position — c'est cette hypothèse qui permet de retrouver le
        # palier correspondant à chaque date.
        for date_reprise, palier_id in zip(dates_reprise, paliers):
            if date_reprise > aujourdhui:
                continue  # pas encore due
            en_retard = date_reprise < aujourdhui
            if en_retard:
                if questions_notion is None:
                    questions_notion = await grist.list_records(
                        "Questions", filter_={"Notion": [notion["id"]]}
                    )
                deja_fait = any(
                    (q["id"], palier_id) in reponses_faites for q in questions_notion
                )
                if deja_fait:
                    continue  # rattrapage déjà effectué, on ne le repropose pas indéfiniment
            a_reprendre.append(
                {"notion": notion, "palier_id": palier_id, "en_retard": en_retard}
            )

    cache_reprises.set(cle_cache, a_reprendre)
    return a_reprendre


@router.get("/reprises-du-jour", response_model=list[NotionRepriseOut])
async def reprises_du_jour(
    utilisateur: UtilisateurSession = Depends(exiger_eleve),
) -> list[NotionRepriseOut]:
    a_reprendre = await _notions_a_reprendre_aujourdhui(utilisateur.classe_id, utilisateur.id)

    paliers = {p["id"]: p for p in await grist.list_records("Frequences")}

    resultat: list[NotionRepriseOut] = []
    for item in a_reprendre:
        notion = item["notion"]
        questions_brutes = await grist.list_records(
            "Questions", filter_={"Notion": [notion["id"]]}
        )
        questions_out: list[QuestionOut] = []
        for q in sorted(questions_brutes, key=lambda x: x["Ordre"]):
            options_brutes = await grist.list_records(
                "QCM_Options", filter_={"Question": [q["id"]]}
            )
            # Point de sécurité central : on NE PASSE PAS Est_correcte ici.
            # QCMOptionOut n'a d'ailleurs pas ce champ dans son schéma,
            # donc même une erreur de code ne pourrait pas le laisser fuiter.
            options_out = [
                QCMOptionOut(id=o["id"], texte_option=o["Texte_option"])
                for o in options_brutes
            ]
            questions_out.append(
                QuestionOut(
                    id=q["id"],
                    ordre=q["Ordre"],
                    enonce=q["Enonce"],
                    format=q.get("Format", "natif"),
                    contenu_h5p_url=q.get("Contenu_h5p_url"),
                    options_qcm=options_out,
                )
            )

        palier = paliers.get(item["palier_id"], {})
        resultat.append(
            NotionRepriseOut(
                notion_id=notion["id"],
                titre=notion["Titre"],
                palier_id=item["palier_id"],
                palier_label=palier.get("Label", "?"),
                questions=questions_out,
                en_retard=item["en_retard"],
            )
        )

    return resultat


async def _verifier_appartenance_question(question_id: int, classe_id: int) -> dict:
    """Vérifie que la question demandée appartient bien à une notion de la
    classe de l'élève connecté — jamais fait confiance à question_id seul."""
    questions = await grist.list_records("Questions", filter_={"id": [question_id]})
    if not questions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question introuvable.")
    question = questions[0]

    notions = await grist.list_records("Notions", filter_={"id": [question["Notion"]]})
    if not notions or notions[0]["Classe"] != classe_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Cette question n'appartient pas à votre classe."
        )
    return question


@router.post("/reponses/ouverte", status_code=status.HTTP_201_CREATED)
async def envoyer_reponse_ouverte(
    payload: ReponseOuverteIn,
    utilisateur: UtilisateurSession = Depends(exiger_eleve),
) -> dict:
    await _verifier_appartenance_question(payload.question_id, utilisateur.classe_id)

    await grist.add_records(
        "Reponses_Ouvertes",
        [
            {
                "Question": payload.question_id,
                "Eleve": utilisateur.id,  # toujours l'id de la SESSION, jamais transmis par le client
                "Date_saisie": datetime_vers_ts(datetime.now(timezone.utc)),
                "Texte_reponse": payload.texte_reponse,
                "Palier": payload.palier_id,
            }
        ],
    )
    return {"statut": "enregistre"}


@router.post("/reponses/qcm", response_model=ReponseQCMOut)
async def envoyer_reponse_qcm(
    payload: ReponseQCMIn,
    utilisateur: UtilisateurSession = Depends(exiger_eleve),
) -> ReponseQCMOut:
    question = await _verifier_appartenance_question(
        payload.question_id, utilisateur.classe_id
    )

    options = await grist.list_records(
        "QCM_Options", filter_={"Question": [question["id"]]}
    )
    option_choisie = next(
        (o for o in options if o["id"] == payload.option_choisie_id), None
    )
    if option_choisie is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Option invalide pour cette question.")

    option_correcte = next((o for o in options if o["Est_correcte"]), None)

    await grist.add_records(
        "Reponses_QCM",
        [
            {
                "Question": payload.question_id,
                "Eleve": utilisateur.id,
                "Date_saisie": datetime_vers_ts(datetime.now(timezone.utc)),
                "Option_choisie": payload.option_choisie_id,
                "Palier": payload.palier_id,
            }
        ],
    )

    # C'est SEULEMENT maintenant, après enregistrement, que le résultat
    # peut être révélé à l'élève.
    return ReponseQCMOut(
        est_correcte=bool(option_choisie["Est_correcte"]),
        option_correcte_id=option_correcte["id"] if option_correcte else -1,
    )


# ---------------------------------------------------------------------------
# Mode entraînement — rejouer une notion déjà vue, à volonté, hors calendrier
# officiel des reprises. AUCUNE écriture dans Grist ici : c'est un exercice
# d'auto-correction pour l'élève, qui ne doit jamais fausser les résultats ou
# la comparaison par palier consultés par le professeur (cf. spécifications
# section 3.3) — d'où l'absence totale d'appel à add_records.
# ---------------------------------------------------------------------------
@router.get("/entrainement", response_model=list[NotionEntrainementResumeOut])
async def lister_notions_entrainement(
    utilisateur: UtilisateurSession = Depends(exiger_eleve),
) -> list[NotionEntrainementResumeOut]:
    """Liste TOUTES les notions passées de la classe (pas seulement celles
    dues aujourd'hui ou en retard) — l'élève peut choisir librement laquelle
    rejouer, indépendamment du calendrier officiel de reprise."""
    aujourdhui = date.today()
    notions = await grist.list_records("Notions", filter_={"Classe": [utilisateur.classe_id]})
    resultat = [
        NotionEntrainementResumeOut(
            notion_id=n["id"], titre=n["Titre"], date_jour=ts_vers_date(n["Date_jour"])
        )
        for n in notions
        if ts_vers_date(n["Date_jour"]) <= aujourdhui
    ]
    resultat.sort(key=lambda n: n.date_jour, reverse=True)
    return resultat


@router.get("/entrainement/{notion_id}", response_model=NotionEntrainementOut)
async def obtenir_notion_entrainement(
    notion_id: int,
    utilisateur: UtilisateurSession = Depends(exiger_eleve),
) -> NotionEntrainementOut:
    notions = await grist.list_records("Notions", filter_={"id": [notion_id]})
    if not notions or notions[0]["Classe"] != utilisateur.classe_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Cette notion n'appartient pas à votre classe."
        )
    notion = notions[0]

    questions_brutes = await grist.list_records("Questions", filter_={"Notion": [notion_id]})
    questions_out: list[QuestionEntrainementOut] = []
    for q in sorted(questions_brutes, key=lambda x: x["Ordre"]):
        options_brutes = await grist.list_records("QCM_Options", filter_={"Question": [q["id"]]})
        questions_out.append(
            QuestionEntrainementOut(
                id=q["id"],
                ordre=q["Ordre"],
                enonce=q["Enonce"],
                format=q.get("Format", "natif"),
                contenu_h5p_url=q.get("Contenu_h5p_url"),
                reponse_attendue=q.get("Reponse_attendue", ""),
                options_qcm=[
                    QCMOptionOut(id=o["id"], texte_option=o["Texte_option"])
                    for o in options_brutes
                ],
            )
        )

    return NotionEntrainementOut(
        notion_id=notion["id"],
        titre=notion["Titre"],
        date_jour=ts_vers_date(notion["Date_jour"]),
        questions=questions_out,
    )


@router.post("/entrainement/reponse-qcm", response_model=ReponseQCMOut)
async def repondre_qcm_entrainement(
    payload: ReponseEntrainementQCMIn,
    utilisateur: UtilisateurSession = Depends(exiger_eleve),
) -> ReponseQCMOut:
    """Équivalent entraînement de /reponses/qcm : calcule et révèle le
    résultat, mais n'écrit RIEN dans Grist (cf. note ci-dessus)."""
    question = await _verifier_appartenance_question(payload.question_id, utilisateur.classe_id)

    options = await grist.list_records("QCM_Options", filter_={"Question": [question["id"]]})
    option_choisie = next((o for o in options if o["id"] == payload.option_choisie_id), None)
    if option_choisie is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Option invalide pour cette question.")
    option_correcte = next((o for o in options if o["Est_correcte"]), None)

    return ReponseQCMOut(
        est_correcte=bool(option_choisie["Est_correcte"]),
        option_correcte_id=option_correcte["id"] if option_correcte else -1,
    )
