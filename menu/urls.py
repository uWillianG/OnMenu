from django.urls import path

from . import views

app_name = 'menu'

urlpatterns = [
    path('', views.menu_list, name='menu_list'),
    path('informacoes/', views.restaurant_info, name='restaurant_info'),
    path('informacoes/logo/', views.update_logo, name='update_logo'),
    path('staff/horarios/', views.edit_business_hours, name='edit_business_hours'),
    path('staff/cardapio/', views.manage_menu, name='manage_menu'),
    path('staff/cardapio/categoria/nova/', views.category_create, name='category_create'),
    path('staff/cardapio/categoria/<int:pk>/excluir/', views.category_delete, name='category_delete'),
    path('staff/cardapio/item/novo/', views.item_create, name='item_create'),
    path('staff/cardapio/item/<int:pk>/editar/', views.item_edit, name='item_edit'),
    path('staff/cardapio/item/<int:pk>/bloquear/', views.item_toggle_available, name='item_toggle_available'),
    path('staff/cardapio/item/<int:pk>/excluir/', views.item_delete, name='item_delete'),
    path('items/<int:pk>/<slug:slug>/', views.item_detail, name='item_detail'),
]
