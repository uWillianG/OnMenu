from django import forms

from .models import Category, MenuItem


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
        }

    def __init__(self, *args, restaurant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if restaurant is not None:
            self.fields['category'].queryset = Category.objects.filter(
                restaurant=restaurant,
            ).order_by('display_order', 'name')
        self.fields['category'].empty_label = 'Selecione uma categoria'
