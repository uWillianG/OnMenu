from django import forms

from .models import Order


class CheckoutForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = [
            'fulfillment_method',
            'customer_name',
            'phone',
            'address_cep',
            'address_street',
            'address_number',
            'address_complement',
            'address_neighborhood',
            'address_city',
            'notes',
            'payment_method',
        ]
        widgets = {
            'fulfillment_method': forms.RadioSelect,
            'payment_method': forms.RadioSelect,
            'customer_name': forms.TextInput(attrs={'autocomplete': 'name'}),
            'phone': forms.TextInput(attrs={'autocomplete': 'tel'}),
            'address_cep': forms.TextInput(attrs={'class': 'form-input', 'autocomplete': 'postal-code', 'inputmode': 'numeric', 'placeholder': '00000-000', 'maxlength': '9'}),
            'address_street': forms.TextInput(attrs={'class': 'form-input', 'autocomplete': 'address-line1', 'placeholder': 'Rua / Avenida'}),
            'address_number': forms.TextInput(attrs={'class': 'form-input', 'autocomplete': 'address-line2', 'inputmode': 'numeric', 'placeholder': 'Nº'}),
            'address_complement': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Apto, bloco, referência'}),
            'address_neighborhood': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Bairro'}),
            'address_city': forms.TextInput(attrs={'class': 'form-input', 'autocomplete': 'address-level2', 'placeholder': 'Cidade'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

    # Fields required only when the order is for delivery.
    DELIVERY_REQUIRED = {
        'address_street': 'Informe a rua.',
        'address_number': 'Informe o número.',
        'address_neighborhood': 'Informe o bairro.',
        'address_city': 'Informe a cidade.',
    }

    def clean(self):
        cleaned_data = super().clean()
        fulfillment_method = cleaned_data.get('fulfillment_method')

        if fulfillment_method == Order.FulfillmentMethod.DELIVERY:
            for field, message in self.DELIVERY_REQUIRED.items():
                if not (cleaned_data.get(field) or '').strip():
                    self.add_error(field, message)

        return cleaned_data


class OrderStatusForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ['status']
