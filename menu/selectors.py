from django.utils import timezone

from .models import Restaurant

DAY_LABELS_SHORT = ['seg.', 'ter.', 'qua.', 'qui.', 'sex.', 'sáb.', 'dom.']


def get_current_restaurant():
    return Restaurant.objects.filter(is_active=True).order_by('id').first()


def _is_shift(hours):
    """A linha representa um turno de verdade (aberto e com os dois horários)."""
    return bool(hours and not hours.is_closed and hours.open_time and hours.close_time)


def _crosses_midnight(hours):
    """Turno que vira o dia, ex.: 18:00 → 02:00 (fecha depois da meia-noite)."""
    return hours.close_time <= hours.open_time


def _next_opening(hours_by_day, today):
    """Próximo dia com turno cadastrado, ex.: 'Abre amanhã às 18:00'."""
    for offset in range(1, 8):
        day = (today + offset) % 7
        nxt = hours_by_day.get(day)
        if _is_shift(nxt):
            label = 'amanhã' if offset == 1 else DAY_LABELS_SHORT[day]
            return f'Abre {label} às {nxt.open_time.strftime("%H:%M")}'
    return ''


def get_open_status(restaurant):
    """Resolve the restaurant's current open/closed state plus a short detail.

    Turnos que viram a madrugada (ex.: 18:00 → 02:00) são tratados como um único
    turno: às 00:30 de terça o restaurante ainda está no turno que abriu na
    segunda, e é o horário de fechamento **da segunda** que vale.

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
    yesterday_hours = hours_by_day.get((today - 1) % 7)

    # Madrugada: o turno de ontem virou o dia e ainda não fechou.
    if (
        _is_shift(yesterday_hours)
        and _crosses_midnight(yesterday_hours)
        and current_time < yesterday_hours.close_time
    ):
        return {
            'is_open': True,
            'today_hours': today_hours,
            'detail': f'Fecha às {yesterday_hours.close_time.strftime("%H:%M")}',
        }

    if today_hours is None:
        return {'is_open': None, 'today_hours': None, 'detail': ''}

    is_open = False
    detail = ''

    if _is_shift(today_hours):
        open_time = today_hours.open_time
        close_time = today_hours.close_time
        if current_time >= open_time and (
            _crosses_midnight(today_hours) or current_time <= close_time
        ):
            is_open = True
            detail = f'Fecha às {close_time.strftime("%H:%M")}'
        elif current_time < open_time:
            detail = f'Abre hoje às {open_time.strftime("%H:%M")}'

    if not is_open and not detail:
        detail = _next_opening(hours_by_day, today)

    return {'is_open': is_open, 'today_hours': today_hours, 'detail': detail}


def is_restaurant_open(restaurant):
    return get_open_status(restaurant)['is_open']
