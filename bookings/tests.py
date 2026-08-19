from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bookings.models import Booking, BookingSettings, Charge, Payment
from bookings.utils import (
    add_business_days, determine_payment_provider, expire_stale_holds, guest_counts_by_age,
    payment_clearing_expiry, recalculate_costs_for_party,
)
from guests.models import Guest
from properties.models import Price, Property, PropertySpec


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


class PaymentClearingExpiryTests(TestCase):
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

    def test_is_a_thin_wrapper_around_add_business_days(self):
        settings = self.make_settings(payment_clearing_business_days=3)
        now = timezone.now()
        self.assertEqual(payment_clearing_expiry(now, settings), add_business_days(now, 3))

    def test_thursday_start_skips_the_weekend(self):
        settings = self.make_settings(payment_clearing_business_days=3)
        thursday = self.next_weekday(3)
        expiry = payment_clearing_expiry(thursday, settings)
        self.assertEqual(expiry.weekday(), 1)  # Thu + 3 business days (Fri, Mon, Tue) = Tuesday

    def test_never_lands_on_a_weekend(self):
        settings = self.make_settings(payment_clearing_business_days=3)
        for weekday in range(7):
            start = self.next_weekday(weekday)
            expiry = payment_clearing_expiry(start, settings)
            self.assertLess(expiry.weekday(), 5)


class GuestCountsByAgeTests(TestCase):
    def make_settings(self, **overrides):
        settings = BookingSettings.load()
        for key, value in overrides.items():
            setattr(settings, key, value)
        settings.save()
        return settings

    def test_default_thresholds_bucket_correctly(self):
        settings = self.make_settings(adult_min_age=13, child_min_age=2)
        counts = guest_counts_by_age([1, 5, 12, 13, 40], settings)
        self.assertEqual(counts, {'adults': 2, 'children': 2, 'infants': 1})

    def test_age_15_counts_as_adult_not_child_under_default_cutoff(self):
        settings = self.make_settings(adult_min_age=13, child_min_age=2)
        counts = guest_counts_by_age([15], settings)
        self.assertEqual(counts, {'adults': 1, 'children': 0, 'infants': 0})

    def test_custom_cutoffs_are_respected(self):
        settings = self.make_settings(adult_min_age=18, child_min_age=5)
        counts = guest_counts_by_age([15, 4, 20], settings)
        self.assertEqual(counts, {'adults': 1, 'children': 1, 'infants': 1})


class RecalculateCostsForPartyTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property RC', short_title='TESTRC')
        PropertySpec.objects.create(property=self.property, max_guests=6)
        self.guest = Guest.objects.create(first_name='Vitor', last_name='Carvalho', email='vitor-rc@example.com')
        self.start = date.today() + timedelta(days=60)
        self.end = self.start + timedelta(days=7)
        Price.objects.create(
            property=self.property, name='Test', start_date=self.start, end_date=self.end,
            rate=Decimal('100.00'), extra_adult_rate=Decimal('10.00'), extra_child_rate=Decimal('5.00'),
        )
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Awaiting payment', enquiry_source='Website',
            adults=2, children=1, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(
            booking=self.booking, basic_rental=Decimal('735.00'), admin=Decimal('40.43'),
            due_at_booking=Decimal('193.86'), due_at_balance=Decimal('581.57'),
            balance_due_date=self.start - timedelta(days=56), currency='EUR',
            gbp_conversion_rate=Decimal('0.8600'),
        )

    def test_unchanged_party_reports_no_change(self):
        # 2 adults (30, 32) + 1 child (10) matches the original 2 adults + 1 child composition.
        new_guests, new_costs, changed = recalculate_costs_for_party(self.booking, [30, 32, 10])
        self.assertFalse(changed)
        self.assertEqual(new_guests, {'adults': 2, 'children': 1, 'infants': 0})

    def test_reclassifying_child_to_adult_changes_price(self):
        # Same three people, but the "child" is actually 15 - an adult under the default cutoff.
        new_guests, new_costs, changed = recalculate_costs_for_party(self.booking, [30, 32, 15])
        self.assertTrue(changed)
        self.assertEqual(new_guests, {'adults': 3, 'children': 0, 'infants': 0})
        self.assertEqual(new_costs['basic_rental'], Decimal('770.00'))

    def test_unpriceable_stay_returns_all_none(self):
        self.booking.property.prices.all().delete()
        result = recalculate_costs_for_party(self.booking, [30, 32, 10])
        self.assertEqual(result, (None, None, None))


class BookingDetailsViewTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property BD', short_title='TESTBD')
        PropertySpec.objects.create(property=self.property, max_guests=4)
        self.guest = Guest.objects.create(first_name='Vitor', last_name='Carvalho', email='vitor-bd@example.com')
        self.start = date.today() + timedelta(days=60)
        self.end = self.start + timedelta(days=7)
        Price.objects.create(
            property=self.property, name='Test', start_date=self.start, end_date=self.end,
            rate=Decimal('100.00'), extra_adult_rate=Decimal('10.00'), extra_child_rate=Decimal('5.00'),
        )
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Awaiting payment', enquiry_source='Website',
            adults=2, children=1, babies=0, last_updated=timezone.now(),
            hold_expires_at=timezone.now() + timedelta(minutes=20),
        )
        self.charge = Charge.objects.create(
            booking=self.booking, basic_rental=Decimal('735.00'), admin=Decimal('40.43'),
            due_at_booking=Decimal('193.86'), due_at_balance=Decimal('581.57'),
            balance_due_date=self.start - timedelta(days=56), currency='EUR',
            gbp_conversion_rate=Decimal('0.8600'),
        )
        self.payment = Payment.objects.create(booking=self.booking, provider='revolut', status='pending')
        self.url = reverse('bookings:details', kwargs={'reference': self.booking.reference})
        self.pay_url = reverse('bookings:pay', kwargs={'reference': self.booking.reference})

    def _set_session(self):
        session = self.client.session
        session['pending_booking_reference'] = self.booking.reference
        session.save()

    def _post_data(self, first_names, last_names, ages, confirmed=False):
        data = {
            'first_name[]': first_names,
            'last_name[]': last_names,
            'age[]': [str(age) for age in ages],
        }
        if confirmed:
            data['confirmed'] = '1'
        return data

    def test_get_seeds_blank_rows_matching_guest_count(self):
        response = self.client.get(self.url)
        rows = response.context['rows']
        self.assertEqual(len(rows), 3)  # adults(2) + children(1) + babies(0)
        self.assertEqual(rows[0]['first_name'], 'Vitor')
        self.assertEqual(rows[0]['last_name'], 'Carvalho')

    def test_post_without_matching_session_is_rejected(self):
        response = self.client.post(self.url, self._post_data(
            ['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 10],
        ))
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.assertEqual(self.booking.party.count(), 0)

    def test_post_unchanged_pricing_persists_and_redirects_to_pay(self):
        self._set_session()
        response = self.client.post(self.url, self._post_data(
            ['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 10],
        ))
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.assertEqual(self.booking.party.count(), 3)
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.basic_rental, Decimal('735.00'))

    def test_post_price_changing_party_without_confirmed_shows_warning_and_does_not_persist(self):
        self._set_session()
        response = self.client.post(self.url, self._post_data(
            ['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 15],
        ))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['price_changed'])
        self.assertEqual(self.booking.party.count(), 0)
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.basic_rental, Decimal('735.00'))

    def test_post_confirmed_commits_the_recalculated_price(self):
        self._set_session()
        response = self.client.post(self.url, self._post_data(
            ['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 15], confirmed=True,
        ))
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.basic_rental, Decimal('770.00'))
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.adults, 3)
        self.assertEqual(self.booking.children, 0)

    def test_post_against_expired_hold_is_rejected(self):
        self._set_session()
        self.booking.hold_expires_at = timezone.now() - timedelta(minutes=1)
        self.booking.save(update_fields=['hold_expires_at'])
        response = self.client.post(self.url, self._post_data(
            ['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 10],
        ))
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.assertEqual(self.booking.party.count(), 0)

    def test_post_with_payment_in_progress_is_rejected(self):
        self._set_session()
        self.payment.status = 'in_progress'
        self.payment.save(update_fields=['status'])
        response = self.client.post(self.url, self._post_data(
            ['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 10],
        ))
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.assertEqual(self.booking.party.count(), 0)

    def test_post_with_mismatched_array_lengths_is_rejected(self):
        self._set_session()
        response = self.client.post(self.url, {
            'first_name[]': ['Vitor', 'Joana'],
            'last_name[]': ['Carvalho', 'Moura', 'Carvalho'],
            'age[]': ['30', '32', '10'],
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['non_field_error'])
        self.assertEqual(self.booking.party.count(), 0)

    def test_post_exceeding_max_guests_is_rejected(self):
        self._set_session()  # PropertySpec.max_guests=4 here
        response = self.client.post(self.url, self._post_data(
            ['A', 'B', 'C', 'D', 'E'], ['X', 'X', 'X', 'X', 'X'], [30, 30, 10, 10, 5],
        ))
        self.assertEqual(response.status_code, 200)
        self.assertIn('maximum', response.context['non_field_error'])
        self.assertEqual(self.booking.party.count(), 0)

    def test_post_with_zero_adults_is_rejected(self):
        self._set_session()
        response = self.client.post(self.url, self._post_data(['A', 'B'], ['X', 'X'], [10, 8]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('adult', response.context['non_field_error'])
        self.assertEqual(self.booking.party.count(), 0)
