"""
Conversion entre les types Python natifs (date, datetime) et le format brut
utilisé par l'API REST Grist pour les colonnes Date/DateTime.

Piège important, découvert avant qu'il ne cause un bug silencieux en
production : l'API Grist ne renvoie PAS les dates sous forme de chaîne
ISO ("2026-09-03"), mais sous forme de nombre — les secondes écoulées
depuis l'epoch Unix (minuit UTC pour une colonne Date). Comparer une
valeur brute de l'API à une chaîne de date échoue silencieusement
(aucune erreur, juste un résultat toujours vide) — exactement le genre
de piège qu'on a appris à traquer en configurant les tables Grist.

Toute conversion entre l'API Grist et le reste de l'application DOIT
passer par ces fonctions, plutôt que de manipuler les timestamps à la main.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone


_RE_DATETIME_DATE = re.compile(r"datetime\.date\((\d+),\s*(\d+),\s*(\d+)\)")


def ts_vers_date(valeur: float | int | str | None) -> date | None:
    """Tolérant aux formats réellement observés côté API Grist pour une
    valeur "date" : timestamp Unix numérique (colonnes Date natives)
    ou chaîne ISO "AAAA-MM-JJ"."""
    if valeur is None or valeur == "":
        return None
    if isinstance(valeur, str):
        return date.fromisoformat(valeur)
    return datetime.fromtimestamp(valeur, tz=timezone.utc).date()


def parser_liste_dates(brut) -> list[date]:
    """Gère les différentes formes observées pour une colonne Grist censée
    contenir une liste de dates :
    - une vraie liste JSON (de timestamps ou de chaînes ISO) ;
    - OU (cas rencontré en pratique quand la colonne reste typée Text/Any
      côté Grist plutôt que "Date List") une chaîne UNIQUE contenant la
      représentation Python brute d'une liste de `datetime.date(...)`,
      que Grist produit en repli quand le type de colonne ne permet pas
      de sérialiser proprement le résultat de la formule.
    Traiter ce cas ici plutôt que d'exiger un typage Grist parfait évite
    une dépendance fragile à un réglage d'interface, qui s'est révélé
    source de frictions récurrentes sur ce projet."""
    if not brut:
        return []
    if isinstance(brut, str):
        return [
            date(int(y), int(m), int(d))
            for y, m, d in _RE_DATETIME_DATE.findall(brut)
        ]
    return [d for d in (ts_vers_date(v) for v in brut) if d is not None]


def parser_liste_ids(brut) -> list[int]:
    """Gère la liste de références Grist, y compris la forme encodée
    interne ['L', id1, id2, ...] parfois renvoyée par l'API pour une
    colonne Reference List calculée par formule (le 'L' est un tag
    interne de type, pas une vraie valeur de la liste)."""
    if not brut:
        return []
    valeurs = list(brut)
    if valeurs and isinstance(valeurs[0], str) and valeurs[0] == "L":
        valeurs = valeurs[1:]
    return [int(v) for v in valeurs]


def date_vers_ts(d: date) -> float:
    dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return dt.timestamp()


def ts_vers_datetime(valeur: float | int | str | None) -> datetime | None:
    if valeur is None or valeur == "":
        return None
    if isinstance(valeur, str):
        return datetime.fromisoformat(valeur)
    return datetime.fromtimestamp(valeur, tz=timezone.utc)


def datetime_vers_ts(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()
