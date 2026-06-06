from django.contrib import admin

from .models import Category, MenuItem, Restaurant


@admin.register(Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'phone',
        'delivery_fee',
        'accepts_delivery',
        'accepts_pickup',
        'is_active',
    )
    list_filter = ('is_active', 'accepts_delivery', 'accepts_pickup')
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ('name', 'phone', 'address')


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'restaurant', 'display_order', 'is_active')
    list_filter = ('restaurant', 'is_active')
    list_editable = ('display_order', 'is_active')
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ('name', 'restaurant__name')


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'category',
        'price',
        'is_available',
        'display_order',
    )
    list_filter = ('category__restaurant', 'category', 'is_available')
    list_editable = ('price', 'is_available', 'display_order')
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ('name', 'description', 'category__name')
