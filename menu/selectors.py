from .models import Restaurant


def get_current_restaurant():
    return Restaurant.objects.filter(is_active=True).order_by('id').first()
