#!/bin/sh
set -eu
case "$(printf '%s' "${DJANGO_DEBUG:-False}" | tr '[:upper:]' '[:lower:]')" in
true|1|yes|on)
  echo 'Configure o modo de produção para iniciar este serviço.' >&2
  exit 1
  ;;
esac
if [ -z "${DJANGO_SECRET_KEY:-}" ]; then
  DJANGO_SECRET_KEY="$(python deployment/secret.py)"
  export DJANGO_SECRET_KEY
fi
if [ "${1:-}" = "operations" ]; then
  exec python manage.py run_operations
fi
python manage.py migrate --noinput
python manage.py collectstatic --noinput --verbosity 0
python manage.py check --deploy --fail-level ERROR
exec "$@"
