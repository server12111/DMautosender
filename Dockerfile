FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV DATABASE_PATH=/app/data/bot.db
ENV SESSIONS_PATH=/app/data/sessions
ENV LOGS_PATH=/app/data/logs

RUN mkdir -p data/sessions data/logs

VOLUME ["/app/data"]

CMD ["python", "run.py"]
