FROM python:3.11-slim@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
RUN uv pip install --system -e ".[dev]"

COPY . .

# Drop root before runtime. Limits blast radius if the app gets RCE —
# attacker lands as uid 10001 with no shell, no sudo, no writable
# system dirs. Static fixed uid so volumes mounted by compose match.
# Pre-create the uploaded-files dir owned by `app`: a fresh named volume
# mounted here inherits this ownership, so uid 10001 can write uploads
# (otherwise the volume is root-owned and every upload fails with EACCES).
RUN useradd --system --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /data/mod_files \
    && chown -R app:app /app /data
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
