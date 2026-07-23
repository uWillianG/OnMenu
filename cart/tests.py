from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from menu.models import (
    Category,
    ComplementChoice,
    ComplementGroup,
    MenuItem,
    Restaurant,
)


class CartViewsTests(TestCase):
    def setUp(self):
        restaurant = Restaurant.objects.create(name='Test Kitchen', slug='test-kitchen')
        category = Category.objects.create(
            restaurant=restaurant,
            name='Mains',
            slug='mains',
        )
        self.item = MenuItem.objects.create(
            category=category,
            name='Burger',
            slug='burger',
            price=Decimal('20.00'),
            is_available=True,
        )
        self.unavailable_item = MenuItem.objects.create(
            category=category,
            name='Soup',
            slug='soup',
            price=Decimal('12.00'),
            is_available=False,
        )

    def _lines(self):
        return self.client.get(reverse('cart:cart_detail')).context['cart_items']

    def test_add_update_and_remove_cart_item(self):
        self.client.post(
            reverse('cart:cart_add', args=[self.item.pk]),
            {'quantity': 2},
        )
        response = self.client.get(reverse('cart:cart_detail'))
        self.assertContains(response, 'Burger')
        self.assertContains(response, '40,00')
        line_id = response.context['cart_items'][0]['line_id']

        self.client.post(
            reverse('cart:cart_update', args=[line_id]),
            {'quantity': 3},
        )
        response = self.client.get(reverse('cart:cart_detail'))
        self.assertContains(response, '60,00')

        self.client.post(reverse('cart:cart_remove', args=[line_id]))
        response = self.client.get(reverse('cart:cart_detail'))
        self.assertContains(response, 'Seu carrinho')

    def test_unavailable_item_is_not_added(self):
        self.client.post(
            reverse('cart:cart_add', args=[self.unavailable_item.pk]),
            {'quantity': 1},
        )

        response = self.client.get(reverse('cart:cart_detail'))
        self.assertContains(response, 'Seu carrinho')


class CartLineIdentityTests(TestCase):
    """Linhas do carrinho só se juntam com item + complementos/extras iguais."""

    def setUp(self):
        self.restaurant = Restaurant.objects.create(name='K', slug='k')
        category = Category.objects.create(restaurant=self.restaurant, name='M', slug='m')
        self.item = MenuItem.objects.create(
            category=category,
            name='Burger',
            slug='burger',
            price=Decimal('20.00'),
            is_available=True,
        )
        self.group = ComplementGroup.objects.create(
            restaurant=self.restaurant,
            name='Ponto',
            selection_type=ComplementGroup.SelectionType.SINGLE,
            required=True,
        )
        self.item.complement_groups.add(self.group)
        self.mal = ComplementChoice.objects.create(group=self.group, name='Mal passado')
        self.bem = ComplementChoice.objects.create(group=self.group, name='Bem passado')

    def _add(self, choice, quantity=1):
        self.client.post(
            reverse('cart:cart_add', args=[self.item.pk]),
            {'quantity': quantity, f'option_group_{self.group.pk}': choice.pk},
        )

    def _lines(self):
        return self.client.get(reverse('cart:cart_detail')).context['cart_items']

    def test_same_item_same_options_merge(self):
        self._add(self.mal)
        self._add(self.mal)
        lines = self._lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['quantity'], 2)

    def test_same_item_different_options_stay_separate(self):
        self._add(self.mal)
        self._add(self.bem)
        lines = self._lines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(sum(line['quantity'] for line in lines), 2)

    def test_edit_replaces_line_options(self):
        self._add(self.mal)
        line_id = self._lines()[0]['line_id']
        self.client.post(
            reverse('cart:cart_edit', args=[line_id]),
            {'quantity': 1, f'option_group_{self.group.pk}': self.bem.pk},
        )
        lines = self._lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['options'][0].pk, self.bem.pk)

    def test_edit_merges_into_matching_line(self):
        self._add(self.mal)
        self._add(self.bem, quantity=2)
        line_a = next(l for l in self._lines() if l['options'][0].pk == self.mal.pk)
        self.client.post(
            reverse('cart:cart_edit', args=[line_a['line_id']]),
            {'quantity': 1, f'option_group_{self.group.pk}': self.bem.pk},
        )
        lines = self._lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['quantity'], 3)

    def test_different_notes_stay_separate(self):
        self.client.post(
            reverse('cart:cart_add', args=[self.item.pk]),
            {'quantity': 1, f'option_group_{self.group.pk}': self.mal.pk, 'item_notes': 'sem cebola'},
        )
        self.client.post(
            reverse('cart:cart_add', args=[self.item.pk]),
            {'quantity': 1, f'option_group_{self.group.pk}': self.mal.pk, 'item_notes': 'sem picles'},
        )
        self.assertEqual(len(self._lines()), 2)


class CartOptionValidationTests(TestCase):
    """O POST é público: as escolhas precisam ser validadas no servidor."""

    def setUp(self):
        self.restaurant = Restaurant.objects.create(name='K', slug='k')
        category = Category.objects.create(restaurant=self.restaurant, name='M', slug='m')
        self.item = MenuItem.objects.create(
            category=category,
            name='Burger',
            slug='burger',
            price=Decimal('20.00'),
            is_available=True,
        )
        self.ponto = ComplementGroup.objects.create(
            restaurant=self.restaurant,
            name='Ponto',
            selection_type=ComplementGroup.SelectionType.SINGLE,
            required=True,
        )
        self.mal = ComplementChoice.objects.create(group=self.ponto, name='Mal passado')
        self.bem = ComplementChoice.objects.create(group=self.ponto, name='Bem passado')
        self.extras = ComplementGroup.objects.create(
            restaurant=self.restaurant,
            name='Extras',
            selection_type=ComplementGroup.SelectionType.MULTIPLE,
            required=False,
        )
        self.bacon = ComplementChoice.objects.create(
            group=self.extras, name='Bacon', extra_price=Decimal('5.00'),
        )
        self.item.complement_groups.add(self.ponto, self.extras)

        # Grupo de outro item, não vinculado ao Burger.
        self.molho = ComplementGroup.objects.create(restaurant=self.restaurant, name='Molho')
        self.barbecue = ComplementChoice.objects.create(
            group=self.molho, name='Barbecue', extra_price=Decimal('9.00'),
        )

    def _add(self, data):
        return self.client.post(
            reverse('cart:cart_add', args=[self.item.pk]), data, follow=True,
        )

    def _lines(self):
        return self.client.get(reverse('cart:cart_detail')).context['cart_items']

    def test_required_group_blocks_add(self):
        response = self._add({'quantity': 1})
        self.assertContains(response, 'Escolha uma opção')
        self.assertEqual(self._lines(), [])

    def test_optional_group_can_be_left_empty(self):
        self._add({'quantity': 1, f'option_group_{self.ponto.pk}': self.mal.pk})
        lines = self._lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['unit_price'], Decimal('20.00'))

    def test_choice_from_unlinked_group_is_ignored(self):
        self._add({
            'quantity': 1,
            f'option_group_{self.ponto.pk}': self.mal.pk,
            f'option_group_{self.molho.pk}': self.barbecue.pk,
        })
        lines = self._lines()
        self.assertEqual(len(lines), 1)
        # O molho não pertence ao item: não entra na linha nem cobra o extra.
        self.assertEqual(lines[0]['unit_price'], Decimal('20.00'))
        self.assertEqual([c.pk for c in lines[0]['options']], [self.mal.pk])

    def test_choice_from_another_group_is_ignored(self):
        # Manda uma escolha do grupo "Extras" dentro do grupo "Ponto".
        response = self._add({
            'quantity': 1,
            f'option_group_{self.ponto.pk}': self.bacon.pk,
        })
        self.assertContains(response, 'Escolha uma opção')
        self.assertEqual(self._lines(), [])

    def test_single_group_keeps_only_one_choice(self):
        self._add({
            'quantity': 1,
            f'option_group_{self.ponto.pk}': [self.mal.pk, self.bem.pk],
        })
        lines = self._lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual([c.pk for c in lines[0]['options']], [self.mal.pk])

    def test_multiple_group_keeps_every_valid_choice(self):
        maionese = ComplementChoice.objects.create(
            group=self.extras, name='Maionese', extra_price=Decimal('2.00'),
        )
        self._add({
            'quantity': 1,
            f'option_group_{self.ponto.pk}': self.mal.pk,
            f'option_group_{self.extras.pk}': [self.bacon.pk, maionese.pk],
        })
        lines = self._lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['unit_price'], Decimal('27.00'))

    def test_edit_also_validates_options(self):
        self._add({'quantity': 1, f'option_group_{self.ponto.pk}': self.mal.pk})
        line_id = self._lines()[0]['line_id']
        response = self.client.post(
            reverse('cart:cart_edit', args=[line_id]), {'quantity': 1}, follow=True,
        )
        self.assertContains(response, 'Escolha uma opção')
        # A linha original continua intacta.
        lines = self._lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual([c.pk for c in lines[0]['options']], [self.mal.pk])
