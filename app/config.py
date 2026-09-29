"""
Configuration centralisée, lue depuis les variables d'environnement (.env en local).
Ne jamais committer de vraies valeurs — voir .env.example à la racine du projet.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- Grist ---
    grist_api_url: str = "https://<votre-instance-grist>/api"
    grist_doc_id: str = "<identifiant_du_document>"
    grist_api_key: str = "<cle_api_du_compte_de_service>"

    # --- Authentification ---
    jwt_secret: str = "changez-moi-en-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 8  # 8h, une journée de classe

    # --- Cache reprises du jour ---
    cache_ttl_seconds: int = 300  # 5 minutes, cf. spécifications back-end

    # --- Nommage réel des colonnes dans Grist ---
    # Les noms de colonnes Grist ("Column ID") peuvent diverger du nom
    # théorique du schéma si une colonne a été supprimée/recréée (Grist
    # ajoute parfois un suffixe numérique pour éviter une collision interne,
    # sans que ce soit modifiable facilement depuis l'interface). Plutôt que
    # de coder ce nom en dur dans les routers, on le rend configurable ici.
    colonne_hash_enseignants: str = "Mot_de_passe_hash"
    colonne_hash_eleves: str = "Mot_de_passe_hash"
    colonne_administrateur_enseignants: str = "Est_administrateur"

    # --- CORS ---
    # "*" en développement local (par défaut, pratique). En production,
    # restreindre à l'URL réelle du front (une ou plusieurs, séparées par
    # des virgules) via la variable d'environnement PROGRAMEMO_CORS_ALLOW_ORIGINS
    # — cf. README section déploiement.
    cors_allow_origins: str = "*"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="PROGRAMEMO_")


settings = Settings()
