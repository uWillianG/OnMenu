"""
Django settings for config project.

Configuração dirigida por variáveis de ambiente (arquivo ``.env`` na raiz —
veja ``.env.example``). Os padrões deixam o desenvolvimento funcionando sem
nenhuma variável; produção exige, no mínimo, ``DJANGO_DEBUG=False``,
``DJANGO_SECRET_KEY`` e ``DJANGO_ALLOWED_HOSTS``.

Checklist de deploy: https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/
(rodar ``python manage.py check --deploy`` com o .env de produção carregado).
"""

import os
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Carrega variáveis de ambiente do arquivo .env (SMTP, Mercado Pago, etc.).
load_dotenv(BASE_DIR / '.env')


def env_bool(name, default=False):
    """Lê um booleano do ambiente aceitando 1/true/yes/on (sem diferenciar caixa)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def env_list(name, default=()):
    """Lê uma lista separada por vírgulas do ambiente."""
    values = [item.strip() for item in os.environ.get(name, '').split(',') if item.strip()]
    return values or list(default)


# ── Núcleo ──────────────────────────────────────────────────────────────────
# O padrão é o modo de desenvolvimento; produção precisa de DJANGO_DEBUG=False.
DEBUG = env_bool('DJANGO_DEBUG', True)

# Chave de desenvolvimento: serve só para o setup local rodar sem .env. Fora do
# DEBUG a chave real é obrigatória (gere com
# `python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"`).
DEV_SECRET_KEY = 'django-insecure-(6au7gd2k8q&c18vabc9ek8w!%q=z=r!@s)$%x^#z2_5!aznac'
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured(
            'Defina DJANGO_SECRET_KEY no ambiente para rodar com DJANGO_DEBUG=False.'
        )
    SECRET_KEY = DEV_SECRET_KEY

ALLOWED_HOSTS = env_list(
    'DJANGO_ALLOWED_HOSTS',
    ['localhost', '127.0.0.1', '[::1]'] if DEBUG else [],
)

# URL pública do backend (webhooks do Mercado Pago, callback do Google OAuth).
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:8000')

# Origens confiáveis para o CSRF (formato esquema://host). Sem configuração
# explícita, deriva da BASE_URL.
CSRF_TRUSTED_ORIGINS = env_list('DJANGO_CSRF_TRUSTED_ORIGINS')
if not CSRF_TRUSTED_ORIGINS:
    _base = urlsplit(BASE_URL)
    if _base.scheme and _base.netloc:
        CSRF_TRUSTED_ORIGINS = [f'{_base.scheme}://{_base.netloc}']


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'config',  # comandos de operação do projeto (ex.: backup_db)
    'accounts',
    'menu',
    'cart',
    'orders',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

if not DEBUG:
    # Serve os arquivos de STATIC_ROOT sem depender do nginx. Em DEBUG quem
    # serve é o próprio runserver (a partir de STATICFILES_DIRS), então o
    # WhiteNoise fica de fora e não reclama da pasta staticfiles/ inexistente.
    MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'cart.context_processors.cart_summary',
                'orders.context_processors.notifications',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases
# Sem DATABASE_URL usa o SQLite local; em produção aponte para o Postgres
# (ex.: DATABASE_URL=postgres://user:senha@host:5432/onmenu).

DATABASE_URL = os.environ.get('DATABASE_URL', '')
if DATABASE_URL:
    try:
        import dj_database_url
    except ImportError as exc:  # pragma: no cover - dependência do requirements
        raise ImproperlyConfigured(
            'DATABASE_URL exige o pacote dj-database-url (veja requirements.txt).'
        ) from exc
    DATABASES = {
        'default': dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=int(os.environ.get('DJANGO_CONN_MAX_AGE', '600')),
            conn_health_checks=True,
        ),
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
            'OPTIONS': {
                # WAL + espera no lock reduzem o "database is locked" quando dois
                # pedidos chegam juntos.
                'timeout': 20,
                'init_command': 'PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;',
            },
        },
    }


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 8},
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'accounts.validators.UppercaseValidator',
    },
    {
        'NAME': 'accounts.validators.LowercaseValidator',
    },
    {
        'NAME': 'accounts.validators.NumberValidator',
    },
    {
        'NAME': 'accounts.validators.SpecialCharacterValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'pt-br'

TIME_ZONE = 'America/Sao_Paulo'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
# Destino do `manage.py collectstatic` (o WhiteNoise serve a partir daqui).
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        # Gera as versões comprimidas (.gz/.br) no collectstatic. Sem hash no
        # nome do arquivo: o cache busting é o `?v=N` do base.html.
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}

# Uploads (logo, fotos dos itens) em deploy simples de um servidor só. O ideal é
# servir /media/ pelo nginx ou por um bucket; ligue isto apenas se não houver
# outra camada na frente.
SERVE_MEDIA = env_bool('DJANGO_SERVE_MEDIA', False)

CART_SESSION_ID = 'onmenu_cart'
CURRENCY_SYMBOL = 'R$'
LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'orders:staff_order_list'
LOGOUT_REDIRECT_URL = 'menu:menu_list'

# Clientes entram pelo e-mail (o usuário é um @handle gerado); a equipe ainda
# pode usar o nome de usuário. O ModelBackend padrão fica como fallback.
AUTHENTICATION_BACKENDS = [
    'accounts.backends.EmailOrUsernameModelBackend',
    'django.contrib.auth.backends.ModelBackend',
]


# ── Segurança ───────────────────────────────────────────────────────────────
# Em DEBUG nada disso entra em vigor (o runserver é HTTP puro).

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
X_FRAME_OPTIONS = 'DENY'

# Atrás de um proxy/load balancer que termina o TLS (nginx, Cloudflare, Heroku,
# Render…), o Django precisa confiar no cabeçalho para reconhecer o HTTPS.
# Só ligue quando existir mesmo esse proxy — senão o cabeçalho pode ser forjado.
if env_bool('DJANGO_BEHIND_PROXY', False):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    USE_X_FORWARDED_HOST = True

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Redirecionar HTTP→HTTPS sem o proxy configurado acima causa loop de
    # redirect; por isso é desligável.
    SECURE_SSL_REDIRECT = env_bool('DJANGO_SECURE_SSL_REDIRECT', True)
    # 30 dias. Suba para 1 ano (31536000) depois de validar o HTTPS.
    SECURE_HSTS_SECONDS = int(os.environ.get('DJANGO_HSTS_SECONDS', str(60 * 60 * 24 * 30)))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool('DJANGO_HSTS_INCLUDE_SUBDOMAINS', True)
    SECURE_HSTS_PRELOAD = env_bool('DJANGO_HSTS_PRELOAD', False)


# ── Logs ────────────────────────────────────────────────────────────────────
# Console (capturado pelo systemd/docker) + e-mail para os ADMINS nos erros 500.

LOG_LEVEL = os.environ.get('DJANGO_LOG_LEVEL', 'INFO')

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'require_debug_false': {'()': 'django.utils.log.RequireDebugFalse'},
    },
    'formatters': {
        'simple': {
            'format': '{asctime} {levelname} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
        'mail_admins': {
            'level': 'ERROR',
            'class': 'django.utils.log.AdminEmailHandler',
            'filters': ['require_debug_false'],
            'include_html': True,
        },
    },
    'root': {'handlers': ['console'], 'level': 'WARNING'},
    'loggers': {
        'django.request': {
            'handlers': ['console', 'mail_admins'],
            'level': 'ERROR',
            'propagate': False,
        },
        # Nossos apps: pagamentos, WhatsApp e notificações registram o que
        # aconteceu com cada pedido.
        'accounts': {'level': LOG_LEVEL},
        'cart': {'level': LOG_LEVEL},
        'menu': {'level': LOG_LEVEL},
        'orders': {'level': LOG_LEVEL},
    },
}

# Destinatários dos erros 500 (ex.: DJANGO_ADMINS=dono@restaurante.com).
ADMINS = [('OnMenu', email) for email in env_list('DJANGO_ADMINS')]
MANAGERS = ADMINS


# E-mail: com SMTP configurado no .env (EMAIL_HOST_USER/PASSWORD), os e-mails de
# recuperação de senha são enviados de verdade. Sem essas credenciais, o backend
# cai para o console do runserver — análogo ao modo "mock" dos pagamentos.
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_USE_TLS = env_bool('EMAIL_USE_TLS', True)
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')

# Backend padrão: SMTP quando há credenciais, senão imprime no console.
_default_email_backend = (
    'django.core.mail.backends.smtp.EmailBackend'
    if EMAIL_HOST_USER and EMAIL_HOST_PASSWORD
    else 'django.core.mail.backends.console.EmailBackend'
)
EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', _default_email_backend)
DEFAULT_FROM_EMAIL = os.environ.get(
    'DEFAULT_FROM_EMAIL', EMAIL_HOST_USER or 'OnMenu <nao-responder@onmenu.com.br>'
)
# Remetente dos e-mails de erro enviados aos ADMINS.
SERVER_EMAIL = os.environ.get('DJANGO_SERVER_EMAIL', DEFAULT_FROM_EMAIL)

# --- Mercado Pago / Pix ---
# Access token da conta Mercado Pago. Sem token, o sistema roda em modo "mock"
# (QR Code placeholder) para permitir testar a UI sem cobrança real.
MERCADOPAGO_ACCESS_TOKEN = os.environ.get('MERCADOPAGO_ACCESS_TOKEN', '')
# Chave pública usada pelo SDK MercadoPago.js no browser (tokenização do cartão).
MERCADOPAGO_PUBLIC_KEY = os.environ.get('MERCADOPAGO_PUBLIC_KEY', '')
# Secret usado para validar a assinatura (x-signature) dos webhooks do MP.
# Obrigatório quando há access token: sem ele os webhooks são recusados.
MERCADOPAGO_WEBHOOK_SECRET = os.environ.get('MERCADOPAGO_WEBHOOK_SECRET', '')
# Mock ligado automaticamente quando não há token configurado.
MERCADOPAGO_MOCK = not MERCADOPAGO_ACCESS_TOKEN
# Validade da cobrança Pix, em minutos.
PIX_EXPIRATION_MINUTES = 30
# E-mail do pagador enviado ao Mercado Pago (não coletamos e-mail no checkout).
PIX_DEFAULT_PAYER_EMAIL = os.environ.get('PIX_DEFAULT_PAYER_EMAIL', 'comprador@onmenu.com.br')

# --- Login social com Google (OAuth2) ---
# Credenciais do OAuth client (tipo "Web application") criado no Google Cloud
# Console. O redirect autorizado deve ser BASE_URL + /accounts/entrar/google/callback/.
# Sem as duas credenciais, o recurso fica desligado e o botão "Continuar com
# Google" não é exibido — análogo ao modo mock dos outros serviços externos.
GOOGLE_OAUTH_CLIENT_ID = os.environ.get('GOOGLE_OAUTH_CLIENT_ID', '')
GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET', '')
GOOGLE_OAUTH_ENABLED = bool(GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET)

# --- WhatsApp (Cloud API) ---
# Token e phone number id da WhatsApp Cloud API (Meta). Sem token/phone id, o
# sistema roda em modo "mock": as mensagens são apenas registradas no log, sem
# envio real — análogo ao modo mock do Mercado Pago.
WHATSAPP_TOKEN = os.environ.get('WHATSAPP_TOKEN', '')
WHATSAPP_PHONE_ID = os.environ.get('WHATSAPP_PHONE_ID', '')
WHATSAPP_API_VERSION = os.environ.get('WHATSAPP_API_VERSION', 'v21.0')
# DDI adicionado a telefones sem código do país (Brasil = 55).
WHATSAPP_DEFAULT_COUNTRY_CODE = os.environ.get('WHATSAPP_DEFAULT_COUNTRY_CODE', '55')
# Mock ligado automaticamente quando faltam credenciais.
WHATSAPP_MOCK = not (WHATSAPP_TOKEN and WHATSAPP_PHONE_ID)

TEST_RUNNER = 'config.test_runner.QuietTestRunner'

# Backups do banco (`manage.py backup_db`).
BACKUP_ROOT = Path(os.environ.get('DJANGO_BACKUP_ROOT', BASE_DIR / 'backups'))
BACKUP_KEEP = int(os.environ.get('DJANGO_BACKUP_KEEP', '14'))

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
