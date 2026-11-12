FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir requests anthropic
COPY agent agent
COPY config.json .
VOLUME /app/state
CMD ["python", "-u", "-m", "agent", "run"]
