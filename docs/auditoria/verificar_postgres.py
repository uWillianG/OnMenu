"""Backup/restauração real do PostgreSQL descartável da auditoria Docker."""
import json
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
assert os.environ.get('DATABASE_URL') == 'postgresql://audit:audit@onmenu-audit-postgres:5432/onmenu_audit', (
    'Este roteiro aceita somente o PostgreSQL fictício da auditoria.')

import django
django.setup()
import psycopg
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import connection, connections
from django.test import override_settings
from django.utils import timezone
from config.models import OperationStatus
from menu.models import Restaurant
from orders.models import Order

assert connection.vendor == 'postgresql'
call_command('migrate', interactive=False, verbosity=0)
restaurant = Restaurant.objects.create(name='Restaurante fictício do backup')
user = User.objects.create_user(username='backup_audit', email='backup@example.invalid')
order = Order.objects.create(restaurant=restaurant, user=user,
    customer_name='Cliente fictício', phone='11999998888', subtotal=20)
future = timezone.now()+timedelta(hours=1)
OperationStatus.objects.create(name='sync_pending_pix', next_run_at=future,
    lease_until=future, last_success_at=timezone.now(), last_error='Estado anterior')

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    media = root/'media'
    media.mkdir()
    (media/'upload.txt').write_text('Upload fictício preservado', encoding='utf-8')
    with override_settings(BACKUP_ROOT=root/'backups', MEDIA_ROOT=media, BACKUP_BUCKET='',
            PERSISTED_SECRET_PATH=root/'missing-secret'):
        call_command('backup_site', stdout=StringIO())
    archive = next((root/'backups').glob('site-*.zip'))
    restored_files = root/'restored'
    call_command('restore_site', str(archive), target_dir=str(restored_files), stdout=StringIO())
    with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as database:
        database.execute('CREATE DATABASE onmenu_audit_restored')
    environment = {**os.environ, 'PGPASSWORD':'audit'}
    result = subprocess.run(['pg_restore', '--exit-on-error', '--no-owner', '--host','onmenu-audit-postgres',
        '--username','audit', '--dbname','onmenu_audit_restored', str(restored_files/'database.dump')],
        env=environment, capture_output=True, text=True)
    if result.returncode:
        print(result.stderr, file=sys.stderr)
        raise RuntimeError('A restauração PostgreSQL de teste falhou.')
    restored_settings = {**connection.settings_dict, 'NAME':'onmenu_audit_restored'}
    connections.databases['audit_restored'] = restored_settings
    try:
        call_command('reset_operations', database='audit_restored', stdout=StringIO())
    finally:
        connections['audit_restored'].close()
    with psycopg.connect('postgresql://audit:audit@onmenu-audit-postgres:5432/onmenu_audit_restored') as database:
        preserved = database.execute('SELECT order_number, subtotal, customer_name FROM orders_order WHERE id=%s',
            [order.pk]).fetchone()
        account = database.execute('SELECT email FROM auth_user WHERE id=%s', [user.pk]).fetchone()[0]
        operation = database.execute('SELECT lease_until, last_success_at, last_error FROM config_operationstatus').fetchone()
    assert preserved == (order.order_number, order.subtotal, 'Cliente fictício')
    assert account == 'backup@example.invalid'
    assert operation == (None, None, '')
    assert (restored_files/'media/upload.txt').read_text(encoding='utf-8') == 'Upload fictício preservado'
    print(json.dumps({'banco':'PostgreSQL 16', 'backup':'pg_dump custom', 'restauracao':'pg_restore',
        'pedido_preservado':True, 'conta_preservada':True, 'upload_preservado':True,
        'operacao_pronta_para_retomar':True,
        'credenciais_e_dados':'fictícios'}, ensure_ascii=False, indent=2))
