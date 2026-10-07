from django.urls import path

from . import views
from .views.whatsapp import webhook_whatsapp, outbox, retry_message
from .views.areas import delivery_areas, edit_delivery_area

app_name = 'orders'

urlpatterns = [
    path('staff/whatsapp/', outbox, name='whatsapp_outbox'),
    path('staff/whatsapp/<uuid:message_id>/retry/', retry_message, name='whatsapp_retry'),
    path('staff/entrega/', delivery_areas, name='delivery_areas'),
    path('staff/entrega/<str:kind>/<int:pk>/', edit_delivery_area, name='edit_delivery_area'),
    path('orders/<str:order_number>/whatsapp/stop/', views.stop_whatsapp, name='stop_whatsapp'),
    path('webhook/whatsapp/', webhook_whatsapp, name='webhook_whatsapp'),
    path('orders/checkout/', views.checkout, name='checkout'),
    path('orders/<str:order_number>/payment/', views.payment_resume, name='payment_resume'),
    path('orders/<str:order_number>/payment/state/', views.payment_state, name='payment_state'),
    path('orders/<str:order_number>/payment/change/', views.payment_change, name='payment_change'),
    path(
        'orders/confirmation/<str:order_number>/',
        views.confirmation,
        name='confirmation',
    ),
    path('orders/track/<str:order_number>/', views.track_order, name='track_order'),
    path('orders/notifications/', views.notifications_list, name='notifications'),
    path('orders/notifications/feed/', views.notifications_feed, name='notifications_feed'),
    path('orders/<str:order_number>/repeat/', views.repeat_order, name='repeat_order'),
    path('orders/pix/<str:pix_id>/status/', views.pix_status, name='pix_status'),
    path('orders/pix/<str:order_number>/recreate/', views.pix_recreate, name='pix_recreate'),
    path('orders/card/<str:order_number>/pay/', views.card_pay, name='card_pay'),
    path('orders/card/<str:payment_id>/status/', views.card_status, name='card_status'),
    path('pagamento/3ds-callback/', views.card_3ds_callback, name='card_3ds_callback'),
    path('webhook/pix/', views.webhook_pix, name='webhook_pix'),
    path('webhook/cartao/', views.webhook_card, name='webhook_card'),
    path('staff/orders/', views.staff_order_list, name='staff_order_list'),
    path('staff/orders/<str:order_number>/refund/', views.staff_order_refund, name='staff_order_refund'),
    path('staff/relatorios/', views.staff_reports, name='staff_reports'),
    path('staff/orders/feed/', views.staff_orders_feed, name='staff_orders_feed'),
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
