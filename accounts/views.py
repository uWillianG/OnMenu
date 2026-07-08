import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView
from django.http import JsonResponse
from django.shortcuts import redirect, render, resolve_url
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from orders.models import City, Order

from .forms import (
    AddressForm,
    LoginForm,
    ProfileForm,
    SignupForm,
    _generate_username,
    _split_full_name,
)
from .models import Profile
from .services import google as google_oauth

# Chaves da sessão usadas no fluxo OAuth2 do Google.
GOOGLE_STATE_SESSION_KEY = 'google_oauth_state'
GOOGLE_NEXT_SESSION_KEY = 'google_oauth_next'


class CustomLoginView(LoginView):
    template_name = 'registration/login.html'
    authentication_form = LoginForm
    redirect_authenticated_user = True
    extra_context = {'hide_cart': True}

    def get_default_redirect_url(self):
        """Sem ?next=: todos vão para a tela principal (cardápio)."""
        return resolve_url('menu:menu_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['google_oauth_enabled'] = settings.GOOGLE_OAUTH_ENABLED
        return context


def signup(request):
    """Cadastro de conta. Faz login automático e respeita o ?next=."""
    next_url = request.POST.get('next') or request.GET.get('next', '')

    if request.user.is_authenticated:
        return redirect(_safe_next(request, next_url))

    if request.method == 'POST':
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            # Com múltiplos backends configurados, o login automático precisa
            # saber qual usar para autenticar a sessão recém-criada.
            login(request, user, backend='accounts.backends.EmailOrUsernameModelBackend')
            return redirect(_safe_next(request, next_url))
    else:
        form = SignupForm()

    return render(
        request,
        'registration/signup.html',
        {
            'form': form,
            'next': next_url,
            'hide_cart': True,
            'google_oauth_enabled': settings.GOOGLE_OAUTH_ENABLED,
        },
    )


def google_login(request):
    """Inicia o fluxo OAuth2: gera o ``state`` e redireciona ao Google."""
    if not settings.GOOGLE_OAUTH_ENABLED:
        messages.error(request, 'Login com Google indisponível no momento.')
        return redirect('accounts:login')

    if request.user.is_authenticated:
        return redirect(_safe_next(request, request.GET.get('next', '')))

    state = secrets.token_urlsafe(32)
    request.session[GOOGLE_STATE_SESSION_KEY] = state
    request.session[GOOGLE_NEXT_SESSION_KEY] = request.GET.get('next', '')
    return redirect(google_oauth.build_authorization_url(request, state))


def google_callback(request):
    """Recebe o retorno do Google, valida, e loga (ou cria) o usuário."""
    if not settings.GOOGLE_OAUTH_ENABLED:
        return redirect('accounts:login')

    # O usuário pode ter negado a permissão no Google.
    if request.GET.get('error'):
        messages.error(request, 'Login com Google cancelado.')
        return redirect('accounts:login')

    # Confere o state contra CSRF; consome-o da sessão em qualquer caso.
    expected_state = request.session.pop(GOOGLE_STATE_SESSION_KEY, None)
    next_url = request.session.pop(GOOGLE_NEXT_SESSION_KEY, '')
    state = request.GET.get('state')
    if not state or state != expected_state:
        messages.error(request, 'Falha na verificação de segurança do login. Tente novamente.')
        return redirect('accounts:login')

    code = request.GET.get('code')
    if not code:
        messages.error(request, 'Não recebemos a autorização do Google.')
        return redirect('accounts:login')

    try:
        tokens = google_oauth.exchange_code(request, code)
        userinfo = google_oauth.fetch_userinfo(tokens['access_token'])
    except (google_oauth.GoogleOAuthError, KeyError):
        messages.error(request, 'Não foi possível entrar com o Google. Tente novamente.')
        return redirect('accounts:login')

    email = (userinfo.get('email') or '').strip()
    if not email or not userinfo.get('email_verified', False):
        messages.error(request, 'Sua conta Google não tem um e-mail verificado.')
        return redirect('accounts:login')

    user, created = _get_or_create_google_user(email, userinfo)
    login(request, user, backend='accounts.backends.EmailOrUsernameModelBackend')
    if created:
        messages.success(
            request,
            'Conta criada com o Google! Você poderá informar CPF e telefone no '
            'primeiro pedido.',
        )
    return redirect(_safe_next(request, next_url))


def _get_or_create_google_user(email, userinfo):
    """Casa por e-mail uma conta existente ou cria uma nova (sem CPF/telefone).

    Contas criadas via Google ficam sem senha utilizável (``set_unusable_password``)
    e sem CPF/telefone — esses dados são coletados depois, no checkout.
    """
    user = User.objects.filter(email__iexact=email).order_by('id').first()
    if user:
        return user, False

    first = (userinfo.get('given_name') or '').strip()
    last = (userinfo.get('family_name') or '').strip()
    if not first:
        first, last = _split_full_name(userinfo.get('name') or email.split('@')[0])

    user = User(
        username=_generate_username(first, last or first),
        email=email,
        first_name=first,
        last_name=last,
    )
    user.set_unusable_password()
    user.save()  # o signal post_save cria o Profile (CPF/telefone em branco).
    return user, True


@login_required
def profile(request):
    """Perfil do cliente: dados da conta + endereço (editáveis) + histórico."""
    profile_obj, _ = Profile.objects.get_or_create(user=request.user)

    form = ProfileForm(instance=request.user)
    address_form = AddressForm(instance=profile_obj)

    if request.method == 'POST':
        # Distingue qual seção foi enviada (dados da conta x endereço).
        if request.POST.get('form_kind') == 'address':
            address_form = AddressForm(request.POST, instance=profile_obj)
            if address_form.is_valid():
                address_form.save()
                messages.success(request, 'Endereço atualizado com sucesso.')
                return redirect('accounts:profile')
        else:
            form = ProfileForm(request.POST, instance=request.user)
            if form.is_valid():
                form.save()
                messages.success(request, 'Dados atualizados com sucesso.')
                return redirect('accounts:profile')

    return render(
        request,
        'registration/profile.html',
        {
            'form': form,
            'address_form': address_form,
            'delivery_areas': _delivery_areas_data(),
        },
    )


@login_required
def order_history(request):
    """Tela de consulta: lista todos os pedidos do usuário."""
    orders = (
        Order.objects
        .filter(user=request.user)
        .prefetch_related('items__options')
        .order_by('-created_at')
    )
    return render(request, 'registration/order_history.html', {'orders': orders})


@login_required
@require_POST
def save_address(request):
    """Salva no perfil o endereço digitado no checkout (via AJAX).

    Reaproveita os mesmos nomes de campo do CheckoutForm (city, neighborhood,
    address_street, address_number, address_complement), então o JS do checkout
    pode enviar a própria seção de endereço sem montar um payload separado.
    """
    profile_obj, _ = Profile.objects.get_or_create(user=request.user)
    form = AddressForm(request.POST, instance=profile_obj)
    if form.is_valid():
        form.save()
        return JsonResponse({'ok': True})
    return JsonResponse({'ok': False, 'errors': form.errors}, status=400)


def _delivery_areas_data():
    """Cidades/bairros ativos (id, nome) para o seletor dependente do perfil."""
    cities = City.objects.filter(is_active=True).prefetch_related('neighborhoods')
    return {
        str(city.id): {
            'name': city.name,
            'neighborhoods': [
                {'id': n.id, 'name': n.name}
                for n in city.neighborhoods.all()
                if n.is_active
            ],
        }
        for city in cities
    }


def _safe_next(request, next_url):
    """Evita open redirect: só permite destinos do próprio host."""
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return 'menu:menu_list'
