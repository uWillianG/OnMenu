# OnMenu

Cardápio digital para delivery e retirada, em Django. Um restaurante ativo por
instalação: cardápio público, carrinho, checkout com Pix e cartão (Mercado
Pago), acompanhamento do pedido e painel para a equipe.

## Desenvolvimento

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py createsuperuser
.\.venv\Scripts\python manage.py seed_menu
.\.venv\Scripts\python manage.py runserver
```

Abra `http://127.0.0.1:8000/` para o cardápio. Painel da equipe em
`/staff/orders/`; Django admin em `/admin/`.

Sem `.env` o projeto roda em modo de desenvolvimento: SQLite, `DEBUG=True`,
pagamentos em **mock** (QR de exemplo, sem cobrança), WhatsApp e e-mail apenas
no log/console. Copie `.env.example` para `.env` para ligar cada integração.

## Testes

```powershell
.\.venv\Scripts\python manage.py test
```

## Deploy

### 1. Variáveis de ambiente

Copie `.env.example` para `.env` no servidor. O mínimo para sair do modo de
desenvolvimento:

```env
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<50+ caracteres aleatórios>
DJANGO_ALLOWED_HOSTS=seudominio.com.br,www.seudominio.com.br
BASE_URL=https://seudominio.com.br
```

Gere a chave com:

```powershell
.\.venv\Scripts\python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

Sem `DJANGO_SECRET_KEY`, subir com `DJANGO_DEBUG=False` falha na hora — de
propósito, para a chave de desenvolvimento nunca ir para produção.

### 2. HTTPS

Com `DJANGO_DEBUG=False` entram em vigor cookies `Secure`, HSTS e redirect
HTTP→HTTPS. Atrás de nginx/Cloudflare/Render (que terminam o TLS), ligue
`DJANGO_BEHIND_PROXY=True` para o Django reconhecer o HTTPS pelo cabeçalho
`X-Forwarded-Proto`. Se o próprio proxy já redireciona, desligue
`DJANGO_SECURE_SSL_REDIRECT` para não criar loop.

### 3. Banco

Sem `DATABASE_URL` o projeto usa o SQLite local (WAL ligado, aguenta o volume de
um restaurante). Para Postgres:

```env
DATABASE_URL=postgres://usuario:senha@host:5432/onmenu
```

e instale o driver: `pip install "psycopg[binary]"`.

### 4. Arquivos

```powershell
.\.venv\Scripts\python manage.py collectstatic --noinput
.\.venv\Scripts\python manage.py migrate
```

Os estáticos são servidos pelo **WhiteNoise** (já comprimidos no
`collectstatic`), sem precisar de nginx. Já os uploads (logo, fotos dos itens)
ficam em `media/`: sirva por nginx ou por um bucket. Em um servidor só, ligue
`DJANGO_SERVE_MEDIA=True` e o próprio Django entrega os arquivos.

### 5. Conferir antes de abrir ao público

```powershell
.\.venv\Scripts\python manage.py check --deploy
```

Não deve sobrar nenhum aviso além do `security.W021` (HSTS preload, opcional).
As checagens do projeto avisam se o `MERCADOPAGO_WEBHOOK_SECRET` estiver
faltando com pagamento real ligado (`orders.W001`) e se a `BASE_URL` não for
HTTPS (`orders.W002`).

### 6. Servidor de aplicação

Em Linux, `pip install gunicorn` e:

```bash
gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3
```

Os erros 500 vão para o console (systemd/journal) e, se `DJANGO_ADMINS` estiver
preenchido e o SMTP configurado, também por e-mail.

### 7. Tarefas agendadas

| Comando | Frequência | Para quê |
|---------|------------|----------|
| `manage.py sync_pending_pix` | ~10 min | Confirma Pix pendentes quando o webhook falha |
| `manage.py sync_pending_card` | ~30 min | Confirma cartões em análise (janela de ~24h) |
| `manage.py backup_db` | 1 h ou diária | Cópia do SQLite (mantém as últimas `DJANGO_BACKUP_KEEP`) |

Sem os dois primeiros, um pagamento aprovado fora do webhook fica preso em
"pendente". Use o Agendador de Tarefas do Windows ou cron.

### 8. Webhooks do Mercado Pago

Cadastre no painel do Mercado Pago (Suas integrações → Webhooks):

- `BASE_URL/webhook/pix/`
- `BASE_URL/webhook/cartao/`

Copie o *secret* gerado para `MERCADOPAGO_WEBHOOK_SECRET`. **Em produção, sem o
secret os webhooks são recusados** (401) — é o que impede alguém de marcar
pedidos como pagos.
