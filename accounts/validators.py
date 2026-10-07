import re

from django.core.exceptions import ValidationError


def validate_cpf(value):
    digits = re.sub(r'[^0-9]', '', value or '')
    if len(digits) != 11 or len(set(digits)) == 1:
        raise ValidationError('CPF inválido.', code='invalid_cpf')
    for size in (9, 10):
        total = sum(int(digits[index]) * (size + 1 - index) for index in range(size))
        check = (total * 10 % 11) % 10
        if check != int(digits[size]):
            raise ValidationError('CPF inválido.', code='invalid_cpf')
    return digits


def validate_phone(value):
    digits = re.sub(r'[^0-9]', '', value or '')
    if len(digits) in (12, 13) and digits.startswith('55'):
        digits = digits[2:]
    if (len(digits) not in (10, 11) or len(set(digits)) == 1
            or int(digits[:2]) < 11 or digits[2] in '01'):
        raise ValidationError('Informe um telefone válido com DDD.', code='invalid_phone')
    return (value or '').strip()


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
