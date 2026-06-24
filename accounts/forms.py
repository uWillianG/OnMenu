import re
import unicodedata

from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordResetForm,
    SetPasswordForm,
    UserCreationForm,
)
from django.contrib.auth.models import User

from orders.models import City, Neighborhood

from .models import Profile

PASSWORD_HELP_TEXT = (
    'Mínimo de 8 caracteres, com letra maiúscula, minúscula, '
    'número e caractere especial.'
)


def _only_digits(value):
    return ''.join(ch for ch in (value or '') if ch.isdigit())


def _strip_accents(text):
    nfkd = unicodedata.normalize('NFKD', text)
    return ''.join(ch for ch in nfkd if not unicodedata.combining(ch))


def _generate_username(first_name, last_name):
    """Gera o @handle do cliente: '@' + primeiro + último nome (sem acentos).

    Em caso de duplicata, anexa um número incremental (@joaosilva2).
    """
    base = re.sub(r'[^a-z0-9]', '', _strip_accents(f'{first_name}{last_name}').lower())
    handle = '@' + (base or 'cliente')
    candidate = handle
    suffix = 2
    while User.objects.filter(username=candidate).exists():
        candidate = f'{handle}{suffix}'
        suffix += 1
    return candidate


def _split_full_name(value):
    """Normaliza o nome completo e separa em (primeiro nome, sobrenome)."""
    parts = (value or '').split()
    first = parts[0] if parts else ''
    last = ' '.join(parts[1:]) if len(parts) > 1 else ''
    return first, last


def _cpf_field():
    return forms.CharField(
        label='CPF',
        max_length=14,
        widget=forms.TextInput(attrs={
            'inputmode': 'numeric',
            'autocomplete': 'off',
            'maxlength': '14',
            'placeholder': '000.000.000-00',
            'data-mask': 'cpf',
        }),
    )


def _clean_full_name(value):
    name = ' '.join((value or '').split())
    if len(name.split()) < 2:
        raise forms.ValidationError('Informe o nome e o sobrenome.')
    return name


def _phone_field():
    # max_length=15 acompanha o formato mascarado "(XX) XXXXX-XXXX" e faz o
    # Django emitir maxlength=15 no input (limite mesmo sem JS).
    return forms.CharField(
        label='Telefone',
        max_length=15,
        widget=forms.TextInput(attrs={
            'autocomplete': 'tel',
            'inputmode': 'tel',
            'placeholder': '(00) 00000-0000',
            'data-mask': 'phone',
        }),
    )


def _clean_phone(value):
    digits = ''.join(ch for ch in (value or '') if ch.isdigit())
    if len(digits) < 10:
        raise forms.ValidationError('Informe um telefone válido com DDD.')
    return value.strip()


class StyledFormMixin:
    """Aplica as classes do design system (.form-input) aos campos."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get('class', '')
            field.widget.attrs['class'] = (css + ' form-input').strip()


class LoginForm(StyledFormMixin, AuthenticationForm):
    """Login por e-mail (clientes) ou nome de usuário (equipe), em pt-BR."""

    error_messages = {
        'invalid_login': 'E-mail/usuário ou senha incorretos. Tente novamente.',
        'inactive': 'Esta conta está inativa.',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = 'E-mail ou usuário'
        self.fields['password'].label = 'Senha'


class SignupForm(StyledFormMixin, UserCreationForm):
    """Cadastro de cliente: nome completo + e-mail + CPF.

    O nome de usuário (``username``) é gerado automaticamente a partir do nome
    (``@primeiroúltimo``) e o cliente acessa o sistema pelo e-mail. O CPF é
    obrigatório, gravado no Profile e nunca exibido depois.
    """

    full_name = forms.CharField(
        label='Nome completo',
        max_length=120,
        widget=forms.TextInput(attrs={
            'autocomplete': 'name', 'placeholder': 'Ex: João da Silva',
        }),
    )
    email = forms.EmailField(
        label='E-mail',
        required=True,
        help_text='Digite um e-mail válido.',
        widget=forms.EmailInput(attrs={'autocomplete': 'email'}),
    )
    phone = _phone_field()
    cpf = _cpf_field()

    class Meta(UserCreationForm.Meta):
        model = User
        # 'username' é gerado no save(); só o e-mail vem do formulário para o User.
        fields = ('email',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(['full_name', 'email', 'phone', 'cpf', 'password1', 'password2'])
        self.fields['password1'].label = 'Senha'
        self.fields['password1'].help_text = PASSWORD_HELP_TEXT
        self.fields['password2'].label = 'Confirme a senha'
        self.fields['password2'].help_text = 'Repita a senha para conferência.'

    def clean_full_name(self):
        return _clean_full_name(self.cleaned_data.get('full_name'))

    def clean_email(self):
        email = self.cleaned_data['email']
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Já existe uma conta com este e-mail.')
        return email

    def clean_phone(self):
        return _clean_phone(self.cleaned_data.get('phone'))

    def clean_cpf(self):
        digits = _only_digits(self.cleaned_data.get('cpf'))
        if len(digits) != 11:
            raise forms.ValidationError('CPF inválido.')
        if Profile.objects.filter(cpf=digits).exists():
            raise forms.ValidationError('Já existe uma conta com este CPF.')
        return digits

    def save(self, commit=True):
        user = super().save(commit=False)
        parts = self.cleaned_data['full_name'].split()
        user.first_name = parts[0]
        user.last_name = ' '.join(parts[1:])
        user.email = self.cleaned_data['email']
        # @handle usa o primeiro e o ÚLTIMO nome (ignora nomes do meio).
        user.username = _generate_username(parts[0], parts[-1])
        if commit:
            user.save()
            # O signal post_save já cria o Profile; gravamos telefone e CPF.
            profile, _ = Profile.objects.get_or_create(user=user)
            profile.phone = self.cleaned_data['phone']
            profile.cpf = self.cleaned_data['cpf']
            profile.save()
        return user


class ProfileForm(StyledFormMixin, forms.ModelForm):
    """Edição dos dados da conta do cliente no perfil.

    O nome de usuário (@handle) é gerado no cadastro e não é editável aqui; o
    cliente edita o nome completo, o e-mail e o telefone.
    """

    full_name = forms.CharField(
        label='Nome completo',
        max_length=120,
        widget=forms.TextInput(attrs={
            'autocomplete': 'name', 'placeholder': 'Ex: João da Silva',
        }),
    )
    email = forms.EmailField(
        label='E-mail',
        required=True,
        widget=forms.EmailInput(attrs={'autocomplete': 'email'}),
    )
    phone = _phone_field()

    class Meta:
        model = User
        fields = ('email',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(['full_name', 'email', 'phone'])
        # Pré-preenche nome e telefone a partir da conta/Profile.
        if self.instance and self.instance.pk:
            self.fields['full_name'].initial = self.instance.get_full_name()
            try:
                self.fields['phone'].initial = self.instance.profile.phone
            except Profile.DoesNotExist:
                self.fields['phone'].initial = ''

    def clean_full_name(self):
        return _clean_full_name(self.cleaned_data.get('full_name'))

    def clean_phone(self):
        return _clean_phone(self.cleaned_data.get('phone'))

    def clean_email(self):
        email = self.cleaned_data['email']
        taken = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        if taken.exists():
            raise forms.ValidationError('Já existe uma conta com este e-mail.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        first, last = _split_full_name(self.cleaned_data['full_name'])
        user.first_name = first
        user.last_name = last
        if commit:
            user.save()
            profile, _ = Profile.objects.get_or_create(user=user)
            profile.phone = self.cleaned_data['phone']
            profile.save()
        return user


class AddressForm(forms.ModelForm):
    """Endereço salvo do cliente, editado no perfil e reusado no checkout."""

    city = forms.ModelChoiceField(
        label='Cidade',
        queryset=City.objects.filter(is_active=True),
        required=False,
        empty_label='Selecione a cidade',
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_addr_city'}),
    )
    neighborhood = forms.ModelChoiceField(
        label='Bairro',
        queryset=Neighborhood.objects.filter(is_active=True),
        required=False,
        empty_label='Selecione o bairro',
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_addr_neighborhood'}),
    )

    class Meta:
        model = Profile
        fields = (
            'city',
            'neighborhood',
            'address_street',
            'address_number',
            'address_complement',
        )
        widgets = {
            'address_street': forms.TextInput(attrs={
                'class': 'form-input', 'autocomplete': 'address-line1',
                'placeholder': 'Rua / Avenida',
            }),
            'address_number': forms.TextInput(attrs={
                'class': 'form-input', 'autocomplete': 'address-line2',
                'inputmode': 'numeric', 'placeholder': 'Nº',
            }),
            'address_complement': forms.TextInput(attrs={
                'class': 'form-input', 'placeholder': 'Apto, bloco, referência',
            }),
        }
        labels = {
            'address_street': 'Rua / Avenida',
            'address_number': 'Número',
            'address_complement': 'Complemento',
        }

    def clean(self):
        cleaned_data = super().clean()
        city = cleaned_data.get('city')
        neighborhood = cleaned_data.get('neighborhood')
        if neighborhood and city and neighborhood.city_id != city.id:
            self.add_error('neighborhood', 'Selecione um bairro da cidade escolhida.')
        if neighborhood and not city:
            self.add_error('city', 'Selecione a cidade do bairro.')
        return cleaned_data


class StyledPasswordResetForm(StyledFormMixin, PasswordResetForm):
    """Solicitação de recuperação: informa o e-mail cadastrado."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].label = 'E-mail'
        self.fields['email'].help_text = 'Informe o e-mail usado no cadastro.'


class StyledSetPasswordForm(StyledFormMixin, SetPasswordForm):
    """Define a nova senha a partir do link de recuperação."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['new_password1'].label = 'Nova senha'
        self.fields['new_password1'].help_text = PASSWORD_HELP_TEXT
        self.fields['new_password2'].label = 'Confirme a nova senha'
        self.fields['new_password2'].help_text = 'Repita a senha para conferência.'
