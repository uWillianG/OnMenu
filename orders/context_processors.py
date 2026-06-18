"""Injeta o contador de notificações não lidas em todos os templates."""

from .models import Notification


def notifications(request):
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {}
    return {
        'unread_notifications_count': Notification.objects.filter(
            user=user, is_read=False
        ).count(),
    }
