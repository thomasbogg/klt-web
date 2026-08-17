from django import forms

from availability.utils import date_string_to_date, guests_string_to_dict
from bookings.models import CURRENCY_CHOICES


class ReservationForm(forms.Form):
    """Guest details for creating a booking. Dates/guests ride along as hidden fields (not the
    querystring) so the POST handler has a reliable source even if the page was open a while."""
    first_name = forms.CharField(
        max_length=100, required=False,
        widget=forms.TextInput(attrs={'class': 'reserve-input'}),
    )
    last_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'reserve-input'}),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'class': 'reserve-input'}),
    )
    phone = forms.CharField(
        max_length=50, required=False,
        widget=forms.TextInput(attrs={'class': 'reserve-input'}),
    )
    start = forms.CharField(widget=forms.HiddenInput)
    end = forms.CharField(widget=forms.HiddenInput)
    guests = forms.CharField(widget=forms.HiddenInput)
    currency = forms.ChoiceField(
        choices=CURRENCY_CHOICES,
        widget=forms.HiddenInput, initial='EUR', required=False,
    )

    def clean_start(self):
        try:
            return date_string_to_date(self.cleaned_data['start'])
        except (ValueError, TypeError):
            raise forms.ValidationError("Invalid check-in date.")

    def clean_end(self):
        try:
            return date_string_to_date(self.cleaned_data['end'])
        except (ValueError, TypeError):
            raise forms.ValidationError("Invalid check-out date.")

    def clean_guests(self):
        try:
            return guests_string_to_dict(self.cleaned_data['guests'])
        except (ValueError, TypeError):
            raise forms.ValidationError("Invalid guest counts.")

    def clean_currency(self):
        return self.cleaned_data.get('currency') or 'EUR'

    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get('start')
        end = cleaned_data.get('end')
        if start and end and end <= start:
            raise forms.ValidationError("Check-out must be after check-in.")
        return cleaned_data


class BookingLookupForm(forms.Form):
    reference = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={'class': 'manage-input', 'placeholder': 'e.g. K7QX-3H9M'}),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'class': 'manage-input'}),
    )
