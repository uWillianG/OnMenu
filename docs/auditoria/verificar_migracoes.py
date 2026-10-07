"""Aplica as migrações a uma cópia da base local e verifica preservação dos registros."""
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
runtime = ROOT / '.audit-tmp'
runtime.mkdir(exist_ok=True)
copy = runtime / 'migration-check.sqlite3'
origin = sqlite3.connect((ROOT/'db.sqlite3').as_uri()+'?mode=ro', uri=True)
target = sqlite3.connect(copy)
try:
    origin.backup(target)
finally:
    origin.close()
    target.close()
os.environ.update({'DJANGO_SETTINGS_MODULE':'config.settings', 'DATABASE_URL':'',
    'DJANGO_SQLITE_PATH':str(copy), 'DJANGO_DEBUG':'True',
    'MERCADOPAGO_ACCESS_TOKEN':'', 'WHATSAPP_TOKEN':'', 'WHATSAPP_PHONE_ID':'',
    'GOOGLE_OAUTH_CLIENT_ID':'', 'GOOGLE_OAUTH_CLIENT_SECRET':'',
    'EMAIL_BACKEND':'django.core.mail.backends.locmem.EmailBackend'})
import django
django.setup()
from django.core.management import call_command
from django.db import connection

tables = ['orders_order', 'orders_orderitem', 'menu_restaurant', 'menu_menuitem', 'auth_user', 'accounts_profile']
def counts():
    with connection.cursor() as cursor:
        result = {}
        for table in tables:
            cursor.execute('SELECT COUNT(*) FROM ' + table)
            result[table] = cursor.fetchone()[0]
        return result
before = counts()
call_command('migrate', verbosity=0, interactive=False)
after = counts()
assert before == after, (before, after)
result = {'registros_preservados':before == after, 'contagens':after,
          'migrações_aplicadas_em':'cópia isolada'}
(ROOT/'docs/auditoria/migracoes.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
