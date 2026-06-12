import re

from django.core.exceptions import ValidationError


class UppercaseValidator:
    """Exige ao menos uma letra maiúscula."""

    def validate(self, password, user=None):
        if not re.search(r'[A-Z]', password):
            raise ValidationError(
                'A senha deve conter ao menos uma letra maiúscula.',
                code='password_no_upper',
            )

    def get_help_text(self):
        return 'ao menos uma letra maiúscula'


class LowercaseValidator:
    """Exige ao menos uma letra minúscula."""

    def validate(self, password, user=None):
        if not re.search(r'[a-z]', password):
            raise ValidationError(
                'A senha deve conter ao menos uma letra minúscula.',
                code='password_no_lower',
            )

    def get_help_text(self):
        return 'ao menos uma letra minúscula'


class NumberValidator:
    """Exige ao menos um caractere numérico."""

    def validate(self, password, user=None):
        if not re.search(r'\d', password):
            raise ValidationError(
                'A senha deve conter ao menos um número.',
                code='password_no_number',
            )

    def get_help_text(self):
        return 'ao menos um número'


class SpecialCharacterValidator:
    """Exige ao menos um caractere especial (não alfanumérico)."""

    def validate(self, password, user=None):
        if not re.search(r'[^A-Za-z0-9]', password):
            raise ValidationError(
                'A senha deve conter ao menos um caractere especial (ex.: !@#$%).',
                code='password_no_special',
            )

    def get_help_text(self):
        return 'ao menos um caractere especial'
