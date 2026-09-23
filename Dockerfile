FROM ghcr.io/astral-sh/uv:0.12.13 AS uv

FROM python:3.13-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        poppler-utils tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --home-dir /home/app \
        --shell /usr/sbin/nologin app

COPY --chown=app:app alembic.ini ./
COPY --chown=app:app app ./app
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app scripts ./scripts

EXPOSE 8000

USER 10001:10001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
