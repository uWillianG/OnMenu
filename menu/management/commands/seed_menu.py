from decimal import Decimal

from django.core.management.base import BaseCommand

from menu.models import Category, MenuItem, Restaurant


class Command(BaseCommand):
    help = 'Create sample restaurant, category, and menu item data.'

    def handle(self, *args, **options):
        restaurant, _ = Restaurant.objects.update_or_create(
            slug='onmenu-bistro',
            defaults={
                'name': 'OnMenu Bistro',
                'phone': '+55 11 99999-0000',
                'address': '123 Market Street',
                'delivery_fee': Decimal('7.50'),
                'accepts_delivery': True,
                'accepts_pickup': True,
                'is_active': True,
            },
        )

        categories = [
            ('starters', 'Starters', 10),
            ('mains', 'Mains', 20),
            ('drinks', 'Drinks', 30),
        ]
        category_map = {}
        for slug, name, display_order in categories:
            category, _ = Category.objects.update_or_create(
                restaurant=restaurant,
                slug=slug,
                defaults={
                    'name': name,
                    'display_order': display_order,
                    'is_active': True,
                },
            )
            category_map[slug] = category

        items = [
            (
                'crispy-garlic-bites',
                'Crispy Garlic Bites',
                'starters',
                'Golden bites with garlic butter and herbs.',
                Decimal('18.00'),
                True,
                10,
            ),
            (
                'house-burger',
                'House Burger',
                'mains',
                'Beef patty, cheese, pickles, lettuce, and house sauce.',
                Decimal('34.90'),
                True,
                10,
            ),
            (
                'grilled-chicken-bowl',
                'Grilled Chicken Bowl',
                'mains',
                'Rice, grilled chicken, vegetables, and citrus dressing.',
                Decimal('31.50'),
                True,
                20,
            ),
            (
                'sparkling-limeade',
                'Sparkling Limeade',
                'drinks',
                'Cold limeade with sparkling water.',
                Decimal('9.90'),
                True,
                10,
            ),
            (
                'seasonal-dessert',
                'Seasonal Dessert',
                'starters',
                'Rotating dessert from the kitchen.',
                Decimal('16.00'),
                False,
                20,
            ),
        ]

        for slug, name, category_slug, description, price, is_available, display_order in items:
            MenuItem.objects.update_or_create(
                category=category_map[category_slug],
                slug=slug,
                defaults={
                    'name': name,
                    'description': description,
                    'price': price,
                    'is_available': is_available,
                    'display_order': display_order,
                },
            )

        self.stdout.write(self.style.SUCCESS('Sample OnMenu data created.'))
