"""Executa testes isolados sem carregar credenciais reais do .env."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
runtime = ROOT / '.audit-tmp'
runtime.mkdir(exist_ok=True)
os.environ.update({
    'DJANGO_SETTINGS_MODULE': 'config.settings', 'DATABASE_URL': 'sqlite:///:memory:',
    'DJANGO_DEBUG': 'True', 'DJANGO_ALLOWED_HOSTS': 'testserver,localhost,127.0.0.1',
    'MERCADOPAGO_ACCESS_TOKEN': '', 'MERCADOPAGO_PUBLIC_KEY': '', 'MERCADOPAGO_WEBHOOK_SECRET': '',
    'WHATSAPP_TOKEN': '', 'WHATSAPP_PHONE_ID': '', 'GOOGLE_OAUTH_CLIENT_ID': '',
    'GOOGLE_OAUTH_CLIENT_SECRET': '', 'EMAIL_BACKEND': 'django.core.mail.backends.locmem.EmailBackend',
    'TEMP': str(runtime), 'TMP': str(runtime), 'TMPDIR': str(runtime),
    'ONMENU_BACKUP_BUCKET':'', 'ONMENU_BACKUP_ACCESS_KEY':'', 'ONMENU_BACKUP_SECRET_KEY':'',
})
import django
django.setup()
from django.conf import settings
from django.core.management import call_command

if '--fast' in sys.argv:
    sys.argv.remove('--fast')
    settings.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
call_command('test', *sys.argv[1:], interactive=False, verbosity=1)
