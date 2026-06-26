from django.utils import timezone

from .models import Restaurant

DAY_LABELS_SHORT = ['seg.', 'ter.', 'qua.', 'qui.', 'sex.', 'sáb.', 'dom.']


def get_current_restaurant():
    return Restaurant.objects.filter(is_active=True).order_by('id').first()


def get_open_status(restaurant):
    """Resolve the restaurant's current open/closed state plus a short detail.

    Returns a dict with:
      - ``is_open``: True / False / None (None when today's hours are unknown)
      - ``today_hours``: the BusinessHours row for today (or None)
      - ``detail``: a short pt-BR hint, e.g. "Fecha às 22:00" or "Abre seg. às 09:00"
    """
    now = timezone.localtime()
    today = now.weekday()
    current_time = now.time()

    hours_by_day = {h.day_of_week: h for h in restaurant.business_hours.all()}
    today_hours = hours_by_day.get(today)

    is_open = None
    detail = ''

    if today_hours is None:
        return {'is_open': None, 'today_hours': None, 'detail': ''}

    if (
        not today_hours.is_closed
        and today_hours.open_time
        and today_hours.close_time
    ):
        if today_hours.open_time <= current_time <= today_hours.close_time:
            is_open = True
            detail = f'Fecha às {today_hours.close_time.strftime("%H:%M")}'
        else:
            is_open = False
            if current_time < today_hours.open_time:
                detail = f'Abre hoje às {today_hours.open_time.strftime("%H:%M")}'
    else:
        is_open = False

    if is_open is False and not detail:
        for offset in range(1, 8):
            day = (today + offset) % 7
            nxt = hours_by_day.get(day)
            if nxt and not nxt.is_closed and nxt.open_time:
                label = 'amanhã' if offset == 1 else DAY_LABELS_SHORT[day]
                detail = f'Abre {label} às {nxt.open_time.strftime("%H:%M")}'
                break

    return {'is_open': is_open, 'today_hours': today_hours, 'detail': detail}


def is_restaurant_open(restaurant):
    return get_open_status(restaurant)['is_open']
