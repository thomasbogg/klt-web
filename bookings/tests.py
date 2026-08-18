from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from bookings.models import Booking
from bookings.utils import determine_payment_provider
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
