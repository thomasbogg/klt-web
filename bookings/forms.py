from django import forms
from django_countries import countries

from availability.utils import date_string_to_date, guests_string_to_dict
from bookings.models import CURRENCY_CHOICES
from libraries.phone_country_codes import join_phone, phone_country_choices


class ReservationForm(forms.Form):
    """Guest details for creating a booking. Dates/guests ride along as hidden fields (not the
    querystring) so the POST handler has a reliable source even if the page was open a while.

    security_deposits_enabled (constructor-only, not a model/POST field) gates the country
    field's required-ness - per Thomas 2026-09-08, Country of Residence exists solely to drive
    compute_deposit_waiver()'s UK/EU check (see that field's own comment below), so while
    BookingSettings.security_deposits_enabled is off the reservation template hides the row
    entirely (reserve.html) and this form must stop demanding it, or a guest who never saw the
    field would fail validation for "not filling in" a field they were never shown."""
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
    # Posted as two fields (phone_country_code, phone) - per Thomas 2026-09-08, mirroring the
    # Owner Suite Contact Details dropdown - and joined back into the single freeform string
    # Guest.phone stores (see clean() below and libraries/phone_country_codes.py for why the
    # schema itself never gained a second column for this).
    phone_country_code = forms.ChoiceField(
        choices=[('', '-')] + phone_country_choices(), required=False,
        # autocomplete="off" - without it, Chrome's address-autofill heuristics (matching on the
        # field's name/id containing "country") silently pre-select the browser profile's saved
        # country as soon as the page loads, even though the blank option is what the server
        # actually rendered as selected - found 2026-09-13 on the multi-reserve page, but the same
        # underlying field, so fixed here for every form that uses it.
        widget=forms.Select(attrs={'class': 'reserve-input', 'autocomplete': 'off'}),
    )
    phone = forms.CharField(
        max_length=50, required=False,
        widget=forms.TextInput(attrs={'class': 'reserve-input'}),
    )
    # Required while security deposits are enabled (2026-08-29, per Thomas) - drives the
    # security-deposit country gating (see env_settings.UK_EU_COUNTRY_CODES /
    # StaffCheckinDetailView), which needs an answer for every new booking rather than an
    # ambiguous "unknown". __init__ below drops that requirement (and reserve.html hides the row)
    # while BookingSettings.security_deposits_enabled is off, since the field has no other use.
    # Guest.country itself stays nullable at the model level for pre-existing guests created
    # before this field existed.
    country = forms.ChoiceField(
        choices=[('', 'Select a country…')] + list(countries),
        widget=forms.Select(attrs={'class': 'reserve-input'}),
    )
    # Required on every reservation (2026-09-13, per Thomas) - the actual moment a guest signs the
    # Booking Contract, per the Terms and Conditions document itself. Timestamped onto
    # Booking.terms_accepted_at at creation time (see properties/views.py::ReserveView.post) rather
    # than just trusted from this field alone, since the form data itself isn't kept.
    terms_accepted = forms.BooleanField(
        required=True,
        error_messages={'required': "You must agree to the Terms and Conditions to book."},
    )
    start = forms.CharField(widget=forms.HiddenInput)
    end = forms.CharField(widget=forms.HiddenInput)
    guests = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, security_deposits_enabled=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not security_deposits_enabled:
            self.fields['country'].required = False
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
        cleaned_data['phone'] = join_phone(cleaned_data.get('phone_country_code'), cleaned_data.get('phone'))
        return cleaned_data


class PropertyGuestSplitForm(forms.Form):
    """How a guest party splits across two properties booked together (see bookings/models.py::
    ReservationGroup and availability/utils.py::find_property_combo_suggestions) - 2026-09-13,
    Stage 2 of multi-property booking, per Thomas: "we would also need the guest to be explicit at
    booking time about how many of their party are going into each of the properties they are
    booking." Fields are named '<category>_<property index>' (adults_0, children_0, infants_0,
    adults_1, ...) since the set of properties is only known at construction time, not fixed like
    ReservationForm's single-property guest count.

    Only pairs for now, matching find_property_combo_suggestions - properties must be a 2-item
    list. clean() is the one place that both re-derives each property's own party size (checked
    against its own specs.max_guests - the same cap Booking.clean() enforces server-side, just
    surfaced here as a friendly form error instead of a 500 at booking-creation time) and confirms
    nobody's silently lost or gained from the original search's total party size."""
    CATEGORIES = ('adults', 'children', 'infants')

    def __init__(self, *args, properties, total_guests, **kwargs):
        super().__init__(*args, **kwargs)
        self.properties = properties
        self.total_guests = total_guests
        for index in range(len(properties)):
            for category in self.CATEGORIES:
                self.fields[f'{category}_{index}'] = forms.IntegerField(
                    min_value=0, required=False, initial=0,
                    widget=forms.NumberInput(attrs={'class': 'reserve-input', 'min': '0'}),
                )

    def clean(self):
        cleaned_data = super().clean()
        splits = [
            {category: cleaned_data.get(f'{category}_{index}') or 0 for category in self.CATEGORIES}
            for index in range(len(self.properties))
        ]
        for category in self.CATEGORIES:
            expected = self.total_guests.get(category, 0)
            actual = sum(split[category] for split in splits)
            if actual != expected:
                raise forms.ValidationError(
                    f"{category.capitalize()} across both apartments must add up to {expected} "
                    f"(currently {actual}) - the same total you searched with."
                )
        for property, split in zip(self.properties, splits):
            total = sum(split.values())
            specs = getattr(property, 'specs', None)
            if total < 1:
                raise forms.ValidationError(f"{property} needs at least one guest.")
            if specs and total > specs.max_guests:
                raise forms.ValidationError(
                    f"{property} allows a maximum of {specs.max_guests} guests (currently {total})."
                )
            # max_adults is a separate, tighter cap than max_guests - see Booking.clean()'s own
            # comment (2026-09-13, found via a real example where two properties' max_guests
            # summed to enough for a party that was actually too many adults for either specific
            # split of the two).
            if specs and split['adults'] > specs.max_adults:
                raise forms.ValidationError(
                    f"{property} allows a maximum of {specs.max_adults} adults (currently {split['adults']})."
                )
        cleaned_data['splits'] = splits
        return cleaned_data


class GuestContactDetailsForm(forms.Form):
    """Self-service edit of the lead guest's own email/phone from the Manage Booking hub's
    Contact Details section - mirrors owners.views.OwnerContactDetailsView (added 2026-09-07) for
    guests, per Thomas 2026-09-08. Same phone_country_code + phone split/join as ReservationForm
    above, so a returning guest edits the same shape of field they filled in when booking."""
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'class': 'reserve-input'}),
    )
    phone_country_code = forms.ChoiceField(
        choices=[('', '-')] + phone_country_choices(), required=False,
        # autocomplete="off" - without it, Chrome's address-autofill heuristics (matching on the
        # field's name/id containing "country") silently pre-select the browser profile's saved
        # country as soon as the page loads, even though the blank option is what the server
        # actually rendered as selected - found 2026-09-13 on the multi-reserve page, but the same
        # underlying field, so fixed here for every form that uses it.
        widget=forms.Select(attrs={'class': 'reserve-input', 'autocomplete': 'off'}),
    )
    phone = forms.CharField(
        max_length=50, required=False,
        widget=forms.TextInput(attrs={'class': 'reserve-input'}),
    )

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data['phone'] = join_phone(cleaned_data.get('phone_country_code'), cleaned_data.get('phone'))
        return cleaned_data


class BookingLookupForm(forms.Form):
    reference = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={'class': 'manage-input', 'placeholder': 'e.g. K7QX-3H9M'}),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'class': 'manage-input'}),
    )
