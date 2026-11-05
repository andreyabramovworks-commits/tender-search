FROM mcr.microsoft.com/playwright/python:v1.64.1-noble
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml alembic.ini ./
COPY src ./src
COPY migrations ./migrations
COPY config ./config
COPY scripts ./scripts
RUN pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home tender
USER 10001:10001
CMD ["tender-search", "run"]
