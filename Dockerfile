FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CLOUDOPS_HOST=0.0.0.0 \
    PORT=8080
WORKDIR /app
COPY requirements.txt requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml README.md ./
COPY cloudops_agent ./cloudops_agent
RUN pip install --no-cache-dir --no-deps . \
    && useradd --create-home --uid 10001 cloudops
USER cloudops
EXPOSE 8080
CMD ["python", "-m", "cloudops_agent.server"]
