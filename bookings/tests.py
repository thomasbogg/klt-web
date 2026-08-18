from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from bookings.models import Booking, Charge
from bookings.utils import determine_payment_provider, expire_stale_holds
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

    def test_gbp_charge_returns_converted_amount_at_frozen_rate(self):
        charge = Charge(currency='GBP', due_at_booking=Decimal('131.88'), gbp_conversion_rate=Decimal('0.8600'))
        amount, currency = charge.due_at_booking_in_charge_currency()
        self.assertEqual(amount, Decimal('113.42'))  # 131.88 * 0.8600, rounded
        self.assertEqual(currency, 'GBP')
