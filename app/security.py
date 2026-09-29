"""
Sécurité applicative : hachage des mots de passe et émission/vérification
des jetons de session. Cf. specifications_backend_programemo.md section 1.
"""
from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

ALPHABET_MOT_DE_PASSE = string.ascii_lowercase + string.digits  # évite les ambiguïtés (pas de majuscules/symboles)


def generer_mot_de_passe(longueur: int = 6) -> str:
    """Génère un mot de passe simple, adapté à une distribution en classe.
    Utilisé exclusivement par l'enseignant (cf. flux de génération, section 1.3
    des spécifications) — jamais par l'élève lui-même."""
    return "".join(secrets.choice(ALPHABET_MOT_DE_PASSE) for _ in range(longueur))


def hacher_mot_de_passe(mot_de_passe_clair: str) -> str:
    return _pwd_context.hash(mot_de_passe_clair)


def verifier_mot_de_passe(mot_de_passe_clair: str, hash_stocke: str) -> bool:
    return _pwd_context.verify(mot_de_passe_clair, hash_stocke)


def creer_jeton_session(
    *, sub: str, role: str, classe_id: int | None = None, est_administrateur: bool = False
) -> str:
    """role: "eleve" ou "enseignant". `sub` est l'identifiant interne
    (id de ligne Grist) de l'utilisateur authentifié. `est_administrateur`
    ne s'applique qu'aux enseignants (cf. rôle enseignant-administrateur,
    capable d'agir sur les comptes de ses collègues)."""
    expiration = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": sub,
        "role": role,
        "classe_id": classe_id,
        "est_admin": est_administrateur,
        "exp": expiration,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


class JetonInvalide(Exception):
    pass


def decoder_jeton_session(jeton: str) -> dict:
    try:
        return jwt.decode(jeton, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise JetonInvalide from exc
