from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .models import (
    CardPayment,
    City,
    Neighborhood,
    Notification,
    Order,
    OrderItem,
    OrderItemOption,
    OrderStatusChange,
    PixPayment,
    PaymentAttempt,
    WhatsAppMessage,
)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('message', 'user', 'order', 'is_read', 'created_at')
    list_filter = ('is_read', 'created_at')
    search_fields = ('message', 'user__username', 'order__order_number')
    readonly_fields = ('created_at',)


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


class OrderStatusChangeInline(admin.TabularInline):
    model = OrderStatusChange
    extra = 0
    readonly_fields = ('from_status', 'to_status', 'changed_by', 'created_at')
    can_delete = False


@admin.register(OrderStatusChange)
class OrderStatusChangeAdmin(admin.ModelAdmin):
    list_display = ('order', 'from_status', 'to_status', 'changed_by', 'created_at')
    list_filter = ('to_status', 'created_at')
    search_fields = ('order__order_number', 'changed_by__username')
    readonly_fields = ('order', 'from_status', 'to_status', 'changed_by', 'created_at')


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
    list_editable = ()
    search_fields = ('order_number', 'customer_name', 'phone', 'address')
    readonly_fields = (
        'order_number',
        'subtotal',
        'delivery_fee',
        'total',
        'created_at',
        'updated_at',
    )
    inlines = [OrderItemInline, OrderStatusChangeInline]

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in Order._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'item_name', 'quantity', 'unit_price', 'line_total')
    search_fields = ('order__order_number', 'item_name')

    def get_readonly_fields(self, request, obj=None):
        if obj and (obj.order.requires_online_payment or obj.order.is_paid):
            return tuple(field.name for field in OrderItem._meta.fields)
        return super().get_readonly_fields(request, obj)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        if obj and (obj.order.requires_online_payment or obj.order.is_paid):
            return False
        return super().has_delete_permission(request, obj)

    # Mexer nos itens muda o valor do pedido: o subtotal/total precisam ser
    # refeitos, senão o pedido continua mostrando o valor do checkout.
    def _refresh_totals(self, *order_ids):
        wanted = {order_id for order_id in order_ids if order_id}
        for order in Order.objects.filter(pk__in=wanted):
            order.recalculate_totals()

    def save_model(self, request, obj, form, change):
        if obj.order.requires_online_payment or obj.order.is_paid:
            raise PermissionDenied('Itens de pedidos online ou pagos são preservados como comprovante da compra.')
        previous_order_id = form.initial.get('order') if change else None
        super().save_model(request, obj, form, change)
        self._refresh_totals(obj.order_id, previous_order_id)

    def delete_model(self, request, obj):
        if obj.order.requires_online_payment or obj.order.is_paid:
            raise PermissionDenied('Itens de pedidos online ou pagos não podem ser excluídos.')
        order_id = obj.order_id
        super().delete_model(request, obj)
        self._refresh_totals(order_id)

    def delete_queryset(self, request, queryset):
        if queryset.filter(order__payment_method__in=['pix', 'credit_card']).exists() or queryset.filter(order__payment_status__in=Order.PAID_STATUSES).exists():
            raise PermissionDenied('Itens de pedidos online ou pagos não podem ser excluídos.')
        order_ids = set(queryset.values_list('order_id', flat=True))
        super().delete_queryset(request, queryset)
        self._refresh_totals(*order_ids)


@admin.register(PixPayment)
class PixPaymentAdmin(admin.ModelAdmin):
    list_display = ('external_reference', 'mp_payment_id', 'status', 'amount', 'expires_at', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('external_reference', 'mp_payment_id', 'order__order_number')
    readonly_fields = (
        'status',
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


@admin.register(CardPayment)
class CardPaymentAdmin(admin.ModelAdmin):
    list_display = (
        'external_reference', 'mp_payment_id', 'status',
        'amount', 'installments', 'payment_method_id', 'created_at',
    )
    list_filter = ('status', 'payment_method_id', 'created_at')
    search_fields = ('external_reference', 'mp_payment_id', 'order__order_number')
    readonly_fields = (
        'order',
        'mp_payment_id',
        'external_reference',
        'status',
        'status_detail',
        'amount',
        'installments',
        'payment_method_id',
        'last_four',
        'created_at',
        'updated_at',
    )


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(admin.ModelAdmin):
    list_display = ('order', 'method', 'status', 'amount', 'mp_payment_id', 'created_at')
    list_filter = ('method', 'status')
    search_fields = ('order__order_number', 'mp_payment_id', 'external_reference')
    readonly_fields = tuple(field.name for field in PaymentAttempt._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WhatsAppMessage)
class WhatsAppMessageAdmin(admin.ModelAdmin):
    list_display = ('order', 'status', 'attempts', 'created_at')
    list_filter = ('status',)
    readonly_fields = tuple(field.name for field in WhatsAppMessage._meta.fields)

    def has_add_permission(self, request):
        return False
