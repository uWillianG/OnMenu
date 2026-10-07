"""Views de ``orders``, organizadas por área.

Este pacote substituiu o antigo ``orders/views.py`` (um único arquivo de ~1000
linhas). As views foram separadas por responsabilidade — ``checkout``,
``payments`` (Pix/cartão/webhooks), ``customer`` (confirmação/acompanhamento/
notificações) e ``staff`` (painel/relatórios/impressão) — e reexportadas aqui,
de modo que ``orders.urls`` e os testes continuam importando de ``orders.views``
sem mudança.
"""

from .checkout import checkout, repeat_order
from .customer import (
    stop_whatsapp,
    confirmation,
    notifications_feed,
    notifications_list,
    track_order,
)
from .payments import (
    payment_state,
    payment_change,
    payment_resume,
    card_3ds_callback,
    card_pay,
    card_status,
    pix_recreate,
    pix_status,
    webhook_card,
    webhook_pix,
)
from .staff import (
    staff_order_refund,
    STAFF_FINISHED_PAGE_SIZE,
    staff_order_detail,
    staff_order_list,
    staff_order_print,
    staff_order_summary,
    staff_orders_bulk_update,
    staff_orders_feed,
    staff_orders_print_active,
    staff_reports,
)

__all__ = [
    'payment_resume',
    'checkout',
    'repeat_order',
    'confirmation',
    'track_order',
    'notifications_list',
    'notifications_feed',
    'pix_status',
    'pix_recreate',
    'card_pay',
    'card_status',
    'card_3ds_callback',
    'webhook_pix',
    'webhook_card',
    'staff_order_list',
    'staff_reports',
    'staff_orders_feed',
    'staff_orders_bulk_update',
    'staff_orders_print_active',
    'staff_order_print',
    'staff_order_summary',
    'staff_order_detail',
    'STAFF_FINISHED_PAGE_SIZE',
]
