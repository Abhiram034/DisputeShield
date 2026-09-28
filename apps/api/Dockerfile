FROM python:3.11-slim
WORKDIR /srv
COPY apps/api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/api/app ./app
COPY migrations ./migrations
COPY scripts/migrate.py ./scripts/migrate.py
CMD ["sh", "-c", "python scripts/migrate.py && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
