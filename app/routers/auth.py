from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.grist_client import grist
from app.models import LoginRequest, TokenResponse
from app.security import creer_jeton_session, verifier_mot_de_passe
from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest) -> TokenResponse:
    # On cherche d'abord côté élèves, puis côté enseignants — l'identifiant
    # de connexion est supposé unique tous rôles confondus dans le document.
    eleves = await grist.list_records(
        "Eleves", filter_={"Identifiant_connexion": [payload.identifiant]}
    )
    if eleves:
        eleve = eleves[0]
        if not verifier_mot_de_passe(
            payload.mot_de_passe, eleve.get(settings.colonne_hash_eleves, "")
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Identifiants incorrects.")
        jeton = creer_jeton_session(
            sub=str(eleve["id"]), role="eleve", classe_id=eleve["Classe"]
        )
        return TokenResponse(access_token=jeton, role="eleve")

    enseignants = await grist.list_records(
        "Enseignants", filter_={"Email": [payload.identifiant]}
    )
    if enseignants:
        enseignant = enseignants[0]
        if not verifier_mot_de_passe(
            payload.mot_de_passe, enseignant.get(settings.colonne_hash_enseignants, "")
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Identifiants incorrects.")
        est_administrateur = bool(enseignant.get(settings.colonne_administrateur_enseignants, False))
        jeton = creer_jeton_session(
            sub=str(enseignant["id"]), role="enseignant", est_administrateur=est_administrateur
        )
        return TokenResponse(access_token=jeton, role="enseignant", est_administrateur=est_administrateur)

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Identifiants incorrects.")
