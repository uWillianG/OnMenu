from decimal import Decimal
import uuid

from django.db import models
from django.utils import timezone

from menu.models import MenuItem, Restaurant


def generate_order_number():
    date_part = timezone.now().strftime('%Y%m%d')
    random_part = uuid.uuid4().hex[:6].upper()
    return f'OM-{date_part}-{random_part}'


class City(models.Model):
    """A deliverable city. Its fee is added to the chosen neighborhood's fee."""

    name = models.CharField('Cidade', max_length=100)
    delivery_fee = models.DecimalField(
        'Taxa de entrega',
        max_digits=8,
        decimal_places=2,
        default=Decimal('0.00'),
    )
    is_active = models.BooleanField('Ativa', default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'cidade'
        verbose_name_plural = 'cidades'

    def __str__(self):
        return self.name


class Neighborhood(models.Model):
    """A deliverable neighborhood within a city, with its own delivery fee."""

    city = models.ForeignKey(
        City,
        on_delete=models.CASCADE,
        related_name='neighborhoods',
        verbose_name='cidade',
    )
    name = models.CharField('Bairro', max_length=100)
    delivery_fee = models.DecimalField(
        'Taxa de entrega',
        max_digits=8,
        decimal_places=2,
        default=Decimal('0.00'),
    )
    is_active = models.BooleanField('Ativo', default=True)

    class Meta:
        ordering = ['name']
        unique_together = [('city', 'name')]
        verbose_name = 'bairro'
        verbose_name_plural = 'bairros'

    def __str__(self):
        return f'{self.name} — {self.city.name}'


class Order(models.Model):
    class FulfillmentMethod(models.TextChoices):
        DELIVERY = 'delivery', 'Entrega'
        PICKUP = 'pickup', 'Retirada'

    class PaymentMethod(models.TextChoices):
        CASH = 'cash', 'Dinheiro'
        CARD_ON_DELIVERY = 'card_on_delivery', 'Cartão na entrega'
        PIX = 'pix', 'PIX'

    class Status(models.TextChoices):
        RECEIVED = 'received', 'Received'
        PREPARING = 'preparing', 'Preparing'
        OUT_FOR_DELIVERY = 'out_for_delivery', 'Out for delivery'
        DELIVERED = 'delivered', 'Delivered'
        CANCELLED = 'cancelled', 'Cancelled'

    class PaymentStatus(models.TextChoices):
        PENDING = 'pending', 'Pendente'
        PAID = 'paid', 'Pago'
        CANCELLED = 'cancelled', 'Cancelado'

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
    # Coletados quando o pagamento é Pix (exigidos pela API do Mercado Pago).
    customer_email = models.EmailField('E-mail', blank=True)
    customer_cpf = models.CharField('CPF', max_length=14, blank=True)
    fulfillment_method = models.CharField(
        max_length=20,
        choices=FulfillmentMethod.choices,
        default=FulfillmentMethod.DELIVERY,
    )
    # Composed full address (kept for display, WhatsApp and admin search).
    # Auto-built from the structured fields below on save() for delivery orders.
    address = models.TextField(blank=True)
    address_street = models.CharField('Rua', max_length=200, blank=True)
    address_number = models.CharField('Número', max_length=20, blank=True)
    address_complement = models.CharField('Complemento', max_length=100, blank=True)
    address_neighborhood = models.CharField('Bairro', max_length=100, blank=True)
    address_city = models.CharField('Cidade', max_length=100, blank=True)
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
    payment_status = models.CharField(
        'Status do pagamento',
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
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

    def _compose_address(self):
        line1 = ', '.join(p for p in [self.address_street, self.address_number] if p)
        if self.address_complement:
            line1 = f'{line1} ({self.address_complement})' if line1 else self.address_complement
        line2 = ' - '.join(p for p in [self.address_neighborhood, self.address_city] if p)
        return '\n'.join(line for line in [line1, line2] if line)

    @property
    def has_structured_address(self):
        return any([
            self.address_street, self.address_number, self.address_neighborhood,
            self.address_city, self.address_complement,
        ])

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = generate_order_number()
        # Rebuild the display address from parts (skip legacy orders with no
        # structured fields so their existing address text isn't wiped).
        if self.fulfillment_method == self.FulfillmentMethod.DELIVERY and self.has_structured_address:
            self.address = self._compose_address()
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
    notes = models.CharField(max_length=300, blank=True, default='')

    class Meta:
        ordering = ['id']

    def save(self, *args, **kwargs):
        self.line_total = self.unit_price * Decimal(self.quantity)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.quantity} x {self.item_name}'


class PixPayment(models.Model):
    """A Pix charge created at Mercado Pago for an order.

    Holds the QR Code data shown to the customer and mirrors the payment status
    reported by Mercado Pago (via webhook or polling). One charge per order.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendente'
        APPROVED = 'approved', 'Aprovado'
        CANCELLED = 'cancelled', 'Cancelado'
        EXPIRED = 'expired', 'Expirado'

    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name='pix_payment',
    )
    # Id do pagamento no Mercado Pago (o "pixId" da spec).
    mp_payment_id = models.CharField(max_length=64, blank=True, db_index=True)
    # Referência idempotente nossa (= order_number), ecoada pelo MP.
    external_reference = models.CharField(max_length=64, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    qr_code_text = models.TextField(blank=True)      # copia-e-cola
    qr_code_base64 = models.TextField(blank=True)    # imagem PNG em base64
    txid = models.CharField(max_length=64, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'pagamento Pix'
        verbose_name_plural = 'pagamentos Pix'

    @property
    def is_expired(self):
        return bool(self.expires_at and timezone.now() > self.expires_at)

    def __str__(self):
        return f'Pix {self.external_reference} ({self.get_status_display()})'


class OrderItemOption(models.Model):
    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.CASCADE,
        related_name='options',
    )
    group_name = models.CharField(max_length=120)
    choice_name = models.CharField(max_length=120)
    extra_price = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('0.00'),
    )

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.group_name}: {self.choice_name}'
