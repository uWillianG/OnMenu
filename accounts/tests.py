from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import throttle
from accounts.models import AccessAttempt
from accounts.views import (
    GOOGLE_NEXT_SESSION_KEY,
    GOOGLE_STATE_SESSION_KEY,
    ORDER_HISTORY_PAGE_SIZE,
)
from menu.models import Restaurant
from orders.models import Order

GOOGLE_ENABLED = override_settings(
    GOOGLE_OAUTH_CLIENT_ID='client-id',
    GOOGLE_OAUTH_CLIENT_SECRET='client-secret',
    GOOGLE_OAUTH_ENABLED=True,
)

# Força o modo desligado independentemente do que houver no .env do ambiente.
GOOGLE_DISABLED = override_settings(
    GOOGLE_OAUTH_CLIENT_ID='',
    GOOGLE_OAUTH_CLIENT_SECRET='',
    GOOGLE_OAUTH_ENABLED=False,
)


class AuthFlowTests(TestCase):
    def test_login_and_signup_pages_render(self):
        self.assertEqual(self.client.get(reverse('accounts:login')).status_code, 200)
        self.assertEqual(self.client.get(reverse('accounts:signup')).status_code, 200)

    def test_login_page_links_to_signup_preserving_next(self):
        url = reverse('accounts:login') + '?next=' + reverse('orders:checkout')
        html = self.client.get(url).content.decode()
        self.assertIn(reverse('accounts:signup'), html)
        # next deve estar embutido no formulário para redirecionar após login
        self.assertIn('value="' + reverse('orders:checkout') + '"', html)

    def test_signup_creates_user_logs_in_and_honors_next(self):
        response = self.client.post(
            reverse('accounts:signup'),
            {
                'full_name': 'João Silva',
                'email': 'cliente1@example.com',
                'phone': '(11) 99999-9999',
                'cpf': '111.444.777-35',
                'password1': 'Sup3rSecret!9',
                'password2': 'Sup3rSecret!9',
                'next': reverse('orders:checkout'),
            },
        )
        self.assertRedirects(
            response, reverse('orders:checkout'), fetch_redirect_response=False
        )
        user = User.objects.get(email='cliente1@example.com')
        # Nome dividido em primeiro/sobrenome e @handle gerado automaticamente.
        self.assertEqual(user.first_name, 'João')
        self.assertEqual(user.last_name, 'Silva')
        self.assertEqual(user.username, '@joaosilva')
        # CPF guardado só em dígitos no Profile.
        self.assertEqual(user.profile.cpf, '11144477735')
        self.assertIn('_auth_user_id', self.client.session)

    def test_signup_handle_uses_first_and_last_name_only(self):
        self.client.post(reverse('accounts:signup'), {
            'full_name': 'Maria de Souza',
            'email': 'maria.souza@example.com',
            'phone': '(11) 99999-9999',
            'cpf': '111.444.777-35',
            'password1': 'Sup3rSecret!9',
            'password2': 'Sup3rSecret!9',
        })
        user = User.objects.get(email='maria.souza@example.com')
        self.assertEqual(user.username, '@mariasouza')   # ignora "de"
        self.assertEqual(user.first_name, 'Maria')
        self.assertEqual(user.last_name, 'de Souza')     # nome completo preservado

    def test_signup_appends_suffix_for_duplicate_handle(self):
        base = {
            'phone': '(11) 99999-9999',
            'password1': 'Sup3rSecret!9',
            'password2': 'Sup3rSecret!9',
        }
        self.client.post(reverse('accounts:signup'), {
            **base, 'full_name': 'João Silva',
            'email': 'a@example.com', 'cpf': '111.444.777-35',
        })
        self.client.logout()
        self.client.post(reverse('accounts:signup'), {
            **base, 'full_name': 'João Silva',
            'email': 'b@example.com', 'cpf': '529.982.247-25',
        })
        self.assertTrue(User.objects.filter(username='@joaosilva').exists())
        self.assertTrue(User.objects.filter(username='@joaosilva2').exists())

    def test_login_with_email(self):
        self.client.post(reverse('accounts:signup'), {
            'full_name': 'Maria Souza',
            'email': 'maria@example.com',
            'phone': '(11) 99999-9999',
            'cpf': '111.444.777-35',
            'password1': 'Sup3rSecret!9',
            'password2': 'Sup3rSecret!9',
        })
        self.client.logout()
        response = self.client.post(
            reverse('accounts:login'),
            {'username': 'maria@example.com', 'password': 'Sup3rSecret!9'},
        )
        self.assertRedirects(
            response, reverse('menu:menu_list'), fetch_redirect_response=False
        )

    def test_signup_rejects_open_redirect(self):
        response = self.client.post(
            reverse('accounts:signup'),
            {
                'full_name': 'Carlos Lima',
                'email': 'cliente2@example.com',
                'phone': '(11) 99999-9999',
                'cpf': '111.444.777-35',
                'password1': 'Sup3rSecret!9',
                'password2': 'Sup3rSecret!9',
                'next': 'https://evil.example.com/phish',
            },
        )
        self.assertRedirects(
            response, reverse('menu:menu_list'), fetch_redirect_response=False
        )

    def test_login_redirects_to_next(self):
        User.objects.create_user(username='cliente3', password='Sup3rSecret!9')
        response = self.client.post(
            reverse('accounts:login'),
            {
                'username': 'cliente3',
                'password': 'Sup3rSecret!9',
                'next': reverse('orders:checkout'),
            },
        )
        self.assertRedirects(
            response, reverse('orders:checkout'), fetch_redirect_response=False
        )

    def test_signup_requires_full_name_with_surname(self):
        response = self.client.post(
            reverse('accounts:signup'),
            {
                'full_name': 'Joao',
                'email': 'so_nome@example.com',
                'phone': '(11) 99999-9999',
                'cpf': '111.444.777-35',
                'password1': 'Sup3rSecret!9',
                'password2': 'Sup3rSecret!9',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('full_name', response.context['form'].errors)

    def test_signup_requires_valid_cpf(self):
        response = self.client.post(
            reverse('accounts:signup'),
            {
                'full_name': 'João Silva',
                'email': 'cpf_curto@example.com',
                'phone': '(11) 99999-9999',
                'cpf': '123',
                'password1': 'Sup3rSecret!9',
                'password2': 'Sup3rSecret!9',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('cpf', response.context['form'].errors)

    def test_signup_rejects_weak_password(self):
        response = self.client.post(
            reverse('accounts:signup'),
            {
                'full_name': 'Cliente Fraco',
                'email': 'fraco@example.com',
                'phone': '(11) 99999-9999',
                'cpf': '111.444.777-35',
                'password1': 'abcdefgh',
                'password2': 'abcdefgh',
            },
        )
        self.assertEqual(response.status_code, 200)
        errors = ' '.join(response.context['form'].errors['password2'])
        self.assertIn('letra maiúscula', errors)
        self.assertIn('número', errors)
        self.assertIn('caractere especial', errors)

    def test_signup_rejects_invalid_email(self):
        response = self.client.post(
            reverse('accounts:signup'),
            {
                'full_name': 'Cliente Email',
                'email': 'nao-e-email',
                'phone': '(11) 99999-9999',
                'cpf': '111.444.777-35',
                'password1': 'Sup3rSecret!9',
                'password2': 'Sup3rSecret!9',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('email', response.context['form'].errors)

    def test_login_without_next_sends_customer_to_menu(self):
        User.objects.create_user(username='cliente_sem_next', password='Sup3rSecret!9')
        response = self.client.post(
            reverse('accounts:login'),
            {'username': 'cliente_sem_next', 'password': 'Sup3rSecret!9'},
        )
        self.assertRedirects(
            response, reverse('menu:menu_list'), fetch_redirect_response=False
        )

    def test_login_without_next_sends_staff_to_home(self):
        User.objects.create_user(
            username='equipe', password='Sup3rSecret!9', is_staff=True
        )
        response = self.client.post(
            reverse('accounts:login'),
            {'username': 'equipe', 'password': 'Sup3rSecret!9'},
        )
        self.assertRedirects(
            response,
            reverse('menu:menu_list'),
            fetch_redirect_response=False,
        )

    def test_login_form_labels_in_portuguese(self):
        html = self.client.get(reverse('accounts:login')).content.decode()
        self.assertIn('E-mail ou usuário', html)
        self.assertIn('Senha', html)
        self.assertNotIn('>Username<', html)
        self.assertNotIn('>Password<', html)

    def test_signup_validation_errors_in_portuguese(self):
        response = self.client.post(
            reverse('accounts:signup'),
            {
                'full_name': 'Cliente Pt',
                'email': 'cliente_pt@example.com',
                'phone': '(11) 99999-9999',
                'cpf': '111.444.777-35',
                'password1': 'Sup3rSecret!9',
                'password2': 'naoConfere!9',
            },
        )
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertIn(
            'Os dois campos de senha não correspondem.',
            form.errors['password2'],
        )

    def test_signup_renders_password_toggle_and_live_checklist(self):
        html = self.client.get(reverse('accounts:signup')).content.decode()
        # Botão de exibir/ocultar em ambos os campos de senha
        self.assertIn('data-toggle-for="id_password1"', html)
        self.assertIn('data-toggle-for="id_password2"', html)
        # Checklist de requisitos da senha
        self.assertIn('id="password-reqs"', html)
        self.assertIn('data-req="length"', html)
        self.assertIn('data-req="special"', html)
        # Script de comportamento incluído
        self.assertIn('js/auth.js', html)

    def test_login_renders_password_toggle(self):
        html = self.client.get(reverse('accounts:login')).content.decode()
        self.assertIn('data-toggle-for="id_password"', html)
        self.assertIn('js/auth.js', html)

    def test_signup_shows_email_help_text(self):
        html = self.client.get(reverse('accounts:signup')).content.decode()
        self.assertIn('Digite um e-mail válido.', html)

    def test_login_shows_password_reset_link(self):
        html = self.client.get(reverse('accounts:login')).content.decode()
        self.assertIn(reverse('accounts:password_reset'), html)
        self.assertIn('Esqueceu a senha?', html)

    def test_password_reset_sends_email_with_link(self):
        from django.core import mail

        User.objects.create_user(
            username='cliente_reset',
            email='reset@example.com',
            password='Sup3rSecret!9',
        )
        response = self.client.post(
            reverse('accounts:password_reset'), {'email': 'reset@example.com'}
        )
        self.assertRedirects(response, reverse('accounts:password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/accounts/reset/', mail.outbox[0].body)
        self.assertIn('OnMenu', mail.outbox[0].subject)

    def test_password_reset_confirm_sets_new_password(self):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        user = User.objects.create_user(
            username='cliente_reset2',
            email='reset2@example.com',
            password='OldPass!123',
        )
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)

        # GET inicial redireciona para a URL com 'set-password' na sessão
        confirm_url = reverse(
            'accounts:password_reset_confirm',
            kwargs={'uidb64': uid, 'token': token},
        )
        response = self.client.get(confirm_url, follow=True)
        self.assertEqual(response.status_code, 200)

        # POST da nova senha (URL com token trocado por 'set-password')
        post_url = response.redirect_chain[-1][0] if response.redirect_chain else confirm_url
        response = self.client.post(
            post_url,
            {
                'new_password1': 'BrandNew!99',
                'new_password2': 'BrandNew!99',
            },
        )
        self.assertRedirects(
            response, reverse('accounts:password_reset_complete')
        )
        user.refresh_from_db()
        self.assertTrue(user.check_password('BrandNew!99'))

    def test_cart_shows_auth_choice_for_anonymous_only(self):
        # Anônimo: botão abre o modal de escolha
        html = self.client.get(reverse('cart:cart_detail')).content.decode()
        self.assertIn('data-auth-open', html)

        User.objects.create_user(username='cliente4', password='Sup3rSecret!9')
        self.client.login(username='cliente4', password='Sup3rSecret!9')
        html = self.client.get(reverse('cart:cart_detail')).content.decode()
        self.assertNotIn('data-auth-open', html)


class GoogleOAuthTests(TestCase):
    """Login social com Google (fluxo OAuth2 redirect)."""

    def _prime_state(self, state='the-state', next_url=''):
        """Simula o estado deixado por google_login na sessão."""
        session = self.client.session
        session[GOOGLE_STATE_SESSION_KEY] = state
        session[GOOGLE_NEXT_SESSION_KEY] = next_url
        session.save()

    @GOOGLE_DISABLED
    def test_button_hidden_when_disabled(self):
        for name in ('accounts:login', 'accounts:signup'):
            html = self.client.get(reverse(name)).content.decode()
            self.assertNotIn('Continuar com Google', html)

    @GOOGLE_ENABLED
    def test_button_shown_when_enabled(self):
        for name in ('accounts:login', 'accounts:signup'):
            html = self.client.get(reverse(name)).content.decode()
            self.assertIn('Continuar com Google', html)
            self.assertIn(reverse('accounts:google_login'), html)

    @GOOGLE_DISABLED
    def test_google_login_disabled_redirects_to_login(self):
        response = self.client.get(reverse('accounts:google_login'))
        self.assertRedirects(response, reverse('accounts:login'))

    @GOOGLE_ENABLED
    def test_google_login_sets_state_and_redirects_to_google(self):
        response = self.client.get(reverse('accounts:google_login') + '?next=/cart/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('https://accounts.google.com/'))
        self.assertIn(GOOGLE_STATE_SESSION_KEY, self.client.session)
        self.assertEqual(self.client.session[GOOGLE_NEXT_SESSION_KEY], '/cart/')

    @GOOGLE_ENABLED
    @patch('accounts.services.google.fetch_userinfo')
    @patch('accounts.services.google.exchange_code')
    def test_callback_creates_new_user_without_cpf(self, mock_exchange, mock_userinfo):
        mock_exchange.return_value = {'access_token': 'tok'}
        mock_userinfo.return_value = {
            'email': 'novo@gmail.com',
            'email_verified': True,
            'given_name': 'Novo',
            'family_name': 'Cliente',
        }
        self._prime_state()
        response = self.client.get(
            reverse('accounts:google_callback') + '?state=the-state&code=abc'
        )
        self.assertRedirects(
            response, reverse('menu:menu_list'), fetch_redirect_response=False
        )
        user = User.objects.get(email='novo@gmail.com')
        self.assertEqual(user.first_name, 'Novo')
        self.assertEqual(user.username, '@novocliente')
        # Conta via Google nasce sem CPF/telefone e sem senha utilizável.
        self.assertEqual(user.profile.cpf, '')
        self.assertEqual(user.profile.phone, '')
        self.assertFalse(user.has_usable_password())
        self.assertIn('_auth_user_id', self.client.session)

    @GOOGLE_ENABLED
    @patch('accounts.services.google.fetch_userinfo')
    @patch('accounts.services.google.exchange_code')
    def test_callback_matches_existing_user_by_email(self, mock_exchange, mock_userinfo):
        existing = User.objects.create_user(
            username='@existente', email='ja@gmail.com', password='Sup3rSecret!9'
        )
        mock_exchange.return_value = {'access_token': 'tok'}
        mock_userinfo.return_value = {
            'email': 'JA@gmail.com',  # e-mail casa sem diferenciar maiúsculas
            'email_verified': True,
            'given_name': 'Ja',
        }
        self._prime_state(next_url=reverse('orders:checkout'))
        response = self.client.get(
            reverse('accounts:google_callback') + '?state=the-state&code=abc'
        )
        self.assertRedirects(
            response, reverse('orders:checkout'), fetch_redirect_response=False
        )
        self.assertEqual(User.objects.filter(email__iexact='ja@gmail.com').count(), 1)
        self.assertEqual(int(self.client.session['_auth_user_id']), existing.pk)

    @GOOGLE_ENABLED
    def test_callback_rejects_mismatched_state(self):
        self._prime_state(state='esperado')
        response = self.client.get(
            reverse('accounts:google_callback') + '?state=forjado&code=abc'
        )
        self.assertRedirects(response, reverse('accounts:login'))
        self.assertNotIn('_auth_user_id', self.client.session)

    @GOOGLE_ENABLED
    @patch('accounts.services.google.fetch_userinfo')
    @patch('accounts.services.google.exchange_code')
    def test_callback_rejects_unverified_email(self, mock_exchange, mock_userinfo):
        mock_exchange.return_value = {'access_token': 'tok'}
        mock_userinfo.return_value = {
            'email': 'naoverificado@gmail.com',
            'email_verified': False,
            'given_name': 'Nao',
        }
        self._prime_state()
        response = self.client.get(
            reverse('accounts:google_callback') + '?state=the-state&code=abc'
        )
        self.assertRedirects(response, reverse('accounts:login'))
        self.assertFalse(User.objects.filter(email='naoverificado@gmail.com').exists())


class PhoneAndProfileTests(TestCase):
    def test_signup_requires_phone(self):
        response = self.client.post(
            reverse('accounts:signup'),
            {
                'full_name': 'Sem Telefone',
                'email': 'sem_tel@example.com',
                'cpf': '111.444.777-35',
                'password1': 'Sup3rSecret!9',
                'password2': 'Sup3rSecret!9',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('phone', response.context['form'].errors)

    def test_signup_saves_phone_and_cpf_to_profile(self):
        self.client.post(
            reverse('accounts:signup'),
            {
                'full_name': 'Com Telefone',
                'email': 'com_tel@example.com',
                'phone': '(11) 98888-7777',
                'cpf': '111.444.777-35',
                'password1': 'Sup3rSecret!9',
                'password2': 'Sup3rSecret!9',
            },
        )
        user = User.objects.get(email='com_tel@example.com')
        self.assertEqual(user.profile.phone, '(11) 98888-7777')
        self.assertEqual(user.profile.cpf, '11144477735')

    def test_profile_shows_name_username_and_cpf_read_only(self):
        user = User.objects.create_user(
            username='@leitorteste', password='Sup3rSecret!9',
            email='leitor@example.com', first_name='Leitor', last_name='Teste',
        )
        user.profile.phone = '(21) 3333-4444'
        user.profile.cpf = '11144477735'
        user.profile.save()
        self.client.force_login(user)
        html = self.client.get(reverse('accounts:profile')).content.decode()
        self.assertIn('(21) 3333-4444', html)
        self.assertIn('Leitor Teste', html)
        # Usuário e CPF aparecem (formatado), mas só para leitura.
        self.assertIn('@leitorteste', html)
        self.assertIn('111.444.777-35', html)
        # CPF e usuário não têm campo no formulário de edição.
        form = self.client.get(reverse('accounts:profile')).context['form']
        self.assertNotIn('cpf', form.fields)
        self.assertNotIn('username', form.fields)

    def test_profile_updates_name_and_phone(self):
        user = User.objects.create_user(
            username='@editor', password='Sup3rSecret!9', email='editor@example.com'
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse('accounts:profile'),
            {
                'full_name': 'Editor Atualizado',
                'email': 'editor@example.com',
                'phone': '(31) 2222-1111',
            },
        )
        self.assertRedirects(response, reverse('accounts:profile'))
        user.refresh_from_db()
        user.profile.refresh_from_db()
        self.assertEqual(user.first_name, 'Editor')
        self.assertEqual(user.last_name, 'Atualizado')
        self.assertEqual(user.profile.phone, '(31) 2222-1111')
        # O @handle não muda ao editar o nome no perfil.
        self.assertEqual(user.username, '@editor')

    def test_profile_rejects_invalid_phone(self):
        user = User.objects.create_user(
            username='@invalido', password='Sup3rSecret!9', email='invalido@example.com'
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse('accounts:profile'),
            {
                'full_name': 'Invalido Teste',
                'email': 'invalido@example.com',
                'phone': '123',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('phone', response.context['form'].errors)


class AddressTests(TestCase):
    def setUp(self):
        from orders.models import City, Neighborhood

        self.city = City.objects.create(name='São Paulo', delivery_fee='5.00')
        self.other_city = City.objects.create(name='Guarulhos', delivery_fee='8.00')
        self.nb = Neighborhood.objects.create(
            city=self.city, name='Centro', delivery_fee='2.00'
        )
        self.other_nb = Neighborhood.objects.create(
            city=self.other_city, name='Bonsucesso', delivery_fee='3.00'
        )
        self.user = User.objects.create_user(
            username='morador', password='Sup3rSecret!9', email='morador@example.com'
        )

    def test_profile_saves_address(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('accounts:profile'),
            {
                'form_kind': 'address',
                'city': self.city.id,
                'neighborhood': self.nb.id,
                'address_street': 'Rua das Flores',
                'address_number': '100',
                'address_complement': 'Ap 12',
            },
        )
        self.assertRedirects(response, reverse('accounts:profile'))
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.address_street, 'Rua das Flores')
        self.assertEqual(self.user.profile.address_number, '100')
        self.assertEqual(self.user.profile.city_id, self.city.id)
        self.assertEqual(self.user.profile.neighborhood_id, self.nb.id)

    def test_profile_rejects_neighborhood_from_other_city(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('accounts:profile'),
            {
                'form_kind': 'address',
                'city': self.city.id,
                'neighborhood': self.other_nb.id,
                'address_street': 'Rua X',
                'address_number': '1',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('neighborhood', response.context['address_form'].errors)

    def test_profile_shows_saved_address_in_read_mode(self):
        p = self.user.profile
        p.address_street = 'Av. Brasil'
        p.address_number = '500'
        p.city = self.city
        p.neighborhood = self.nb
        p.save()
        self.client.force_login(self.user)
        html = self.client.get(reverse('accounts:profile')).content.decode()
        self.assertIn('Av. Brasil', html)
        self.assertIn('Centro', html)
        self.assertIn('São Paulo', html)

    def test_checkout_prefills_from_profile(self):
        self.user.first_name = 'Maria'
        self.user.last_name = 'Souza'
        self.user.save()
        p = self.user.profile
        p.phone = '(11) 91234-5678'
        p.address_street = 'Av. Brasil'
        p.address_number = '500'
        p.city = self.city
        p.neighborhood = self.nb
        p.save()
        self.client.force_login(self.user)

        self._add_item_to_cart()
        html = self.client.get(reverse('orders:checkout')).content.decode()
        self.assertIn('Maria Souza', html)
        self.assertIn('(11) 91234-5678', html)
        self.assertIn('Av. Brasil', html)

    def test_save_address_endpoint_persists_to_profile(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('accounts:save_address'),
            {
                'city': self.city.id,
                'neighborhood': self.nb.id,
                'address_street': 'Rua Nova',
                'address_number': '42',
                'address_complement': '',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.address_street, 'Rua Nova')
        self.assertEqual(self.user.profile.neighborhood_id, self.nb.id)

    def test_save_address_endpoint_requires_login(self):
        response = self.client.post(reverse('accounts:save_address'), {})
        self.assertEqual(response.status_code, 302)

    def test_save_address_endpoint_returns_errors(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('accounts:save_address'),
            {'city': self.city.id, 'neighborhood': self.other_nb.id},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('neighborhood', response.json()['errors'])

    def test_checkout_shows_save_address_button_only_when_logged_in(self):
        self._add_item_to_cart()
        html = self.client.get(reverse('orders:checkout')).content.decode()
        self.assertNotIn('id="save-address-btn"', html)

        self.client.force_login(self.user)
        html = self.client.get(reverse('orders:checkout')).content.decode()
        self.assertIn('id="save-address-btn"', html)

    def _add_item_to_cart(self):
        """Coloca um item disponível no carrinho para liberar o checkout."""
        from menu.models import Category, MenuItem, Restaurant

        restaurant = Restaurant.objects.create(name='Resto', is_active=True)
        category = Category.objects.create(restaurant=restaurant, name='Cat')
        item = MenuItem.objects.create(
            category=category, name='Lanche', price='20.00', is_available=True
        )
        self.client.post(
            reverse('cart:cart_add', args=[item.id]),
            {'quantity': 1},
        )


class OrderHistoryPaginationTests(TestCase):
    """O histórico traz itens e complementos de cada pedido: precisa de recorte."""

    def setUp(self):
        self.user = User.objects.create_user(username='cliente', password='pw')
        self.client.force_login(self.user)
        self.restaurant = Restaurant.objects.create(name='Histórico', slug='hist')
        self.page_size = ORDER_HISTORY_PAGE_SIZE

    def _make_orders(self, count):
        for index in range(count):
            Order.objects.create(
                restaurant=self.restaurant, user=self.user,
                customer_name=f'Cliente {index}', phone='555-0000',
                subtotal=Decimal('10.00'), total=Decimal('10.00'),
            )

    def test_history_is_paginated(self):
        self._make_orders(self.page_size + 3)
        response = self.client.get(reverse('accounts:order_history'))
        self.assertEqual(len(response.context['orders']), self.page_size)
        self.assertEqual(response.context['page'].paginator.num_pages, 2)

    def test_second_page_returns_the_remainder(self):
        self._make_orders(self.page_size + 3)
        response = self.client.get(reverse('accounts:order_history'), {'page': 2})
        self.assertEqual(len(response.context['orders']), 3)

    def test_pagination_neither_repeats_nor_skips_orders(self):
        total = self.page_size + 3
        self._make_orders(total)
        url = reverse('accounts:order_history')

        first = self.client.get(url).context['orders']
        second = self.client.get(url, {'page': 2}).context['orders']

        numbers = [o.order_number for o in first] + [o.order_number for o in second]
        self.assertEqual(len(set(numbers)), total)

    def test_single_page_hides_the_pager(self):
        self._make_orders(2)
        response = self.client.get(reverse('accounts:order_history'))
        self.assertNotContains(response, 'class="pager"')


@override_settings(RATELIMIT_ENABLED=True, RATELIMIT_TRUST_FORWARDED=False)
class ThrottleUnitTests(TestCase):
    """A camada de limite (janela deslizante) isolada das views."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_record_then_block_at_the_limit(self):
        limit, _ = throttle.RULES[throttle.LOGIN]
        for _ in range(limit - 1):
            throttle.record(throttle.LOGIN, '1.2.3.4')
        self.assertFalse(throttle.is_blocked(throttle.LOGIN, '1.2.3.4'))
        throttle.record(throttle.LOGIN, '1.2.3.4')
        self.assertTrue(throttle.is_blocked(throttle.LOGIN, '1.2.3.4'))

    def test_different_keys_do_not_share_a_counter(self):
        limit, _ = throttle.RULES[throttle.LOGIN]
        for _ in range(limit):
            throttle.record(throttle.LOGIN, '1.1.1.1')
        self.assertTrue(throttle.is_blocked(throttle.LOGIN, '1.1.1.1'))
        self.assertFalse(throttle.is_blocked(throttle.LOGIN, '2.2.2.2'))

    def test_clear_resets_the_counter(self):
        limit, _ = throttle.RULES[throttle.LOGIN]
        for _ in range(limit):
            throttle.record(throttle.LOGIN, '9.9.9.9')
        throttle.clear(throttle.LOGIN, '9.9.9.9')
        self.assertFalse(throttle.is_blocked(throttle.LOGIN, '9.9.9.9'))

    def test_attempts_outside_the_window_do_not_count(self):
        limit, window = throttle.RULES[throttle.LOGIN]
        for _ in range(limit):
            throttle.record(throttle.LOGIN, '8.8.8.8')
        # Envelhece as tentativas para além da janela.
        AccessAttempt.objects.filter(scope=throttle.LOGIN, key='8.8.8.8').update(
            created_at=timezone.now() - timedelta(seconds=window + 60),
        )
        self.assertFalse(throttle.is_blocked(throttle.LOGIN, '8.8.8.8'))

    def test_record_prunes_stale_rows_of_the_same_key(self):
        _, window = throttle.RULES[throttle.LOGIN]
        throttle.record(throttle.LOGIN, '7.7.7.7')
        AccessAttempt.objects.filter(scope=throttle.LOGIN, key='7.7.7.7').update(
            created_at=timezone.now() - timedelta(seconds=window + 60),
        )
        # A próxima gravação limpa as linhas velhas dessa chave.
        throttle.record(throttle.LOGIN, '7.7.7.7')
        self.assertEqual(
            AccessAttempt.objects.filter(scope=throttle.LOGIN, key='7.7.7.7').count(), 1,
        )

    def test_client_ip_uses_remote_addr_by_default(self):
        request = self.factory.post('/', REMOTE_ADDR='203.0.113.9')
        request.META['HTTP_X_FORWARDED_FOR'] = '198.51.100.7'
        self.assertEqual(throttle.client_ip(request), '203.0.113.9')

    @override_settings(RATELIMIT_TRUST_FORWARDED=True)
    def test_client_ip_trusts_forwarded_when_behind_proxy(self):
        request = self.factory.post('/', REMOTE_ADDR='10.0.0.1')
        request.META['HTTP_X_FORWARDED_FOR'] = '198.51.100.7, 10.0.0.1'
        self.assertEqual(throttle.client_ip(request), '198.51.100.7')

    @override_settings(RATELIMIT_ENABLED=False)
    def test_disabled_flag_never_blocks(self):
        limit, _ = throttle.RULES[throttle.LOGIN]
        for _ in range(limit * 3):
            throttle.record(throttle.LOGIN, '4.4.4.4')
        self.assertFalse(throttle.is_blocked(throttle.LOGIN, '4.4.4.4'))
        # Desligado, nem grava.
        self.assertEqual(AccessAttempt.objects.count(), 0)


@override_settings(RATELIMIT_ENABLED=True, RATELIMIT_TRUST_FORWARDED=False)
class LoginThrottleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='@cliente', email='c@example.com', password='Sup3rSecret!9',
        )
        self.url = reverse('accounts:login')

    def _bad_login(self, **extra):
        return self.client.post(
            self.url, {'username': 'c@example.com', 'password': 'errada'}, **extra,
        )

    def _good_login(self, **extra):
        return self.client.post(
            self.url, {'username': 'c@example.com', 'password': 'Sup3rSecret!9'}, **extra,
        )

    def test_blocks_after_max_failures_even_with_correct_password(self):
        limit, _ = throttle.RULES[throttle.LOGIN]
        for _ in range(limit):
            self._bad_login()
        response = self._good_login()
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(response, 'Muitas tentativas')

    def test_successful_login_clears_the_counter(self):
        for _ in range(3):
            self._bad_login()
        self._good_login()
        self.assertIn('_auth_user_id', self.client.session)
        self.assertFalse(AccessAttempt.objects.exists())

    def test_same_account_is_protected_across_ips(self):
        # IPs distintos: o limite por IP nunca acumula, mas o por conta sim.
        limit, _ = throttle.RULES[throttle.LOGIN_USER]
        for i in range(limit):
            self._bad_login(REMOTE_ADDR=f'10.0.0.{i}')
        response = self._good_login(REMOTE_ADDR='10.0.0.250')
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(response, 'Muitas tentativas')

    @override_settings(RATELIMIT_ENABLED=False)
    def test_disabled_flag_allows_login_past_limit(self):
        limit, _ = throttle.RULES[throttle.LOGIN]
        for _ in range(limit + 2):
            self._bad_login()
        self._good_login()
        self.assertIn('_auth_user_id', self.client.session)


@override_settings(RATELIMIT_ENABLED=True, RATELIMIT_TRUST_FORWARDED=False)
class SignupThrottleTests(TestCase):
    def _signup(self, email, cpf):
        return self.client.post(reverse('accounts:signup'), {
            'full_name': 'Cliente Teste',
            'email': email,
            'phone': '(11) 99999-9999',
            'cpf': cpf,
            'password1': 'Sup3rSecret!9',
            'password2': 'Sup3rSecret!9',
        })

    def test_blocks_after_limit(self):
        limit, _ = throttle.RULES[throttle.SIGNUP]
        AccessAttempt.objects.bulk_create(
            AccessAttempt(scope=throttle.SIGNUP, key='127.0.0.1') for _ in range(limit)
        )
        before = User.objects.count()
        response = self._signup('novo@example.com', '111.444.777-35')
        self.assertContains(response, 'Muitas tentativas')
        self.assertEqual(User.objects.count(), before)


@override_settings(RATELIMIT_ENABLED=True, RATELIMIT_TRUST_FORWARDED=False)
class PasswordResetThrottleTests(TestCase):
    def setUp(self):
        User.objects.create_user(
            username='@cliente', email='c@example.com', password='Sup3rSecret!9',
        )
        self.url = reverse('accounts:password_reset')

    def test_blocks_after_limit(self):
        limit, _ = throttle.RULES[throttle.PASSWORD_RESET]
        AccessAttempt.objects.bulk_create(
            AccessAttempt(scope=throttle.PASSWORD_RESET, key='127.0.0.1')
            for _ in range(limit)
        )
        response = self.client.post(self.url, {'email': 'c@example.com'})
        self.assertEqual(response.status_code, 200)  # não redireciona para "done"
        self.assertContains(response, 'Muitas tentativas')
