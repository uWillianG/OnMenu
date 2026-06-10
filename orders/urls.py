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
    path('orders/pix/<str:pix_id>/status/', views.pix_status, name='pix_status'),
    path('orders/pix/<str:order_number>/recreate/', views.pix_recreate, name='pix_recreate'),
    path('webhook/pix/', views.webhook_pix, name='webhook_pix'),
    path('staff/orders/', views.staff_order_list, name='staff_order_list'),
    path(
        'staff/orders/<str:order_number>/',
        views.staff_order_detail,
        name='staff_order_detail',
    ),
]
