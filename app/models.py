from __future__ import annotations

from datetime import date

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Authentification
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    identifiant: str
    mot_de_passe: str


class TokenResponse(BaseModel):
    access_token: str
    role: str
    est_administrateur: bool = False  # utile au front pour afficher ou non l'onglet Administration


# ---------------------------------------------------------------------------
# Vue élève — reprises du jour
# ---------------------------------------------------------------------------
class QCMOptionOut(BaseModel):
    """ATTENTION : ne comporte volontairement PAS de champ est_correcte.
    C'est le contrat qui garantit qu'aucune route ne peut, par erreur,
    exposer la bonne réponse avant que l'élève n'ait répondu."""
    id: int
    texte_option: str


class QuestionOut(BaseModel):
    id: int
    ordre: int
    enonce: str
    format: str  # "natif" ou "h5p"
    contenu_h5p_url: str | None = None
    options_qcm: list[QCMOptionOut] = []


class NotionRepriseOut(BaseModel):
    notion_id: int
    titre: str
    palier_id: int
    palier_label: str
    questions: list[QuestionOut]
    en_retard: bool = False  # reprise dont la date est passée sans avoir été faite : autorisée en rattrapage


class ReponseOuverteIn(BaseModel):
    question_id: int
    palier_id: int
    texte_reponse: str


class ReponseQCMIn(BaseModel):
    question_id: int
    palier_id: int
    option_choisie_id: int


class ReponseQCMOut(BaseModel):
    """Ici, à l'inverse de QCMOptionOut, on RÉVÈLE le résultat —
    mais seulement après coup, une fois la réponse enregistrée."""
    est_correcte: bool
    option_correcte_id: int


# ---------------------------------------------------------------------------
# Mode entraînement — rejouer une notion déjà vue, à volonté, hors calendrier
# officiel des reprises. Rien de ce qui se passe ici n'est écrit dans Grist :
# c'est un pur exercice d'auto-correction, qui ne doit jamais fausser les
# statistiques (résultats/comparaison) que consulte le professeur.
# ---------------------------------------------------------------------------
class NotionEntrainementResumeOut(BaseModel):
    notion_id: int
    titre: str
    date_jour: date


class QuestionEntrainementOut(BaseModel):
    id: int
    ordre: int
    enonce: str
    format: str
    contenu_h5p_url: str | None = None
    reponse_attendue: str  # visible ici (contrairement à QuestionOut) : c'est de l'auto-correction, pas un contrôle noté
    options_qcm: list[QCMOptionOut] = []  # est_correcte reste masqué tant que l'élève n'a pas cliqué, même logique que la vue officielle


class NotionEntrainementOut(BaseModel):
    notion_id: int
    titre: str
    date_jour: date
    questions: list[QuestionEntrainementOut]


class ReponseEntrainementQCMIn(BaseModel):
    question_id: int
    option_choisie_id: int


# ---------------------------------------------------------------------------
# Vue professeur
# ---------------------------------------------------------------------------
class NotionIn(BaseModel):
    date_jour: date
    titre: str


class NotionUpdateIn(BaseModel):
    date_jour: date | None = None
    titre: str | None = None


class QCMOptionOutProf(BaseModel):
    id: int
    texte_option: str
    est_correcte: bool  # visible côté prof, contrairement à QCMOptionOut (vue élève)


class QuestionOutProf(BaseModel):
    id: int
    ordre: int
    enonce: str
    reponse_attendue: str
    format: str
    contenu_h5p_url: str | None = None
    options: list[QCMOptionOutProf] = []


class ClasseOut(BaseModel):
    id: int
    nom: str
    role: str  # "Titulaire" ou "Co-intervenant"


class NotionOut(BaseModel):
    id: int
    date_jour: date
    titre: str
    enseignant_nom: str


class QuestionIn(BaseModel):
    ordre: int
    enonce: str
    reponse_attendue: str
    format: str = "natif"
    contenu_h5p_url: str | None = None
    options_qcm: list[str] = []  # première option = la bonne réponse, par convention d'API
    index_bonne_option: int = 0


class QuestionUpdateIn(BaseModel):
    ordre: int | None = None
    enonce: str | None = None
    reponse_attendue: str | None = None
    format: str | None = None
    contenu_h5p_url: str | None = None
    # Si fourni, remplace ENTIÈREMENT les options QCM existantes (suppression
    # puis recréation) plutôt que de tenter une fusion fine option par option.
    options_qcm: list[str] | None = None
    index_bonne_option: int = 0


class ResultatLigne(BaseModel):
    eleve_nom: str
    reponse_ouverte: str | None = None
    score_qcm: str | None = None  # ex. "2/2"


class ComparaisonPalier(BaseModel):
    palier_label: str
    taux_reussite_pct: float
    nb_reponses: int


class GenerationMotDePasseOut(BaseModel):
    eleve_id: int
    identifiant_connexion: str
    mot_de_passe_clair: str  # affiché UNE SEULE fois, jamais réémis ensuite


class ChangerMonMotDePasseIn(BaseModel):
    """Corps optionnel : si nouveau_mot_de_passe est fourni, l'enseignant
    choisit lui-même son mot de passe ; sinon, un mot de passe est généré
    automatiquement (même logique que pour les élèves)."""
    nouveau_mot_de_passe: str | None = None


class MotDePasseOut(BaseModel):
    """Réponse générique pour un changement de mot de passe self-service —
    le mot de passe en clair n'est renvoyé qu'à cette occasion, jamais stocké
    ni réémis ensuite (même principe que GenerationMotDePasseOut)."""
    identifiant_connexion: str
    mot_de_passe_clair: str


# ---------------------------------------------------------------------------
# Administration (enseignant administrateur)
# ---------------------------------------------------------------------------
class EnseignantOut(BaseModel):
    id: int
    nom: str
    email: str
    est_administrateur: bool


class GenerationMotDePasseEnseignantOut(BaseModel):
    enseignant_id: int
    identifiant_connexion: str
    mot_de_passe_clair: str  # affiché UNE SEULE fois, jamais réémis ensuite
