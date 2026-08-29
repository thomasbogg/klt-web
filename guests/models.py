from django.db import models
from django_countries.fields import CountryField

# Create your models here.

class Guest(models.Model):
    """Guest information."""
    first_name = models.CharField(max_length=100, blank=True, null=True)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    preferred_language = models.CharField(max_length=10, default='EN')
    # Country of residence, collected at first booking contact (bookings.forms.ReservationForm) -
    # required there (2026-08-29, per Thomas) for the security-deposit country gating (see
    # env_settings.UK_EU_COUNTRY_CODES / staff/views.py::StaffCheckinDetailView), but nullable here
    # since every Guest row created before this field existed has no value to backfill. Distinct
    # from bookings.models.GuestRegistration.country_of_residence, which is separate SEF/legal-ID
    # data captured per named party member, much later in the flow.
    country = CountryField(blank_label='-', blank=True, null=True)

    class Meta:
        db_table = 'guests'
        verbose_name = 'Guest'
        verbose_name_plural = 'Guests'

    def __str__(self):
        if self.first_name:
            return f"{self.first_name} {self.last_name}"
        return self.last_name