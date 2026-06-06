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
    path('staff/orders/', views.staff_order_list, name='staff_order_list'),
    path(
        'staff/orders/<str:order_number>/',
        views.staff_order_detail,
        name='staff_order_detail',
    ),
]
