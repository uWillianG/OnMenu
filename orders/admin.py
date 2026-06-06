from django.contrib import admin

from .models import Order, OrderItem, OrderItemOption


class OrderItemOptionInline(admin.TabularInline):
    model = OrderItemOption
    extra = 0
    readonly_fields = ('group_name', 'choice_name', 'extra_price')
    can_delete = False


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = (
        'menu_item',
        'item_name',
        'unit_price',
        'quantity',
        'line_total',
    )
    can_delete = False
    show_change_link = True


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'order_number',
        'customer_name',
        'fulfillment_method',
        'payment_method',
        'status',
        'total',
        'created_at',
    )
    list_filter = (
        'restaurant',
        'fulfillment_method',
        'payment_method',
        'status',
        'created_at',
    )
    list_editable = ('status',)
    search_fields = ('order_number', 'customer_name', 'phone', 'address')
    readonly_fields = (
        'order_number',
        'subtotal',
        'delivery_fee',
        'total',
        'created_at',
        'updated_at',
    )
    inlines = [OrderItemInline]


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'item_name', 'quantity', 'unit_price', 'line_total')
    search_fields = ('order__order_number', 'item_name')
