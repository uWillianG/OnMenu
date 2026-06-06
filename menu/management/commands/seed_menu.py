from decimal import Decimal

from django.core.management.base import BaseCommand

from menu.models import Category, ItemOptionChoice, ItemOptionGroup, MenuItem, Restaurant


class Command(BaseCommand):
    help = 'Cria dados de exemplo para uma hamburgueria brasileira.'

    def handle(self, *args, **options):
        Restaurant.objects.exclude(slug='fabrica-hamburgueria').update(is_active=False)

        restaurant, _ = Restaurant.objects.update_or_create(
            slug='fabrica-hamburgueria',
            defaults={
                'name': 'Fábrica Hamburgueria',
                'phone': '(41) 99999-0000',
                'whatsapp_number': '5541999990000',
                'address': 'Rua das Flores, 42 — Portão, Curitiba/PR',
                'delivery_fee': Decimal('6.00'),
                'accepts_delivery': True,
                'accepts_pickup': True,
                'is_active': True,
            },
        )

        categories_data = [
            ('entradas',           'Entradas',          10),
            ('hamburgueres',       'Hambúrgueres',      20),
            ('lanches',            'Lanches',           30),
            ('adicionais',         'Adicionais',        40),
            ('bebidas-sem-alcool', 'Bebidas sem álcool', 50),
            ('bebidas-alcoolicas', 'Bebidas alcoólicas', 60),
        ]
        cat = {}
        for slug, name, order in categories_data:
            obj, _ = Category.objects.update_or_create(
                restaurant=restaurant,
                slug=slug,
                defaults={'name': name, 'display_order': order, 'is_active': True},
            )
            cat[slug] = obj

        items_data = [
            # (slug, name, category, description, price, available, display_order, featured)
            # Entradas
            ('batata-frita',    'Batata Frita Crocante',   'entradas',
             'Porção de batata frita crocante com molho especial da casa.',
             Decimal('22.00'), True, 10, False),
            ('onion-rings',     'Onion Rings',              'entradas',
             'Anéis de cebola empanados e fritos na hora, com molho ranch.',
             Decimal('26.00'), True, 20, False),
            ('isca-frango',     'Isca de Frango',           'entradas',
             'Iscas de frango empanado com temperos especiais. Acompanha molho barbecue.',
             Decimal('28.00'), True, 30, False),
            ('mini-burguer',    'Mini Burguer (3 unid.)',   'entradas',
             'Três miniburguers com queijo cheddar e picles. Perfeito para compartilhar.',
             Decimal('32.00'), True, 40, True),

            # Hambúrgueres
            ('classic-smash',   'Classic Smash',            'hamburgueres',
             'Blend 150g smashado, queijo cheddar cremoso, picles, cebola caramelizada e molho especial.',
             Decimal('38.00'), True, 10, True),
            ('duplo-bacon',     'Duplo Bacon',              'hamburgueres',
             'Dois blends 120g, queijo cheddar duplo, bacon crocante, alface e tomate.',
             Decimal('47.00'), True, 20, True),
            ('veggie-burger',   'Veggie Burger',            'hamburgueres',
             'Hambúrguer de grão-de-bico artesanal, queijo brie, rúcula e geleia de pimenta.',
             Decimal('36.00'), True, 30, False),
            ('frango-crispy',   'Frango Crispy',            'hamburgueres',
             'Filé de frango empanado artesanalmente, maionese de limão, alface e tomate.',
             Decimal('34.00'), True, 40, False),
            ('smash-trufado',   'Smash Trufado',            'hamburgueres',
             'Blend 150g, queijo gruyère, cogumelos salteados, maionese trufada e rúcula.',
             Decimal('52.00'), True, 50, False),

            # Lanches
            ('x-salada',        'X-Salada',                 'lanches',
             'Hambúrguer bovino, queijo prato, alface, tomate e maionese.',
             Decimal('22.00'), True, 10, False),
            ('x-burguer',       'X-Burguer',                'lanches',
             'Hambúrguer bovino, queijo prato e maionese.',
             Decimal('20.00'), True, 20, False),
            ('x-egg',           'X-Egg',                    'lanches',
             'Hambúrguer bovino, queijo prato, ovo frito, alface e tomate.',
             Decimal('26.00'), True, 30, False),
            ('x-bacon',         'X-Bacon',                  'lanches',
             'Hambúrguer bovino, queijo prato, bacon e maionese.',
             Decimal('28.00'), True, 40, False),

            # Adicionais
            ('queijo-extra',    'Queijo Extra',             'adicionais',
             'Uma fatia extra de queijo cheddar cremoso.',
             Decimal('4.00'),  True, 10, False),
            ('bacon-extra',     'Bacon Extra',              'adicionais',
             'Fatias de bacon crocante adicionais.',
             Decimal('6.00'),  True, 20, False),
            ('ovo-frito',       'Ovo Frito',                'adicionais',
             'Ovo frito no ponto.',
             Decimal('4.00'),  True, 30, False),
            ('molho-especial',  'Molho Especial',           'adicionais',
             'Porção extra do nosso molho especial da casa.',
             Decimal('3.00'),  True, 40, False),
            ('porcao-batata',   'Porção Batata (pequena)', 'adicionais',
             'Porção pequena de batata frita para acompanhar.',
             Decimal('12.00'), True, 50, False),

            # Bebidas sem álcool
            ('refri-lata',      'Refrigerante Lata',        'bebidas-sem-alcool',
             'Coca-Cola, Guaraná Antarctica ou Sprite. Gelado.',
             Decimal('7.00'),  True, 10, False),
            ('suco-natural',    'Suco Natural',             'bebidas-sem-alcool',
             'Laranja, limão ou maracujá. Feito na hora.',
             Decimal('12.00'), True, 20, False),
            ('agua-mineral',    'Água Mineral',             'bebidas-sem-alcool',
             'Água mineral sem gás 500ml.',
             Decimal('5.00'),  True, 30, False),
            ('limonada',        'Limonada Suíça',           'bebidas-sem-alcool',
             'Limonada cremosa com leite condensado. Copo 500ml.',
             Decimal('14.00'), True, 40, False),
            ('milk-shake',      'Milk-Shake',               'bebidas-sem-alcool',
             'Chocolate, morango ou baunilha. Copo 400ml.',
             Decimal('18.00'), True, 50, False),

            # Bebidas alcoólicas
            ('cerveja-long',    'Cerveja Long Neck',        'bebidas-alcoolicas',
             'Heineken, Budweiser ou Corona. 330ml gelada.',
             Decimal('12.00'), True, 10, False),
            ('ipa-artesanal',   'IPA Artesanal',            'bebidas-alcoolicas',
             'IPA local com notas cítricas e amargor equilibrado. 350ml.',
             Decimal('18.00'), True, 20, False),
            ('chopp',           'Chopp 500ml',              'bebidas-alcoolicas',
             'Chopp pilsen gelado no copo. Cremoso e refrescante.',
             Decimal('16.00'), True, 30, False),
            ('gin-tonica',      'Gin Tônica',               'bebidas-alcoolicas',
             'Gin com água tônica, limão siciliano e alecrim. Copo 300ml.',
             Decimal('22.00'), True, 40, False),
        ]

        item_map = {}
        for slug, name, cat_slug, desc, price, available, display_order, featured in items_data:
            obj, _ = MenuItem.objects.update_or_create(
                category=cat[cat_slug],
                slug=slug,
                defaults={
                    'name': name,
                    'description': desc,
                    'price': price,
                    'is_available': available,
                    'display_order': display_order,
                    'is_featured': featured,
                },
            )
            item_map[slug] = obj

        # ── Option groups for Classic Smash ──
        burger = item_map['classic-smash']
        group, _ = ItemOptionGroup.objects.update_or_create(
            menu_item=burger,
            name='Ponto do hambúrguer',
            defaults={'required': True, 'display_order': 10},
        )
        for i, (name, extra) in enumerate([
            ('Mal passado', Decimal('0.00')),
            ('Ao ponto', Decimal('0.00')),
            ('Bem passado', Decimal('0.00')),
        ], start=1):
            ItemOptionChoice.objects.update_or_create(
                group=group, name=name,
                defaults={'extra_price': extra, 'display_order': i * 10},
            )

        group2, _ = ItemOptionGroup.objects.update_or_create(
            menu_item=burger,
            name='Pão',
            defaults={'required': False, 'display_order': 20},
        )
        for i, (name, extra) in enumerate([
            ('Brioche (padrão)', Decimal('0.00')),
            ('Integral', Decimal('0.00')),
            ('Sem glúten', Decimal('4.00')),
        ], start=1):
            ItemOptionChoice.objects.update_or_create(
                group=group2, name=name,
                defaults={'extra_price': extra, 'display_order': i * 10},
            )

        self.stdout.write(self.style.SUCCESS(
            f'Dados de exemplo criados para "{restaurant.name}".'
        ))
