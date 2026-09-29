"""
Cache en mémoire, à TTL court (5 min par défaut, cf. config.cache_ttl_seconds).
Suffisant pour une V1 mono-instance. À remplacer par Redis si l'application
est un jour déployée sur plusieurs instances en parallèle (le cache
en mémoire ne serait alors plus partagé entre elles).
"""
from __future__ import annotations

import time
from typing import Any

from app.config import settings


class CacheTTL:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, cle: str) -> Any | None:
        entree = self._store.get(cle)
        if entree is None:
            return None
        expire_a, valeur = entree
        if time.monotonic() > expire_a:
            del self._store[cle]
            return None
        return valeur

    def set(self, cle: str, valeur: Any) -> None:
        expire_a = time.monotonic() + settings.cache_ttl_seconds
        self._store[cle] = (expire_a, valeur)

    def invalider(self, cle: str) -> None:
        self._store.pop(cle, None)


cache_reprises = CacheTTL()
