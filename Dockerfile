ARG PYTHON_VERSION=3.13
FROM python:${PYTHON_VERSION}-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app/biovision-iffar

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir --no-compile -r requirements.txt \
    && rm -rf /root/.cache

RUN useradd --create-home --uid 10001 biovision
COPY --chown=biovision:biovision . .
USER biovision

EXPOSE 5001

CMD ["gunicorn", "--config", "gunicorn.conf.py", "backend.wsgi:app"]
