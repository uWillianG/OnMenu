from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordResetForm,
    SetPasswordForm,
    UserCreationForm,
)
from django.contrib.auth.models import User
from django.contrib.auth.validators import (
    ASCIIUsernameValidator,
    UnicodeUsernameValidator,
)

PASSWORD_HELP_TEXT = (
    'Mínimo de 8 caracteres, com letra maiúscula, minúscula, '
    'número e caractere especial.'
)


class StyledFormMixin:
    """Aplica as classes do design system (.form-input) aos campos."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get('class', '')
            field.widget.attrs['class'] = (css + ' form-input').strip()


class LoginForm(StyledFormMixin, AuthenticationForm):
    """Login por nome de usuário, com rótulos em pt-BR."""

    error_messages = {
        'invalid_login': 'Usuário ou senha incorretos. Tente novamente.',
        'inactive': 'Esta conta está inativa.',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = 'Usuário'
        self.fields['password'].label = 'Senha'


class SignupForm(StyledFormMixin, UserCreationForm):
    """Cadastro de conta de cliente com e-mail."""

    email = forms.EmailField(
        label='E-mail',
        required=True,
        help_text='Digite um e-mail válido.',
        widget=forms.EmailInput(attrs={'autocomplete': 'email'}),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        username = self.fields['username']
        username.label = 'Usuário'
        username.help_text = 'Até 150 caracteres.'
        # Sem restrição de caracteres: mantém apenas o limite de tamanho.
        username.validators = [
            v for v in username.validators
            if not isinstance(v, (UnicodeUsernameValidator, ASCIIUsernameValidator))
        ]

        self.fields['password1'].label = 'Senha'
        self.fields['password1'].help_text = PASSWORD_HELP_TEXT
        self.fields['password2'].label = 'Confirme a senha'
        self.fields['password2'].help_text = 'Repita a senha para conferência.'

    def _post_clean(self):
        super()._post_clean()
        # O UserCreationForm é um ModelForm: o full_clean() da instância
        # reaplica o validador de caracteres do campo username. Descartamos
        # apenas esse erro de regex (code 'invalid'), preservando o limite de
        # tamanho ('max_length') e a checagem de unicidade.
        field_errors = self._errors.get('username')
        if field_errors:
            kept = [e for e in field_errors.as_data() if e.code != 'invalid']
            if kept:
                self._errors['username'] = self.error_class(kept)
            else:
                del self._errors['username']

    def clean_email(self):
        email = self.cleaned_data['email']
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Já existe uma conta com este e-mail.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return user


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
