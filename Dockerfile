# Image de production pour l'API PrograMemo — construite par le pipeline
# GitLab CI/CD et poussée sur Docker Hub, d'où Render la déploie (cf.
# deploiement_laforge_render.md). Image légère : dépendances puis code
# applicatif uniquement, aucun secret ici (tout vient des variables
# d'environnement fournies par Render à l'exécution).
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Render (et la plupart des PaaS) fournissent le port d'écoute réel via la
# variable d'environnement PORT à l'exécution — 8000 n'est qu'une valeur de
# repli pour un lancement local de l'image (docker run sans -e PORT=...).
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
