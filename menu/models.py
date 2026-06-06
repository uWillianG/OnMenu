from decimal import Decimal

from django.db import models
from django.utils.text import slugify


class Restaurant(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    address = models.CharField(max_length=255, blank=True)
    whatsapp_number = models.CharField(max_length=30, blank=True)
    delivery_fee = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('0.00'),
    )
    accepts_delivery = models.BooleanField(default=True)
    accepts_pickup = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:140] or 'restaurant'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Category(models.Model):
    restaurant = models.ForeignKey(
        Restaurant,
        on_delete=models.CASCADE,
        related_name='categories',
    )
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=100, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name_plural = 'categories'
        constraints = [
            models.UniqueConstraint(
                fields=['restaurant', 'slug'],
                name='unique_category_slug_per_restaurant',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:100] or 'category'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class MenuItem(models.Model):
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name='items',
    )
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, blank=True)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='menu_items/', blank=True)
    image_url = models.URLField(blank=True)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    is_available = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['category', 'slug'],
                name='unique_item_slug_per_category',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:140] or 'menu-item'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    @property
    def image_src(self):
        if self.image:
            return self.image.url
        return self.image_url


class ItemOptionGroup(models.Model):
    menu_item = models.ForeignKey(
        MenuItem,
        on_delete=models.CASCADE,
        related_name='option_groups',
    )
    name = models.CharField(max_length=120)
    required = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'name']

    def __str__(self):
        return f'{self.menu_item} — {self.name}'


class ItemOptionChoice(models.Model):
    group = models.ForeignKey(
        ItemOptionGroup,
        on_delete=models.CASCADE,
        related_name='choices',
    )
    name = models.CharField(max_length=120)
    extra_price = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('0.00'),
    )
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'name']

    def __str__(self):
        return self.name


class BusinessHours(models.Model):
    DAY_CHOICES = [
        (0, 'Segunda-feira'),
        (1, 'Terça-feira'),
        (2, 'Quarta-feira'),
        (3, 'Quinta-feira'),
        (4, 'Sexta-feira'),
        (5, 'Sábado'),
        (6, 'Domingo'),
    ]

    restaurant = models.ForeignKey(
        Restaurant,
        on_delete=models.CASCADE,
        related_name='business_hours',
    )
    day_of_week = models.IntegerField(choices=DAY_CHOICES)
    open_time = models.TimeField(null=True, blank=True)
    close_time = models.TimeField(null=True, blank=True)
    is_closed = models.BooleanField(default=False)

    class Meta:
        ordering = ['day_of_week']
        verbose_name_plural = 'business hours'
        constraints = [
            models.UniqueConstraint(
                fields=['restaurant', 'day_of_week'],
                name='unique_business_hours_per_day',
            ),
        ]

    def __str__(self):
        return self.get_day_of_week_display()
