FROM node:24-bookworm-slim AS frontend
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS application
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-web.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements-web.txt
COPY torinteo_paper/ ./torinteo_paper/
RUN pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home markets && mkdir -p /data && chown markets:markets /data
COPY --from=frontend /build/web/dist ./web/dist
USER markets
EXPOSE 8787
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=4).read()"
ENTRYPOINT ["python", "-m", "torinteo_paper.web", "--host", "0.0.0.0", "--port", "8787", "--data-dir", "/data", "--static-dir", "/app/web/dist"]
