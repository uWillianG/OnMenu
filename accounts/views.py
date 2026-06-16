from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.http import JsonResponse
from django.shortcuts import redirect, render, resolve_url
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from orders.models import City, Order

from .forms import AddressForm, LoginForm, ProfileForm, SignupForm
from .models import Profile


class CustomLoginView(LoginView):
    template_name = 'registration/login.html'
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def get_default_redirect_url(self):
        """Sem ?next=: staff vai ao painel; cliente vai ao cardápio.

        Evita mandar um cliente comum para uma página @staff_member_required
        (que o jogaria para o login do admin)."""
        if self.request.user.is_staff:
            return resolve_url('orders:staff_order_list')
        return resolve_url('menu:menu_list')


def signup(request):
    """Cadastro de conta. Faz login automático e respeita o ?next=."""
    next_url = request.POST.get('next') or request.GET.get('next', '')

    if request.user.is_authenticated:
        return redirect(_safe_next(request, next_url))

    if request.method == 'POST':
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect(_safe_next(request, next_url))
    else:
        form = SignupForm()

    return render(request, 'registration/signup.html', {'form': form, 'next': next_url})


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

    orders = (
        Order.objects
        .filter(user=request.user)
        .prefetch_related('items__options')
        .order_by('-created_at')
    )

    return render(
        request,
        'registration/profile.html',
        {
            'form': form,
            'address_form': address_form,
            'orders': orders,
            'delivery_areas': _delivery_areas_data(),
        },
    )


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
