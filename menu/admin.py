from django.contrib import admin

from .models import (
    BusinessHours,
    Category,
    ComplementChoice,
    ComplementGroup,
    MenuItem,
    Restaurant,
)


class BusinessHoursInline(admin.TabularInline):
    model = BusinessHours
    extra = 0
    fields = ('day_of_week', 'open_time', 'close_time', 'is_closed')


@admin.register(Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'phone',
        'whatsapp_number',
        'delivery_fee',
        'delivery_time_min',
        'delivery_time_max',
        'accepts_delivery',
        'accepts_pickup',
        'is_active',
    )
    list_filter = ('is_active', 'accepts_delivery', 'accepts_pickup')
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ('name', 'phone', 'address')
    inlines = [BusinessHoursInline]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'restaurant', 'display_order', 'is_active')
    list_filter = ('restaurant', 'is_active')
    list_editable = ('display_order', 'is_active')
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ('name', 'restaurant__name')


class ComplementChoiceInline(admin.TabularInline):
    model = ComplementChoice
    extra = 1
    fields = ('name', 'extra_price', 'display_order')


@admin.register(ComplementGroup)
class ComplementGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'restaurant', 'selection_type', 'required', 'display_order')
    list_filter = ('restaurant', 'selection_type', 'required')
    search_fields = ('name', 'restaurant__name')
    inlines = [ComplementChoiceInline]


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'category',
        'price',
        'is_available',
        'is_featured',
        'display_order',
    )
    list_filter = ('category__restaurant', 'category', 'is_available', 'is_featured')
    list_editable = ('price', 'is_available', 'is_featured', 'display_order')
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ('name', 'description', 'category__name')
    filter_horizontal = ('complement_groups',)
