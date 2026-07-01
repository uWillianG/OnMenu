from decimal import Decimal

from django import forms

from .models import (
    Category,
    ComplementChoice,
    ComplementGroup,
    MenuItem,
    Restaurant,
)


class RestaurantLogoForm(forms.ModelForm):
    """Upload do logo do estabelecimento (usado pelo staff na tela de info)."""

    class Meta:
        model = Restaurant
        fields = ['logo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['logo'].required = True


class RestaurantInfoForm(forms.ModelForm):
    """Edição dos dados de contato e entrega do estabelecimento (staff)."""

    class Meta:
        model = Restaurant
        fields = [
            'address',
            'phone',
            'whatsapp_number',
            'delivery_time_min',
            'delivery_time_max',
        ]
        labels = {
            'address': 'Endereço',
            'phone': 'Telefone',
            'whatsapp_number': 'WhatsApp',
            'delivery_time_min': 'Tempo mínimo (min)',
            'delivery_time_max': 'Tempo máximo (min)',
        }
        help_texts = {
            'whatsapp_number': 'Inclua o DDD. Vira um link wa.me automaticamente.',
        }
        widgets = {
            'address': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Rua, número, bairro, cidade',
            }),
            'phone': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': '(11) 3333-4444',
                'inputmode': 'tel',
            }),
            'whatsapp_number': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': '(11) 99999-8888',
                'inputmode': 'tel',
            }),
            'delivery_time_min': forms.NumberInput(attrs={
                'class': 'form-input',
                'min': 0,
                'placeholder': '30',
            }),
            'delivery_time_max': forms.NumberInput(attrs={
                'class': 'form-input',
                'min': 0,
                'placeholder': '45',
            }),
        }

    def clean(self):
        cleaned = super().clean()
        low = cleaned.get('delivery_time_min')
        high = cleaned.get('delivery_time_max')
        if low and high and low > high:
            self.add_error(
                'delivery_time_max',
                'O tempo máximo deve ser maior ou igual ao tempo mínimo.',
            )
        return cleaned


class BRLDecimalField(forms.DecimalField):
    """Aceita preços no formato brasileiro (ex.: "1.234,56") além do padrão."""

    def to_python(self, value):
        if isinstance(value, str) and ',' in value:
            # Vírgula = separador decimal; pontos = separadores de milhar.
            value = value.replace('.', '').replace(',', '.')
        return super().to_python(value)


class CategoryForm(forms.ModelForm):
    """Cadastro/edição de uma categoria do cardápio (ex.: Lanches, Bebidas)."""

    class Meta:
        model = Category
        fields = ['name', 'display_order', 'is_active']
        labels = {
            'name': 'Nome da categoria',
            'display_order': 'Ordem de exibição',
            'is_active': 'Categoria ativa',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Ex.: Lanches, Bebidas, Sobremesas',
            }),
            'display_order': forms.NumberInput(attrs={'class': 'form-input', 'min': 0}),
        }


class MenuItemForm(forms.ModelForm):
    """Cadastro/edição de um item do cardápio (lanche, bebida, etc.)."""

    price = BRLDecimalField(
        label='Preço (R$)',
        max_digits=8,
        decimal_places=2,
        min_value=0,
        widget=forms.TextInput(attrs={
            'class': 'form-input money-mask',
            'inputmode': 'decimal',
            'placeholder': '0,00',
            'autocomplete': 'off',
        }),
    )

    class Meta:
        model = MenuItem
        fields = [
            'category',
            'name',
            'description',
            'price',
            'image',
            'image_url',
            'is_available',
            'is_featured',
            'display_order',
            'complement_groups',
        ]
        labels = {
            'category': 'Categoria',
            'name': 'Nome do item',
            'description': 'Descrição',
            'image': 'Foto (upload)',
            'image_url': 'Foto (link)',
            'is_available': 'Disponível para venda',
            'is_featured': 'Destacar na tela inicial',
            'display_order': 'Ordem de exibição',
            'complement_groups': 'Complementos aplicáveis a este item',
        }
        help_texts = {
            'image_url': 'Use um link de imagem caso não vá enviar um arquivo.',
        }
        widgets = {
            'category': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Ex.: X-Salada, Coca-Cola Lata',
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-textarea',
                'rows': 3,
                'placeholder': 'Ingredientes, tamanho, observações…',
            }),
            'image': forms.FileInput(attrs={'class': 'file-input', 'accept': 'image/*'}),
            'image_url': forms.URLInput(attrs={
                'class': 'form-input',
                'placeholder': 'https://…',
            }),
            'display_order': forms.NumberInput(attrs={'class': 'form-input', 'min': 0}),
            'complement_groups': forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, restaurant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if restaurant is not None:
            self.fields['category'].queryset = Category.objects.filter(
                restaurant=restaurant,
            ).order_by('display_order', 'name')
            self.fields['complement_groups'].queryset = ComplementGroup.objects.filter(
                restaurant=restaurant,
            ).order_by('display_order', 'name')
        self.fields['category'].empty_label = 'Selecione uma categoria'
        self.fields['complement_groups'].required = False


class ComplementGroupForm(forms.ModelForm):
    """Cadastro/edição de um grupo de complementos (ex.: Ponto, Pão)."""

    class Meta:
        model = ComplementGroup
        fields = ['name', 'selection_type', 'required', 'display_order']
        labels = {
            'name': 'Nome do complemento',
            'selection_type': 'Tipo de escolha',
            'required': 'Obrigatório',
            'display_order': 'Ordem de exibição',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Ex.: Ponto do hambúrguer, Pão, Adicionais',
            }),
            'selection_type': forms.Select(attrs={'class': 'form-select'}),
            'display_order': forms.NumberInput(attrs={'class': 'form-input', 'min': 0}),
        }


class ComplementChoiceForm(forms.ModelForm):
    """Uma opção dentro de um grupo de complementos."""

    extra_price = BRLDecimalField(
        label='Preço adicional (R$)',
        max_digits=8,
        decimal_places=2,
        min_value=0,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-input money-mask',
            'inputmode': 'decimal',
            'placeholder': '0,00',
            'autocomplete': 'off',
        }),
    )

    class Meta:
        model = ComplementChoice
        fields = ['name', 'extra_price', 'display_order']
        labels = {
            'name': 'Opção',
            'display_order': 'Ordem',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Ex.: Bacon crocante',
            }),
            'display_order': forms.NumberInput(attrs={'class': 'form-input', 'min': 0}),
        }

    def clean_extra_price(self):
        # Campo em branco = sem custo adicional (o modelo é NOT NULL).
        return self.cleaned_data.get('extra_price') or Decimal('0.00')


ComplementChoiceFormSet = forms.inlineformset_factory(
    ComplementGroup,
    ComplementChoice,
    form=ComplementChoiceForm,
    extra=1,
    can_delete=True,
)
