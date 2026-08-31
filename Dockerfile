FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml alembic.ini ./
COPY migrations ./migrations
COPY data ./data
COPY scripts ./scripts
COPY src ./src

# Непривилегированный пользователь.
RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

CMD ["uvicorn", "shop.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
