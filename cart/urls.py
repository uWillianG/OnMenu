from django.urls import path

from . import views

app_name = 'cart'

urlpatterns = [
    path('', views.cart_detail, name='cart_detail'),
    path('add/<int:item_id>/', views.cart_add, name='cart_add'),
    path('update/<str:line_id>/', views.cart_update, name='cart_update'),
    path('edit/<str:line_id>/', views.cart_edit, name='cart_edit'),
    path('remove/<str:line_id>/', views.cart_remove, name='cart_remove'),
]
