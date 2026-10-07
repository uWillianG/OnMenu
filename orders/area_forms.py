from django import forms
from menu.forms import BRLDecimalField
from .models import City, Neighborhood


class AreaForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            elif not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-input'


class CityForm(AreaForm):
    delivery_fee = BRLDecimalField(label='Taxa da cidade (R$)', min_value=0,
                                 max_value=9999, max_digits=8, decimal_places=2)
    class Meta:
        model = City
        fields = ['name', 'delivery_fee', 'is_active']


class NeighborhoodForm(AreaForm):
    delivery_fee = BRLDecimalField(label='Taxa adicional do bairro (R$)', min_value=0,
                                 max_value=9999, max_digits=8, decimal_places=2)
    class Meta:
        model = Neighborhood
        fields = ['city', 'name', 'delivery_fee', 'is_active']
