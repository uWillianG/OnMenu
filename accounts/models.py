from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


class Profile(models.Model):
    """Dados adicionais da conta do cliente que não existem no User padrão."""

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='profile',
    )
    phone = models.CharField('Telefone', max_length=40, blank=True)

    # CPF do cliente, coletado no cadastro. Informação sensível: armazenada só
    # em dígitos e somente leitura — exibida no perfil, mas nunca editável.
    cpf = models.CharField('CPF', max_length=14, blank=True)

    # Endereço salvo do cliente, reaproveitado no checkout para não pedir os
    # mesmos dados de novo. Cidade/bairro referenciam as áreas de entrega
    # cadastradas (mesma origem usada para calcular a taxa no checkout).
    address_street = models.CharField('Rua', max_length=200, blank=True)
    address_number = models.CharField('Número', max_length=20, blank=True)
    address_complement = models.CharField('Complemento', max_length=100, blank=True)
    city = models.ForeignKey(
        'orders.City',
        on_delete=models.SET_NULL,
        related_name='+',
        verbose_name='Cidade',
        null=True,
        blank=True,
    )
    neighborhood = models.ForeignKey(
        'orders.Neighborhood',
        on_delete=models.SET_NULL,
        related_name='+',
        verbose_name='Bairro',
        null=True,
        blank=True,
    )

    @property
    def handle(self):
        """Nome de usuário sempre exibido com um único '@' na frente."""
        return '@' + self.user.username.lstrip('@')

    @property
    def cpf_display(self):
        """CPF formatado (000.000.000-00) para exibição; vazio se não houver."""
        digits = ''.join(ch for ch in (self.cpf or '') if ch.isdigit())
        if len(digits) != 11:
            return self.cpf or ''
        return f'{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}'

    def __str__(self):
        return f'Perfil de {self.user.get_full_name() or self.user.username}'


@receiver(post_save, sender=User)
def ensure_profile(sender, instance, created, **kwargs):
    """Garante um Profile para todo usuário criado."""
    if created:
        Profile.objects.get_or_create(user=instance)
