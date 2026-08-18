from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from bookings.models import Booking, BookingSettings, Charge
from bookings.utils import determine_payment_provider, expire_stale_holds, wise_hold_expiry
from guests.models import Guest
from properties.models import Property


class DeterminePaymentProviderTests(TestCase):
    def test_october_31_is_revolut(self):
        self.assertEqual(determine_payment_provider(date(2026, 10, 31)), 'revolut')

    def test_november_1_is_wise(self):
        self.assertEqual(determine_payment_provider(date(2026, 11, 1)), 'wise')

    def test_march_31_is_wise(self):
        self.assertEqual(determine_payment_provider(date(2027, 3, 31)), 'wise')

    def test_april_1_is_revolut(self):
        self.assertEqual(determine_payment_provider(date(2027, 4, 1)), 'revolut')


class BookingOverlappingTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property', short_title='TESTPROP')
        self.guest = Guest.objects.create(last_name='Guest')
        self.start = date.today() + timedelta(days=30)
        self.end = self.start + timedelta(days=7)

    def make_booking(self, enquiry_status, hold_expires_at=None):
        return Booking.objects.create(
            property=self.property,
            guest=self.guest,
            arrival_date=self.start,
            departure_date=self.end,
            is_owner=False,
            enquiry_status=enquiry_status,
            enquiry_source='Website',
            adults=2, children=0, babies=0,
            last_updated=timezone.now(),
            hold_expires_at=hold_expires_at,
        )

    def test_expired_hold_does_not_block(self):
        self.make_booking('Awaiting payment', hold_expires_at=timezone.now() - timedelta(minutes=1))
        self.assertFalse(
            Booking.objects.overlapping(self.property, self.start, self.end).exists()
        )

    def test_null_expiry_provisional_still_blocks(self):
        self.make_booking('Provisional booking', hold_expires_at=None)
        self.assertTrue(
            Booking.objects.overlapping(self.property, self.start, self.end).exists()
        )

    def test_unexpired_hold_still_blocks(self):
        self.make_booking('Awaiting payment', hold_expires_at=timezone.now() + timedelta(minutes=10))
        self.assertTrue(
            Booking.objects.overlapping(self.property, self.start, self.end).exists()
        )

    def test_valid_status_blocks_regardless_of_expiry(self):
        self.make_booking('Booking confirmed', hold_expires_at=timezone.now() - timedelta(days=1))
        self.assertTrue(
            Booking.objects.overlapping(self.property, self.start, self.end).exists()
        )


class ExpireStaleHoldsTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property', short_title='TESTPROP')
        self.guest = Guest.objects.create(last_name='Guest')
        self.start = date.today() + timedelta(days=30)
        self.end = self.start + timedelta(days=7)

    def make_booking(self, enquiry_status, hold_expires_at=None):
        return Booking.objects.create(
            property=self.property,
            guest=self.guest,
            arrival_date=self.start,
            departure_date=self.end,
            is_owner=False,
            enquiry_status=enquiry_status,
            enquiry_source='Website',
            adults=2, children=0, babies=0,
            last_updated=timezone.now(),
            hold_expires_at=hold_expires_at,
        )

    def test_expired_awaiting_payment_flips_to_hold_expired(self):
        booking = self.make_booking('Awaiting payment', hold_expires_at=timezone.now() - timedelta(minutes=1))
        expire_stale_holds()
        booking.refresh_from_db()
        self.assertEqual(booking.enquiry_status, 'Hold expired')

    def test_unexpired_awaiting_payment_is_untouched(self):
        booking = self.make_booking('Awaiting payment', hold_expires_at=timezone.now() + timedelta(minutes=10))
        expire_stale_holds()
        booking.refresh_from_db()
        self.assertEqual(booking.enquiry_status, 'Awaiting payment')

    def test_confirmed_booking_is_untouched_even_with_past_hold_expiry(self):
        booking = self.make_booking('Booking confirmed', hold_expires_at=timezone.now() - timedelta(days=1))
        expire_stale_holds()
        booking.refresh_from_db()
        self.assertEqual(booking.enquiry_status, 'Booking confirmed')


class ChargeDueAtBookingInChargeCurrencyTests(TestCase):
    def test_eur_charge_returns_eur_amount_unconverted(self):
        charge = Charge(currency='EUR', due_at_booking=Decimal('131.88'), gbp_conversion_rate=Decimal('0.8600'))
        amount, currency = charge.due_at_booking_in_charge_currency()
        self.assertEqual(amount, Decimal('131.88'))
        self.assertEqual(currency, 'EUR')


class WiseHoldExpiryTests(TestCase):
    def next_weekday(self, target_weekday):
        """The next datetime (from now) landing on the given date.weekday() value."""
        now = timezone.now()
        offset = (target_weekday - now.weekday()) % 7
        return now + timedelta(days=offset)

    def make_settings(self, **overrides):
        settings = BookingSettings.load()
        for key, value in overrides.items():
            setattr(settings, key, value)
        settings.save()
        return settings

    def test_friday_booking_extends_to_business_days_when_enabled(self):
        settings = self.make_settings(extend_wise_hold_on_weekends=True, wise_weekend_hold_business_days=2, wise_hold_hours=12)
        friday = self.next_weekday(4)
        expiry = wise_hold_expiry(friday, settings)
        self.assertEqual(expiry.weekday(), 1)  # Friday + 2 business days (Mon, Tue) = Tuesday
        self.assertGreater(expiry, friday + timedelta(hours=12))

    def test_saturday_booking_extends_to_business_days_when_enabled(self):
        settings = self.make_settings(extend_wise_hold_on_weekends=True, wise_weekend_hold_business_days=2)
        saturday = self.next_weekday(5)
        expiry = wise_hold_expiry(saturday, settings)
        self.assertEqual(expiry.weekday(), 1)  # Saturday + 2 business days (Mon, Tue) = Tuesday

    def test_weekday_booking_uses_flat_hours_even_when_extension_enabled(self):
        settings = self.make_settings(extend_wise_hold_on_weekends=True, wise_hold_hours=12)
        wednesday = self.next_weekday(2)
        expiry = wise_hold_expiry(wednesday, settings)
        self.assertEqual(expiry, wednesday + timedelta(hours=12))

    def test_friday_booking_uses_flat_hours_when_extension_disabled(self):
        settings = self.make_settings(extend_wise_hold_on_weekends=False, wise_hold_hours=12)
        friday = self.next_weekday(4)
        expiry = wise_hold_expiry(friday, settings)
        self.assertEqual(expiry, friday + timedelta(hours=12))

    def test_gbp_charge_returns_converted_amount_at_frozen_rate(self):
        charge = Charge(currency='GBP', due_at_booking=Decimal('131.88'), gbp_conversion_rate=Decimal('0.8600'))
        amount, currency = charge.due_at_booking_in_charge_currency()
        self.assertEqual(amount, Decimal('113.42'))  # 131.88 * 0.8600, rounded
        self.assertEqual(currency, 'GBP')
