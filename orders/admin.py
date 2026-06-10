from django.contrib import admin

from .models import City, Neighborhood, Order, OrderItem, OrderItemOption, PixPayment


class NeighborhoodInline(admin.TabularInline):
    model = Neighborhood
    extra = 1
    fields = ('name', 'delivery_fee', 'is_active')


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ('name', 'delivery_fee', 'is_active')
    list_editable = ('delivery_fee', 'is_active')
    search_fields = ('name',)
    inlines = [NeighborhoodInline]


@admin.register(Neighborhood)
class NeighborhoodAdmin(admin.ModelAdmin):
    list_display = ('name', 'city', 'delivery_fee', 'is_active')
    list_editable = ('delivery_fee', 'is_active')
    list_filter = ('city', 'is_active')
    search_fields = ('name', 'city__name')


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
        'payment_status',
        'status',
        'total',
        'created_at',
    )
    list_filter = (
        'restaurant',
        'fulfillment_method',
        'payment_method',
        'payment_status',
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


@admin.register(PixPayment)
class PixPaymentAdmin(admin.ModelAdmin):
    list_display = ('external_reference', 'mp_payment_id', 'status', 'amount', 'expires_at', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('external_reference', 'mp_payment_id', 'order__order_number')
    readonly_fields = (
        'order',
        'mp_payment_id',
        'external_reference',
        'amount',
        'qr_code_text',
        'qr_code_base64',
        'txid',
        'expires_at',
        'created_at',
        'updated_at',
    )
