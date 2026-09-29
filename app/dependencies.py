"""
Dépendances d'authentification. C'est ICI que vit la règle de sécurité
centrale du projet (cf. spécifications back-end, section 2) :
> ne jamais faire confiance à un identifiant transmis par le client
> sans le comparer à celui porté par le jeton de session, vérifié serveur.

Toute route qui manipule une ressource liée à un élève ou un enseignant
précis doit dépendre de `utilisateur_courant` (ou des variantes de rôle
ci-dessous) et comparer explicitement `utilisateur_courant.id` à la
ressource demandée — jamais utiliser un id transmis dans l'URL/le corps
de la requête sans ce contrôle.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.security import JetonInvalide, decoder_jeton_session

_bearer_scheme = HTTPBearer()


@dataclass
class UtilisateurSession:
    id: int
    role: str  # "eleve" ou "enseignant"
    classe_id: int | None
    est_administrateur: bool = False  # n'a de sens que pour role == "enseignant"


def utilisateur_courant(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> UtilisateurSession:
    try:
        payload = decoder_jeton_session(credentials.credentials)
    except JetonInvalide:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session invalide ou expirée, reconnectez-vous.",
        )
    return UtilisateurSession(
        id=int(payload["sub"]),
        role=payload["role"],
        classe_id=payload.get("classe_id"),
        est_administrateur=bool(payload.get("est_admin", False)),
    )


def exiger_eleve(
    utilisateur: UtilisateurSession = Depends(utilisateur_courant),
) -> UtilisateurSession:
    if utilisateur.role != "eleve":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Réservé aux élèves.")
    return utilisateur


def exiger_enseignant(
    utilisateur: UtilisateurSession = Depends(utilisateur_courant),
) -> UtilisateurSession:
    if utilisateur.role != "enseignant":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Réservé aux enseignants.")
    return utilisateur


def exiger_enseignant_administrateur(
    utilisateur: UtilisateurSession = Depends(exiger_enseignant),
) -> UtilisateurSession:
    """Réservé aux enseignants marqués administrateurs (colonne
    `Est_administrateur` sur `Enseignants`) — utilisé pour les actions qui
    dépassent le périmètre d'un enseignant sur ses propres classes, comme
    agir sur le compte d'un collègue."""
    if not utilisateur.est_administrateur:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Réservé aux enseignants administrateurs."
        )
    return utilisateur
