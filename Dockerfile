FROM mcr.microsoft.com/playwright/python:v1.64.1-noble
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# The server has no IPv6 route, while some Google hosts advertise IPv6 before
# IPv4. Prefer IPv4 so Drive/OAuth calls fail over neither slowly nor forever.
RUN printf 'precedence ::ffff:0:0/96  100\n' >> /etc/gai.conf
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml alembic.ini ./
COPY src ./src
COPY migrations ./migrations
COPY config ./config
COPY scripts ./scripts
RUN pip install --no-cache-dir --no-deps --force-reinstall . && useradd --uid 10001 --create-home tender
USER 10001:10001
CMD ["tender-search", "run"]
