"""
Client Grist — point de passage UNIQUE vers l'API Grist.
Aucun autre module de l'application ne doit appeler `httpx`/`requests`
directement vers Grist : tout passe par ici, avec le compte de service
(clé API à rôle Owner, cf. specifications_backend_programemo.md).
"""
from __future__ import annotations

from typing import Any

import httpx

from app.config import settings


class GristError(RuntimeError):
    """Erreur remontée par l'API Grist (statut HTTP non 2xx)."""


class GristClient:
    def __init__(self) -> None:
        self._base_url = f"{settings.grist_api_url}/docs/{settings.grist_doc_id}/tables"
        self._headers = {
            "Authorization": f"Bearer {settings.grist_api_key}",
            "Content-Type": "application/json",
        }

    async def list_records(
        self, table: str, filter_: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Récupère les lignes d'une table, avec un filtre optionnel
        de la forme {"Colonne": [valeur1, valeur2]} (syntaxe Grist)."""
        params: dict[str, Any] = {}
        if filter_:
            import json

            params["filter"] = json.dumps(filter_)

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base_url}/{table}/records",
                headers=self._headers,
                params=params,
                timeout=10.0,
            )
        if resp.status_code >= 300:
            raise GristError(f"GET {table} -> {resp.status_code} : {resp.text}")
        return [r["fields"] | {"id": r["id"]} for r in resp.json()["records"]]

    async def add_records(
        self, table: str, records: list[dict[str, Any]]
    ) -> list[int]:
        """Crée des lignes. `records` est une liste de dicts {colonne: valeur}."""
        payload = {"records": [{"fields": r} for r in records]}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base_url}/{table}/records",
                headers=self._headers,
                json=payload,
                timeout=10.0,
            )
        if resp.status_code >= 300:
            raise GristError(f"POST {table} -> {resp.status_code} : {resp.text}")
        return [r["id"] for r in resp.json()["records"]]

    async def update_records(
        self, table: str, records: list[dict[str, Any]]
    ) -> None:
        """Met à jour des lignes existantes. Chaque dict doit contenir
        la clé "id" (identifiant de ligne Grist) en plus des champs à modifier."""
        payload = {
            "records": [
                {"id": r["id"], "fields": {k: v for k, v in r.items() if k != "id"}}
                for r in records
            ]
        }
        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{self._base_url}/{table}/records",
                headers=self._headers,
                json=payload,
                timeout=10.0,
            )
        if resp.status_code >= 300:
            raise GristError(f"PATCH {table} -> {resp.status_code} : {resp.text}")

    async def delete_records(self, table: str, ids: list[int]) -> None:
        """Supprime des lignes. Contrairement aux autres méthodes, l'API
        Grist attend ici directement une liste brute d'ids en corps de
        requête (pas d'enveloppe {"records": [...]})."""
        if not ids:
            return
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base_url}/{table}/records/delete",
                headers=self._headers,
                json=ids,
                timeout=10.0,
            )
        if resp.status_code >= 300:
            raise GristError(f"DELETE {table} -> {resp.status_code} : {resp.text}")


# Instance unique réutilisée dans toute l'application (pas de session HTTP
# persistante ouverte en permanence, httpx.AsyncClient est recréé par appel
# pour rester simple — à optimiser avec un client partagé si la charge le justifie).
grist = GristClient()
