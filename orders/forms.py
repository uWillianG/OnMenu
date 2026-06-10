from django import forms

from .models import City, Neighborhood, Order


def _only_digits(value):
    return ''.join(ch for ch in (value or '') if ch.isdigit())


class CheckoutForm(forms.ModelForm):
    city = forms.ModelChoiceField(
        queryset=City.objects.filter(is_active=True),
        required=False,
        empty_label='Selecione a cidade',
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_city'}),
    )
    neighborhood = forms.ModelChoiceField(
        queryset=Neighborhood.objects.filter(is_active=True),
        required=False,
        empty_label='Selecione o bairro',
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_neighborhood'}),
    )

    class Meta:
        model = Order
        fields = [
            'fulfillment_method',
            'customer_name',
            'phone',
            'customer_cpf',
            'address_street',
            'address_number',
            'address_complement',
            'notes',
            'payment_method',
        ]
        widgets = {
            'fulfillment_method': forms.RadioSelect,
            'payment_method': forms.RadioSelect,
            'customer_name': forms.TextInput(attrs={'autocomplete': 'name'}),
            'phone': forms.TextInput(attrs={'autocomplete': 'tel'}),
            'customer_cpf': forms.TextInput(attrs={'class': 'form-input', 'inputmode': 'numeric', 'autocomplete': 'off', 'maxlength': '14', 'placeholder': '000.000.000-00'}),
            'address_street': forms.TextInput(attrs={'class': 'form-input', 'autocomplete': 'address-line1', 'placeholder': 'Rua / Avenida'}),
            'address_number': forms.TextInput(attrs={'class': 'form-input', 'autocomplete': 'address-line2', 'inputmode': 'numeric', 'placeholder': 'Nº'}),
            'address_complement': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Apto, bloco, referência'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

    # Free-text fields required only when the order is for delivery.
    DELIVERY_REQUIRED = {
        'address_street': 'Informe a rua.',
        'address_number': 'Informe o número.',
    }

    def clean(self):
        cleaned_data = super().clean()

        # Pix exige o CPF do pagador (a API do Mercado Pago usa na identificação).
        if cleaned_data.get('payment_method') == Order.PaymentMethod.PIX:
            cpf = _only_digits(cleaned_data.get('customer_cpf'))
            if not cpf:
                self.add_error('customer_cpf', 'Informe o CPF para pagar com Pix.')
            elif len(cpf) != 11:
                self.add_error('customer_cpf', 'CPF inválido.')

        if cleaned_data.get('fulfillment_method') != Order.FulfillmentMethod.DELIVERY:
            return cleaned_data

        for field, message in self.DELIVERY_REQUIRED.items():
            if not (cleaned_data.get(field) or '').strip():
                self.add_error(field, message)

        city = cleaned_data.get('city')
        neighborhood = cleaned_data.get('neighborhood')

        if not city:
            self.add_error('city', 'Selecione a cidade.')
        if not neighborhood:
            self.add_error('neighborhood', 'Selecione o bairro.')
        elif city and neighborhood.city_id != city.id:
            self.add_error('neighborhood', 'Selecione um bairro da cidade escolhida.')

        return cleaned_data


class OrderStatusForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ['status']
