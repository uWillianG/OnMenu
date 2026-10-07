FROM python:3.12-slim
ARG POSTGRES_CLIENT_VERSION=17
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_DATA_ROOT=/data
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && mkdir -p /usr/share/postgresql-common/pgdg \
    && curl --fail --silent --show-error -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && . /etc/os-release \
    && printf 'deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt %s-pgdg main\n' "$VERSION_CODENAME" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends "postgresql-client-${POSTGRES_CLIENT_VERSION}" \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-production.txt ./
RUN pip install --no-cache-dir -r requirements-production.txt
RUN useradd --create-home --uid 10001 onmenu && mkdir -p /data/media /data/backups && chown -R onmenu:onmenu /data /app
COPY --chown=onmenu:onmenu . .
USER onmenu
EXPOSE 8000
ENTRYPOINT ["sh", "deployment/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "60", "--access-logfile", "-"]
