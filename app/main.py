from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import auth, eleve, prof

app = FastAPI(
    title="PrograMemo API",
    description="Back-end applicatif entre le front élève/prof et Grist.",
    version="0.1.0",
)

# "*" par défaut (pratique en développement local). En production, définir
# PROGRAMEMO_CORS_ALLOW_ORIGINS avec l'URL réelle du front (plusieurs valeurs
# possibles, séparées par des virgules) plutôt que de laisser "*" ouvert.
_origines = (
    ["*"]
    if settings.cors_allow_origins.strip() == "*"
    else [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origines,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(eleve.router)
app.include_router(prof.router)


@app.get("/health", tags=["technique"])
async def health() -> dict:
    return {"statut": "ok"}
