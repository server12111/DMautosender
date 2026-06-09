FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV DATABASE_PATH=data/bot.db
ENV SESSIONS_PATH=data/sessions
ENV LOGS_PATH=data/logs

RUN mkdir -p data/sessions data/logs

CMD ["python", "run.py"]
