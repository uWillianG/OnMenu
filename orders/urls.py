from django.urls import path

from . import views

app_name = 'orders'

urlpatterns = [
    path('orders/checkout/', views.checkout, name='checkout'),
    path(
        'orders/confirmation/<str:order_number>/',
        views.confirmation,
        name='confirmation',
    ),
    path('orders/track/<str:order_number>/', views.track_order, name='track_order'),
    path('orders/<str:order_number>/repeat/', views.repeat_order, name='repeat_order'),
    path('orders/pix/<str:pix_id>/status/', views.pix_status, name='pix_status'),
    path('orders/pix/<str:order_number>/recreate/', views.pix_recreate, name='pix_recreate'),
    path('orders/card/<str:order_number>/pay/', views.card_pay, name='card_pay'),
    path('orders/card/<str:payment_id>/status/', views.card_status, name='card_status'),
    path('pagamento/3ds-callback/', views.card_3ds_callback, name='card_3ds_callback'),
    path('webhook/pix/', views.webhook_pix, name='webhook_pix'),
    path('webhook/cartao/', views.webhook_card, name='webhook_card'),
    path('staff/orders/', views.staff_order_list, name='staff_order_list'),
    path(
        'staff/orders/bulk-update/',
        views.staff_orders_bulk_update,
        name='staff_orders_bulk_update',
    ),
    path(
        'staff/orders/print/active/',
        views.staff_orders_print_active,
        name='staff_orders_print_active',
    ),
    path(
        'staff/orders/<str:order_number>/print/',
        views.staff_order_print,
        name='staff_order_print',
    ),
    path(
        'staff/orders/<str:order_number>/summary/',
        views.staff_order_summary,
        name='staff_order_summary',
    ),
    path(
        'staff/orders/<str:order_number>/',
        views.staff_order_detail,
        name='staff_order_detail',
    ),
]
