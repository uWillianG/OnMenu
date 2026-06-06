from django.urls import path

from . import views

app_name = 'menu'

urlpatterns = [
    path('', views.menu_list, name='menu_list'),
    path('items/<int:pk>/<slug:slug>/', views.item_detail, name='item_detail'),
]
