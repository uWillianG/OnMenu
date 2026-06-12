from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render, resolve_url
from django.utils.http import url_has_allowed_host_and_scheme

from .forms import LoginForm, SignupForm


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


def _safe_next(request, next_url):
    """Evita open redirect: só permite destinos do próprio host."""
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return 'menu:menu_list'
