from datetime import date, time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bookings.models import (
    AirportTransferPriceBand, Booking, BookingSettings, Charge, ExtrasSettings, Payment, RequestType,
    WelcomePackItem,
)
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

    def test_get_includes_active_request_types_and_welcome_pack_items(self):
        RequestType.objects.create(name='Extra bed', default_price=Decimal('15.00'))
        RequestType.objects.create(name='Discontinued item', default_price=Decimal('5.00'), active=False)
        WelcomePackItem.objects.create(name='Red wine', order=1)
        response = self.client.get(self.url)
        request_type_names = [row['request_type'].name for row in response.context['request_rows']]
        self.assertEqual(request_type_names, ['Extra bed'])
        self.assertEqual(list(response.context['welcome_pack_items']), [WelcomePackItem.objects.get(name='Red wine')])

    def test_post_persists_welcome_pack_and_requested_extras(self):
        extra_bed = RequestType.objects.create(name='Extra bed', default_price=Decimal('15.00'))
        self._set_session()
        data = self._post_data(['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 10])
        data['welcome_pack'] = 'on'
        data['welcome_pack_food'] = 'vegan'
        data['welcome_pack_drinks'] = 'non_alcoholic'
        data['welcome_pack_note'] = 'Nut allergy'
        data[f'request_qty_{extra_bed.id}'] = '1'
        data[f'request_note_{extra_bed.id}'] = 'For the sofa bed'
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)

        self.booking.refresh_from_db()
        self.assertTrue(self.booking.extras.welcome_pack)
        self.assertEqual(self.booking.extras.welcome_pack_food, 'vegan')
        self.assertEqual(self.booking.extras.welcome_pack_drinks, 'non_alcoholic')
        self.assertEqual(self.booking.extras.welcome_pack_note, 'Nut allergy')
        requested = self.booking.requested_extras.get()
        self.assertEqual(requested.request_type, extra_bed)
        self.assertEqual(requested.quantity, 1)
        self.assertEqual(requested.note, 'For the sofa bed')
        self.assertEqual(requested.price_at_request, Decimal('15.00'))

    def test_post_without_welcome_pack_leaves_food_and_drinks_unset(self):
        self._set_session()
        data = self._post_data(['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 10])
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.booking.refresh_from_db()
        self.assertFalse(self.booking.extras.welcome_pack)
        self.assertIsNone(self.booking.extras.welcome_pack_food)
        self.assertIsNone(self.booking.extras.welcome_pack_drinks)

    def test_post_with_zero_quantity_does_not_create_a_requested_extra(self):
        extra_bed = RequestType.objects.create(name='Extra bed', default_price=Decimal('15.00'))
        self._set_session()
        data = self._post_data(['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 10])
        data[f'request_qty_{extra_bed.id}'] = '0'
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.assertEqual(self.booking.requested_extras.count(), 0)

    def test_post_price_change_warning_preserves_typed_extras(self):
        extra_bed = RequestType.objects.create(name='Extra bed', default_price=Decimal('15.00'))
        self._set_session()
        data = self._post_data(['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 15])
        data['welcome_pack'] = 'on'
        data[f'request_qty_{extra_bed.id}'] = '2'
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['price_changed'])
        self.assertTrue(response.context['welcome_pack'])
        row = next(r for r in response.context['request_rows'] if r['request_type'] == extra_bed)
        self.assertEqual(row['quantity'], '2')
        self.assertEqual(self.booking.requested_extras.count(), 0)

    def test_get_includes_transfer_pricing_config_and_existing_rows(self):
        AirportTransferPriceBand.objects.create(max_guests=4, price=Decimal('25.00'))
        response = self.client.get(self.url)
        self.assertEqual(response.context['transfer_rows'], [])
        self.assertEqual(response.context['transfer_pricing_config']['bands'], [{'max_guests': 4, 'price': '25.00'}])

    def test_post_persists_airport_transfer_with_computed_price(self):
        AirportTransferPriceBand.objects.create(max_guests=4, price=Decimal('25.00'))
        self._set_session()
        data = self._post_data(['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 10])
        data['transfer_direction[]'] = ['inbound']
        data['transfer_airport[]'] = ['faro']
        data['transfer_flight_number[]'] = ['TP1234']
        data['transfer_time[]'] = ['14:30']
        data['transfer_adults[]'] = ['2']
        data['transfer_children[]'] = ['1']
        data['transfer_infants[]'] = ['0']
        data['transfer_child_seats[]'] = ['1 booster seat']
        data['transfer_excess_baggage[]'] = ['']
        data['transfer_notes[]'] = ['']
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)

        transfer = self.booking.airport_transfers.get()
        self.assertEqual(transfer.direction, 'inbound')
        self.assertTrue(transfer.is_faro)
        self.assertEqual(transfer.flight_number, 'TP1234')
        self.assertEqual(str(transfer.time), '14:30:00')
        self.assertEqual(transfer.adults, 2)
        self.assertEqual(transfer.children, 1)
        self.assertEqual(transfer.child_seats, '1 booster seat')
        self.assertEqual(transfer.price_at_request, Decimal('25.00'))

    def test_post_with_invalid_transfer_time_is_rejected_and_persists_nothing(self):
        self._set_session()
        data = self._post_data(['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 10])
        data['transfer_direction[]'] = ['inbound']
        data['transfer_airport[]'] = ['faro']
        data['transfer_flight_number[]'] = ['']
        data['transfer_time[]'] = ['']
        data['transfer_adults[]'] = ['1']
        data['transfer_children[]'] = ['0']
        data['transfer_infants[]'] = ['0']
        data['transfer_child_seats[]'] = ['']
        data['transfer_excess_baggage[]'] = ['']
        data['transfer_notes[]'] = ['']
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['transfer_rows'][0]['errors'].get('time'))
        self.assertEqual(self.booking.party.count(), 0)
        self.assertEqual(self.booking.airport_transfers.count(), 0)

    def test_post_transfer_with_zero_guests_is_rejected(self):
        self._set_session()
        data = self._post_data(['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 10])
        data['transfer_direction[]'] = ['inbound']
        data['transfer_airport[]'] = ['faro']
        data['transfer_flight_number[]'] = ['']
        data['transfer_time[]'] = ['09:00']
        data['transfer_adults[]'] = ['0']
        data['transfer_children[]'] = ['0']
        data['transfer_infants[]'] = ['0']
        data['transfer_child_seats[]'] = ['']
        data['transfer_excess_baggage[]'] = ['']
        data['transfer_notes[]'] = ['']
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['transfer_rows'][0]['errors'].get('guests'))
        self.assertEqual(self.booking.airport_transfers.count(), 0)

    def test_post_with_no_transfer_rows_is_not_an_error(self):
        self._set_session()
        data = self._post_data(['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 10])
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.assertEqual(self.booking.airport_transfers.count(), 0)


class WelcomePackItemMatchesTests(TestCase):
    def test_variant_specific_item_only_matches_its_own_variant(self):
        ham = WelcomePackItem(name='Ham', category=WelcomePackItem.Category.FOOD_STANDARD)
        self.assertTrue(ham.matches(food_choice='standard', drinks_choice='alcoholic'))
        self.assertFalse(ham.matches(food_choice='vegan', drinks_choice='alcoholic'))

    def test_common_item_matches_either_variant_on_its_axis(self):
        water = WelcomePackItem(name='Water', category=WelcomePackItem.Category.DRINKS_COMMON)
        self.assertTrue(water.matches(food_choice='standard', drinks_choice='alcoholic'))
        self.assertTrue(water.matches(food_choice='vegan', drinks_choice='non_alcoholic'))


class AirportTransferPriceBandTests(TestCase):
    def setUp(self):
        AirportTransferPriceBand.objects.create(max_guests=4, price=Decimal('25.00'))
        AirportTransferPriceBand.objects.create(max_guests=8, price=Decimal('45.00'))
        AirportTransferPriceBand.objects.create(max_guests=12, price=Decimal('70.00'))

    def test_picks_the_smallest_band_that_fits(self):
        self.assertEqual(AirportTransferPriceBand.for_guest_count(1).max_guests, 4)
        self.assertEqual(AirportTransferPriceBand.for_guest_count(4).max_guests, 4)
        self.assertEqual(AirportTransferPriceBand.for_guest_count(5).max_guests, 8)
        self.assertEqual(AirportTransferPriceBand.for_guest_count(12).max_guests, 12)

    def test_returns_none_when_guest_count_exceeds_every_band(self):
        self.assertIsNone(AirportTransferPriceBand.for_guest_count(13))


class ExtrasSettingsTransferPricingTests(TestCase):
    def setUp(self):
        AirportTransferPriceBand.objects.create(max_guests=4, price=Decimal('25.00'))
        self.settings = ExtrasSettings.load()
        self.settings.airport_transfer_night_surcharge = Decimal('10.00')
        self.settings.airport_transfer_night_window_start = time(22, 0)
        self.settings.airport_transfer_night_window_end = time(6, 0)
        self.settings.save()

    def test_daytime_transfer_has_no_surcharge(self):
        self.assertEqual(self.settings.compute_transfer_price(2, time(14, 0)), Decimal('25.00'))

    def test_transfer_after_night_window_start_has_surcharge(self):
        self.assertEqual(self.settings.compute_transfer_price(2, time(23, 0)), Decimal('35.00'))

    def test_transfer_before_night_window_end_has_surcharge(self):
        self.assertEqual(self.settings.compute_transfer_price(2, time(3, 0)), Decimal('35.00'))

    def test_transfer_at_window_boundaries_has_surcharge(self):
        self.assertEqual(self.settings.compute_transfer_price(2, time(22, 0)), Decimal('35.00'))
        self.assertEqual(self.settings.compute_transfer_price(2, time(6, 0)), Decimal('35.00'))

    def test_transfer_just_outside_window_has_no_surcharge(self):
        self.assertEqual(self.settings.compute_transfer_price(2, time(21, 59)), Decimal('25.00'))
        self.assertEqual(self.settings.compute_transfer_price(2, time(6, 1)), Decimal('25.00'))

    def test_unbanded_guest_count_returns_none(self):
        self.assertIsNone(self.settings.compute_transfer_price(99, time(14, 0)))

    def test_non_wrapping_window_still_works(self):
        self.settings.airport_transfer_night_window_start = time(1, 0)
        self.settings.airport_transfer_night_window_end = time(5, 0)
        self.settings.save()
        self.assertEqual(self.settings.compute_transfer_price(2, time(3, 0)), Decimal('35.00'))
        self.assertEqual(self.settings.compute_transfer_price(2, time(14, 0)), Decimal('25.00'))
