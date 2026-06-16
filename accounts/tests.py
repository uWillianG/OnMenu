from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


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
                'username': 'cliente1',
                'email': 'cliente1@example.com',
                'phone': '(11) 99999-9999',
                'password1': 'Sup3rSecret!9',
                'password2': 'Sup3rSecret!9',
                'next': reverse('orders:checkout'),
            },
        )
        self.assertRedirects(
            response, reverse('orders:checkout'), fetch_redirect_response=False
        )
        self.assertTrue(User.objects.filter(username='cliente1').exists())
        self.assertIn('_auth_user_id', self.client.session)

    def test_signup_rejects_open_redirect(self):
        response = self.client.post(
            reverse('accounts:signup'),
            {
                'username': 'cliente2',
                'email': 'cliente2@example.com',
                'phone': '(11) 99999-9999',
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

    def test_signup_allows_any_username_characters(self):
        response = self.client.post(
            reverse('accounts:signup'),
            {
                'username': 'cliente novo!',
                'email': 'cliente_novo@example.com',
                'phone': '(11) 99999-9999',
                'password1': 'Sup3rSecret!9',
                'password2': 'Sup3rSecret!9',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username='cliente novo!').exists())

    def test_signup_enforces_username_length_limit(self):
        response = self.client.post(
            reverse('accounts:signup'),
            {
                'username': 'x' * 151,
                'email': 'longo@example.com',
                'password1': 'Sup3rSecret!9',
                'password2': 'Sup3rSecret!9',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('username', response.context['form'].errors)

    def test_signup_rejects_weak_password(self):
        response = self.client.post(
            reverse('accounts:signup'),
            {
                'username': 'cliente_fraco',
                'email': 'fraco@example.com',
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
                'username': 'cliente_email',
                'email': 'nao-e-email',
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

    def test_login_without_next_sends_staff_to_panel(self):
        User.objects.create_user(
            username='equipe', password='Sup3rSecret!9', is_staff=True
        )
        response = self.client.post(
            reverse('accounts:login'),
            {'username': 'equipe', 'password': 'Sup3rSecret!9'},
        )
        self.assertRedirects(
            response,
            reverse('orders:staff_order_list'),
            fetch_redirect_response=False,
        )

    def test_login_form_labels_in_portuguese(self):
        html = self.client.get(reverse('accounts:login')).content.decode()
        self.assertIn('Usuário', html)
        self.assertIn('Senha', html)
        self.assertNotIn('>Username<', html)
        self.assertNotIn('>Password<', html)

    def test_signup_validation_errors_in_portuguese(self):
        response = self.client.post(
            reverse('accounts:signup'),
            {
                'username': 'cliente_pt',
                'email': 'cliente_pt@example.com',
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


class PhoneAndProfileTests(TestCase):
    def test_signup_requires_phone(self):
        response = self.client.post(
            reverse('accounts:signup'),
            {
                'username': 'sem_tel',
                'email': 'sem_tel@example.com',
                'password1': 'Sup3rSecret!9',
                'password2': 'Sup3rSecret!9',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('phone', response.context['form'].errors)

    def test_signup_saves_phone_to_profile(self):
        self.client.post(
            reverse('accounts:signup'),
            {
                'username': 'com_tel',
                'email': 'com_tel@example.com',
                'phone': '(11) 98888-7777',
                'password1': 'Sup3rSecret!9',
                'password2': 'Sup3rSecret!9',
            },
        )
        user = User.objects.get(username='com_tel')
        self.assertEqual(user.profile.phone, '(11) 98888-7777')

    def test_profile_shows_phone_in_read_mode(self):
        user = User.objects.create_user(
            username='leitor', password='Sup3rSecret!9', email='leitor@example.com'
        )
        user.profile.phone = '(21) 3333-4444'
        user.profile.save()
        self.client.force_login(user)
        html = self.client.get(reverse('accounts:profile')).content.decode()
        self.assertIn('(21) 3333-4444', html)
        # Nome/Sobrenome não devem mais existir no formulário
        self.assertNotIn('>Nome<', html)
        self.assertNotIn('>Sobrenome<', html)

    def test_profile_updates_phone(self):
        user = User.objects.create_user(
            username='editor', password='Sup3rSecret!9', email='editor@example.com'
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse('accounts:profile'),
            {'username': 'editor', 'email': 'editor@example.com', 'phone': '(31) 2222-1111'},
        )
        self.assertRedirects(response, reverse('accounts:profile'))
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.phone, '(31) 2222-1111')

    def test_profile_rejects_invalid_phone(self):
        user = User.objects.create_user(
            username='invalido', password='Sup3rSecret!9', email='invalido@example.com'
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse('accounts:profile'),
            {'username': 'invalido', 'email': 'invalido@example.com', 'phone': '123'},
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
