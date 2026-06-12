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

    def __str__(self):
        return f'Perfil de {self.user.username}'


@receiver(post_save, sender=User)
def ensure_profile(sender, instance, created, **kwargs):
    """Garante um Profile para todo usuário criado."""
    if created:
        Profile.objects.get_or_create(user=instance)
