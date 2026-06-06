from django.utils import timezone

from .models import BusinessHours, Restaurant


def get_current_restaurant():
    return Restaurant.objects.filter(is_active=True).order_by('id').first()


def is_restaurant_open(restaurant):
    today = timezone.localtime().weekday()
    try:
        hours = restaurant.business_hours.get(day_of_week=today)
    except BusinessHours.DoesNotExist:
        return None

    if hours.is_closed:
        return False
    if not hours.open_time or not hours.close_time:
        return None

    current_time = timezone.localtime().time()
    return hours.open_time <= current_time <= hours.close_time
