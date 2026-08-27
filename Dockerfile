FROM python:3.11-slim

# Unbuffered so log lines reach `docker logs` as they happen.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencies first: this layer stays cached as long as requirements.txt is
# unchanged, so editing application code does not reinstall them.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py triage.py security.py ./
COPY templates/ ./templates/

# The app writes its SQLite file to BASE_DIR/data/leads.db, so /app/data has to
# exist and belong to the user before we drop privileges - otherwise the first
# write fails.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data \
    && chown -R app:app /app

USER app

EXPOSE 8000

# /health is the app's own endpoint. Using the interpreter that is already
# here avoids installing curl just for this.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health').read()"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
