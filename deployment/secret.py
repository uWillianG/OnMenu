"""Chave por instalação, persistida no volume privado; não imprime credenciais externas."""
import os
from pathlib import Path
from django.core.management.utils import get_random_secret_key

path = Path(os.environ.get('DJANGO_PERSISTED_SECRET',
    Path(os.environ.get('DJANGO_DATA_ROOT', '/data')) / 'django-secret'))
path.parent.mkdir(parents=True, exist_ok=True)
try:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    key = path.read_text(encoding='utf-8').strip()
else:
    with os.fdopen(descriptor, 'w', encoding='utf-8') as destination:
        key = get_random_secret_key()
        destination.write(key)
if len(key) < 50:
    raise SystemExit('A chave persistida da instalação está inválida.')
print(key)
