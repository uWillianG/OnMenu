from decimal import Decimal
import uuid

from django.db import models
from django.utils import timezone

from menu.models import MenuItem, Restaurant


def generate_order_number():
    date_part = timezone.now().strftime('%Y%m%d')
    random_part = uuid.uuid4().hex[:6].upper()
    return f'OM-{date_part}-{random_part}'


class Order(models.Model):
    class FulfillmentMethod(models.TextChoices):
        DELIVERY = 'delivery', 'Delivery'
        PICKUP = 'pickup', 'Pickup'

    class PaymentMethod(models.TextChoices):
        CASH = 'cash', 'Cash'
        CARD_ON_DELIVERY = 'card_on_delivery', 'Card on delivery'
        PIX = 'pix', 'PIX'

    class Status(models.TextChoices):
        RECEIVED = 'received', 'Received'
        PREPARING = 'preparing', 'Preparing'
        OUT_FOR_DELIVERY = 'out_for_delivery', 'Out for delivery'
        DELIVERED = 'delivered', 'Delivered'
        CANCELLED = 'cancelled', 'Cancelled'

    restaurant = models.ForeignKey(
        Restaurant,
        on_delete=models.PROTECT,
        related_name='orders',
    )
    order_number = models.CharField(
        max_length=24,
        unique=True,
        editable=False,
        blank=True,
    )
    customer_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=40)
    fulfillment_method = models.CharField(
        max_length=20,
        choices=FulfillmentMethod.choices,
        default=FulfillmentMethod.DELIVERY,
    )
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    payment_method = models.CharField(
        max_length=30,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH,
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.RECEIVED,
    )
    subtotal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
    )
    delivery_fee = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('0.00'),
    )
    total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = generate_order_number()
        self.total = (self.subtotal or Decimal('0.00')) + (
            self.delivery_fee or Decimal('0.00')
        )
        super().save(*args, **kwargs)

    def __str__(self):
        return self.order_number


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name='items',
    )
    menu_item = models.ForeignKey(
        MenuItem,
        on_delete=models.SET_NULL,
        related_name='order_items',
        null=True,
        blank=True,
    )
    item_name = models.CharField(max_length=120)
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)
    quantity = models.PositiveIntegerField()
    line_total = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ['id']

    def save(self, *args, **kwargs):
        self.line_total = self.unit_price * Decimal(self.quantity)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.quantity} x {self.item_name}'
