from django import forms

from .models import Order


class CheckoutForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = [
            'fulfillment_method',
            'customer_name',
            'phone',
            'address',
            'notes',
            'payment_method',
        ]
        widgets = {
            'fulfillment_method': forms.RadioSelect,
            'payment_method': forms.RadioSelect,
            'customer_name': forms.TextInput(attrs={'autocomplete': 'name'}),
            'phone': forms.TextInput(attrs={'autocomplete': 'tel'}),
            'address': forms.Textarea(attrs={'rows': 3, 'autocomplete': 'street-address'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

    def clean(self):
        cleaned_data = super().clean()
        fulfillment_method = cleaned_data.get('fulfillment_method')
        address = cleaned_data.get('address', '').strip()

        if fulfillment_method == Order.FulfillmentMethod.DELIVERY and not address:
            self.add_error('address', 'Enter a delivery address.')

        return cleaned_data


class OrderStatusForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ['status']
