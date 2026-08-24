from datetime import date, time, timedelta
from decimal import Decimal

from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from bookings.models import (
    AirportTransfer, AirportTransferDirection, Arrival, BalancePayment,
    Booking, BookingDateAdjustment, BookingGuest, BookingRequestedExtra, BookingSettings, Charge,
    Departure, Extra, ExtrasSettings, FAQ, GuestListAdjustment, Payment, RequestType, WelcomePackItem,
)
from bookings.utils import (
    add_business_days, create_booking, determine_payment_provider, expire_stale_holds, extras_summary,
    guest_counts_by_age, has_completed_previous_stay, payment_clearing_expiry, recalculate_balance_for_party,
    recalculate_costs_for_party, sync_ical_link,
)
from bookings.templatetags.bookings_extras import linkify
from guests.models import Guest
from properties.models import Amenity, Location, LocationRules, Price, Property, PropertySpec, iCalLink


def _normalized_text(response):
    """Collapses whitespace in a rendered response body - a phrase split across lines by a
    template's own indentation (e.g. an {% if %}/{% else %} branch) still reads as one sentence
    in a browser, but assertContains' literal substring match doesn't tolerate the embedded
    newline, so tests that assert on multi-line prose should check against this instead."""
    return ' '.join(response.content.decode().split())


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


class BookingTotalGuestsTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property TG', short_title='TESTTG')
        self.guest = Guest.objects.create(first_name='Mia', last_name='Ferreira', email='mia-tg@example.com')
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest,
            arrival_date=date.today() + timedelta(days=100), departure_date=date.today() + timedelta(days=107),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=1, babies=1, last_updated=timezone.now(),
        )

    def test_falls_back_to_adults_children_babies_before_party_list_exists(self):
        self.assertEqual(self.booking.total_guests(), 4)

    def test_uses_party_count_once_the_guest_list_is_filled_in(self):
        BookingGuest.objects.create(booking=self.booking, first_name='Mia', last_name='Ferreira', age=34)
        BookingGuest.objects.create(booking=self.booking, first_name='Leo', last_name='Ferreira', age=29)
        # Only 2 named so far, even though adults+children+babies says 4 - the real party list
        # takes over as the source of truth the moment it exists at all.
        self.assertEqual(self.booking.total_guests(), 2)


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


class ComputeCostsTests(TestCase):
    def make_settings(self, **overrides):
        settings = BookingSettings.load()
        settings.admin_fee_percent = Decimal('5.50')
        settings.deposit_percent_at_booking = Decimal('25.00')
        settings.balance_due_days_before_arrival = 56
        for key, value in overrides.items():
            setattr(settings, key, value)
        settings.save()
        return settings

    def test_normal_split_well_outside_the_window(self):
        settings = self.make_settings()
        today = date(2026, 1, 1)
        arrival = today + timedelta(days=200)
        costs = settings.compute_costs(Decimal('1000.00'), arrival_date=arrival, today=today)
        self.assertEqual(costs['admin_fee'], Decimal('55.00'))
        self.assertEqual(costs['subtotal'], Decimal('1055.00'))
        self.assertEqual(costs['due_at_booking'], Decimal('263.75'))
        self.assertEqual(costs['due_at_balance'], Decimal('791.25'))
        self.assertEqual(costs['balance_due_date'], arrival - timedelta(days=56))

    def test_collapses_when_arrival_is_already_inside_the_window(self):
        settings = self.make_settings()
        today = date(2026, 1, 1)
        arrival = today + timedelta(days=20)  # well inside the 56-day window
        costs = settings.compute_costs(Decimal('1000.00'), arrival_date=arrival, today=today)
        self.assertEqual(costs['due_at_booking'], costs['subtotal'])
        self.assertEqual(costs['due_at_balance'], Decimal('0'))
        self.assertIsNone(costs['balance_due_date'])

    def test_collapses_exactly_on_the_boundary(self):
        settings = self.make_settings()
        today = date(2026, 1, 1)
        arrival = today + timedelta(days=56)  # balance_due_date lands exactly on today
        costs = settings.compute_costs(Decimal('1000.00'), arrival_date=arrival, today=today)
        self.assertEqual(costs['due_at_booking'], costs['subtotal'])
        self.assertEqual(costs['due_at_balance'], Decimal('0'))
        self.assertIsNone(costs['balance_due_date'])

    def test_does_not_collapse_one_day_outside_the_boundary(self):
        settings = self.make_settings()
        today = date(2026, 1, 1)
        arrival = today + timedelta(days=57)
        costs = settings.compute_costs(Decimal('1000.00'), arrival_date=arrival, today=today)
        self.assertGreater(costs['due_at_balance'], Decimal('0'))
        self.assertIsNotNone(costs['balance_due_date'])

    def test_no_arrival_date_never_collapses(self):
        settings = self.make_settings()
        costs = settings.compute_costs(Decimal('1000.00'), today=date(2026, 1, 1))
        self.assertIsNone(costs['balance_due_date'])
        self.assertGreater(costs['due_at_balance'], Decimal('0'))

    def test_default_today_is_used_when_omitted(self):
        settings = self.make_settings()
        arrival = date.today() + timedelta(days=5)
        costs = settings.compute_costs(Decimal('1000.00'), arrival_date=arrival)
        self.assertEqual(costs['due_at_balance'], Decimal('0'))

    def test_gbp_display_keys_present_after_collapse(self):
        settings = self.make_settings()
        today = date(2026, 1, 1)
        arrival = today + timedelta(days=20)
        costs = settings.compute_costs(Decimal('1000.00'), arrival_date=arrival, today=today)
        costs_gbp = settings.costs_in_gbp(costs)
        self.assertEqual(costs_gbp['due_at_booking'], settings.to_gbp(costs['due_at_booking']))
        self.assertEqual(costs_gbp['due_at_balance'], Decimal('0.00'))


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
            property=self.property, start_date=self.start, end_date=self.end,
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


class RecalculateBalanceForPartyTests(TestCase):
    """Same fixture as RecalculateCostsForPartyTests, but exercising the balance-stage variant -
    the deposit (due_at_booking) is already paid here, so only due_at_balance should ever move."""

    def setUp(self):
        self.property = Property.objects.create(title='Test Property RB', short_title='TESTRB')
        PropertySpec.objects.create(property=self.property, max_guests=6)
        self.guest = Guest.objects.create(first_name='Rita', last_name='Fonseca', email='rita-rb@example.com')
        self.start = date.today() + timedelta(days=200)
        self.end = self.start + timedelta(days=7)
        Price.objects.create(
            property=self.property, start_date=self.start, end_date=self.end,
            rate=Decimal('100.00'), extra_adult_rate=Decimal('10.00'), extra_child_rate=Decimal('5.00'),
        )
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=1, babies=0, last_updated=timezone.now(),
        )
        self.charge = Charge.objects.create(
            booking=self.booking, basic_rental=Decimal('735.00'), admin=Decimal('40.43'),
            due_at_booking=Decimal('193.86'), due_at_balance=Decimal('581.57'),
            balance_due_date=self.start - timedelta(days=56), currency='EUR',
            gbp_conversion_rate=Decimal('0.8600'),
        )

    def test_unchanged_party_reports_no_change(self):
        new_guests, new_costs, changed = recalculate_balance_for_party(self.booking, [30, 32, 10])
        self.assertFalse(changed)
        self.assertEqual(new_costs['due_at_balance'], Decimal('581.57'))
        self.assertEqual(new_costs['due_at_booking'], Decimal('193.86'))

    def test_due_at_booking_is_never_recomputed_only_frozen_value_is_reused(self):
        # Same age change RecalculateCostsForPartyTests uses to trigger a genuine price change
        # (child reclassified as an adult), but here due_at_booking must stay exactly what was
        # actually paid, not 25% of the new (larger) subtotal.
        new_guests, new_costs, changed = recalculate_balance_for_party(self.booking, [30, 32, 15])
        self.assertTrue(changed)
        self.assertEqual(new_costs['basic_rental'], Decimal('770.00'))
        self.assertEqual(new_costs['due_at_booking'], Decimal('193.86'))  # untouched
        # subtotal = 770 + 5.5% admin (42.35) = 812.35; balance = 812.35 - 193.86 (frozen deposit)
        self.assertEqual(new_costs['admin_fee'], Decimal('42.35'))
        self.assertEqual(new_costs['due_at_balance'], Decimal('618.49'))

    def test_removing_the_child_reduces_the_balance(self):
        new_guests, new_costs, changed = recalculate_balance_for_party(self.booking, [30, 32])
        self.assertTrue(changed)
        self.assertEqual(new_costs['basic_rental'], Decimal('700.00'))
        self.assertLess(new_costs['due_at_balance'], Decimal('581.57'))

    def test_balance_floors_at_zero_never_negative(self):
        self.charge.due_at_booking = Decimal('100000.00')
        self.charge.save(update_fields=['due_at_booking'])
        new_guests, new_costs, changed = recalculate_balance_for_party(self.booking, [30])
        self.assertEqual(new_costs['due_at_balance'], Decimal('0'))

    def test_unpriceable_stay_returns_all_none(self):
        self.booking.property.prices.all().delete()
        result = recalculate_balance_for_party(self.booking, [30, 32, 10])
        self.assertEqual(result, (None, None, None))


class CreateBookingTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property CB', short_title='TESTCB')
        PropertySpec.objects.create(property=self.property, max_guests=4)
        self.guest_data = {
            'first_name': 'Nuno', 'last_name': 'Pereira', 'email': 'nuno-cb@example.com', 'phone': '',
        }

    def _make_price(self, start, end):
        Price.objects.create(
            property=self.property, start_date=start, end_date=end,
            rate=Decimal('100.00'), extra_adult_rate=Decimal('10.00'), extra_child_rate=Decimal('5.00'),
        )

    def test_two_stage_booking_gets_a_balance_payment_row(self):
        start = date.today() + timedelta(days=200)
        end = start + timedelta(days=5)
        self._make_price(start, end)
        booking = create_booking(
            self.property, self.guest_data, start, end, {'adults': 2, 'children': 0, 'infants': 0},
        )
        self.assertTrue(hasattr(booking, 'balance_payment'))
        self.assertEqual(booking.balance_payment.provider, booking.payment.provider)
        self.assertEqual(booking.balance_payment.status, 'pending')

    def test_collapsed_booking_gets_no_balance_payment_row(self):
        start = date.today() + timedelta(days=10)  # well inside the 56-day window
        end = start + timedelta(days=5)
        self._make_price(start, end)
        booking = create_booking(
            self.property, self.guest_data, start, end, {'adults': 2, 'children': 0, 'infants': 0},
        )
        self.assertFalse(hasattr(booking, 'balance_payment'))
        self.assertEqual(booking.charges.due_at_balance, Decimal('0'))


class BookingDetailsViewTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property BD', short_title='TESTBD')
        PropertySpec.objects.create(property=self.property, max_guests=4)
        self.guest = Guest.objects.create(first_name='Vitor', last_name='Carvalho', email='vitor-bd@example.com')
        self.start = date.today() + timedelta(days=60)
        self.end = self.start + timedelta(days=7)
        Price.objects.create(
            property=self.property, start_date=self.start, end_date=self.end,
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

    def test_get_hides_cot_high_chair_when_no_infant_age_is_present(self):
        response = self.client.get(self.url)
        self.assertFalse(response.context['show_cot_high_chair'])

    def test_get_shows_cot_high_chair_when_an_existing_party_member_is_an_infant(self):
        BookingGuest.objects.create(booking=self.booking, first_name='Baby', last_name='Carvalho', age=1, is_lead=False)
        response = self.client.get(self.url)
        self.assertTrue(response.context['show_cot_high_chair'])

    def test_post_shows_cot_high_chair_when_a_submitted_age_is_an_infant(self):
        self._set_session()
        # Swapping the original 1 child for 1 infant changes the party's price bucket, which
        # triggers the price-change interstitial (200, not a redirect) - convenient here since it
        # means the re-rendered context is available to assert against directly.
        data = self._post_data(['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 1])
        response = self.client.post(self.url, data)
        self.assertTrue(response.context['price_changed'])
        self.assertTrue(response.context['show_cot_high_chair'])

    def test_get_includes_active_request_types_and_welcome_pack_items(self):
        RequestType.objects.create(name='Extra bed', default_price=Decimal('15.00'))
        RequestType.objects.create(name='Discontinued item', default_price=Decimal('5.00'), active=False)
        WelcomePackItem.objects.create(name='Red wine')
        response = self.client.get(self.url)
        request_type_names = [row['request_type'].name for row in response.context['request_rows']]
        self.assertEqual(request_type_names, ['Extra bed'])
        self.assertEqual(list(response.context['welcome_pack_items']), [WelcomePackItem.objects.get(name='Red wine')])

    def test_post_persists_welcome_pack_and_requested_extras(self):
        extra_bed = RequestType.objects.create(name='Extra bed', default_price=Decimal('15.00'))
        settings = ExtrasSettings.load()
        settings.welcome_pack_price = Decimal('12.50')
        settings.save()
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
        self.assertEqual(self.booking.extras.welcome_pack_charge, Decimal('12.50'))
        requested = self.booking.requested_extras.get()
        self.assertEqual(requested.request_type, extra_bed)
        self.assertEqual(requested.quantity, 1)
        self.assertEqual(requested.note, 'For the sofa bed')
        self.assertEqual(requested.price_at_request, Decimal('15.00'))

    def test_post_without_welcome_pack_leaves_food_drinks_and_charge_unset(self):
        self._set_session()
        data = self._post_data(['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 10])
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.booking.refresh_from_db()
        self.assertFalse(self.booking.extras.welcome_pack)
        self.assertIsNone(self.booking.extras.welcome_pack_food)
        self.assertIsNone(self.booking.extras.welcome_pack_drinks)
        self.assertIsNone(self.booking.extras.welcome_pack_charge)

    def test_post_persists_cot_and_high_chair_with_computed_combo_price(self):
        # self.start/self.end is a 7-night stay (short-stay tier, exactly at the boundary).
        settings = ExtrasSettings.load()
        settings.cot_price_short_stay = Decimal('20.00')
        settings.high_chair_price_short_stay = Decimal('15.00')
        settings.cot_and_high_chair_combo_discount_percent = Decimal('10.00')
        settings.save()
        self._set_session()
        data = self._post_data(['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 10])
        data['cot'] = 'on'
        data['high_chair'] = 'on'
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.booking.refresh_from_db()
        self.assertTrue(self.booking.extras.cot)
        self.assertTrue(self.booking.extras.high_chair)
        # 20 + 15 = 35, minus a 10% combo discount = 31.50
        self.assertEqual(self.booking.extras.cot_high_chair_charge, Decimal('31.50'))

    def test_post_without_cot_or_high_chair_charges_nothing(self):
        self._set_session()
        data = self._post_data(['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 10])
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.booking.refresh_from_db()
        self.assertFalse(self.booking.extras.cot)
        self.assertFalse(self.booking.extras.high_chair)
        self.assertEqual(self.booking.extras.cot_high_chair_charge, Decimal('0'))

    def test_post_persists_late_checkout_with_flat_charge(self):
        settings = ExtrasSettings.load()
        settings.late_checkout_price = Decimal('30.00')
        settings.save()
        self._set_session()
        data = self._post_data(['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 10])
        data['late_checkout'] = 'on'
        data['late_checkout_time'] = '13:00'
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.booking.refresh_from_db()
        self.assertTrue(self.booking.extras.late_checkout)
        self.assertEqual(self.booking.extras.late_checkout_time, time(13, 0))
        self.assertEqual(self.booking.extras.late_checkout_charge, Decimal('30.00'))

    def test_post_without_late_checkout_charges_nothing(self):
        self._set_session()
        data = self._post_data(['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 10])
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.booking.refresh_from_db()
        self.assertFalse(self.booking.extras.late_checkout)
        self.assertIsNone(self.booking.extras.late_checkout_time)
        self.assertIsNone(self.booking.extras.late_checkout_charge)

    def test_post_late_checkout_without_a_time_is_rejected(self):
        self._set_session()
        data = self._post_data(['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 10])
        data['late_checkout'] = 'on'
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['late_checkout_error'])
        self.assertEqual(self.booking.party.count(), 0)

    def test_post_late_checkout_with_an_invalid_time_is_rejected(self):
        self._set_session()
        data = self._post_data(['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 10])
        data['late_checkout'] = 'on'
        data['late_checkout_time'] = 'not-a-time'
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['late_checkout_error'])
        self.assertEqual(self.booking.party.count(), 0)

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
        settings = ExtrasSettings.load()
        settings.airport_transfer_price_1_4_guests = Decimal('25.00')
        settings.airport_transfer_price_5_8_guests = Decimal('45.00')
        settings.save()
        response = self.client.get(self.url)
        self.assertEqual(response.context['transfer_rows'], [])
        self.assertEqual(
            response.context['transfer_pricing_config']['bands'],
            [{'max_guests': 4, 'price': '25.00'}, {'max_guests': 8, 'price': '45.00'}],
        )

    def test_post_persists_airport_transfer_with_computed_price(self):
        settings = ExtrasSettings.load()
        settings.airport_transfer_price_1_4_guests = Decimal('25.00')
        settings.save()
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

    def test_two_stage_booking_hides_extras_on_details_and_skips_saving_them(self):
        # Push arrival far enough out that create_booking() doesn't collapse the split - needs its
        # own Price row too, since the original one only covers self.start/self.end.
        self.booking.arrival_date = date.today() + timedelta(days=200)
        self.booking.departure_date = self.booking.arrival_date + timedelta(days=7)
        self.booking.save(update_fields=['arrival_date', 'departure_date'])
        Price.objects.create(
            property=self.property, start_date=self.booking.arrival_date,
            end_date=self.booking.departure_date, rate=Decimal('100.00'),
            extra_adult_rate=Decimal('10.00'), extra_child_rate=Decimal('5.00'),
        )
        self.charge.due_at_balance = Decimal('500.00')
        self.charge.balance_due_date = self.booking.arrival_date - timedelta(days=56)
        self.charge.save(update_fields=['due_at_balance', 'balance_due_date'])
        BalancePayment.objects.create(booking=self.booking, provider='revolut')

        response = self.client.get(self.url)
        self.assertTrue(response.context['is_two_stage'])
        self.assertNotIn('welcome_pack_items', response.context)

        self._set_session()
        data = self._post_data(['Vitor', 'Joana', 'Ines'], ['Carvalho', 'Moura', 'Carvalho'], [30, 32, 10])
        data['welcome_pack'] = 'on'  # should be silently ignored - Extras aren't collected here
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.booking.refresh_from_db()
        self.assertFalse(hasattr(self.booking, 'extras') and self.booking.extras.welcome_pack)


class BookingBalanceDetailsViewTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property BAL', short_title='TESTBAL')
        PropertySpec.objects.create(property=self.property, max_guests=4)
        self.guest = Guest.objects.create(first_name='Elena', last_name='Costa', email='elena-bal@example.com')
        self.start = date.today() + timedelta(days=200)
        self.end = self.start + timedelta(days=7)
        Price.objects.create(
            property=self.property, start_date=self.start, end_date=self.end,
            rate=Decimal('100.00'), extra_adult_rate=Decimal('10.00'), extra_child_rate=Decimal('5.00'),
        )
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=1, babies=0, last_updated=timezone.now(),
        )
        # 2 adults + 1 child: FREE_ADULTS=2 means the first two adults are never priced extra
        # (properties/utils.py) - a child (unlike a 3rd+ adult) always adds extra_child_rate, so
        # this party actually changes price when a guest is added/removed, unlike a 2-adults-only
        # party would. Matches the same fixture numbers as RecalculateCostsForPartyTests.
        BookingGuest.objects.create(booking=self.booking, first_name='Elena', last_name='Costa', age=30, is_lead=True)
        BookingGuest.objects.create(booking=self.booking, first_name='Marco', last_name='Costa', age=32, is_lead=False)
        BookingGuest.objects.create(booking=self.booking, first_name='Ines', last_name='Costa', age=10, is_lead=False)
        self.charge = Charge.objects.create(
            booking=self.booking, basic_rental=Decimal('735.00'), admin=Decimal('40.43'),
            due_at_booking=Decimal('193.86'), due_at_balance=Decimal('581.57'),
            balance_due_date=self.start - timedelta(days=56), currency='EUR',
            gbp_conversion_rate=Decimal('0.8600'),
        )
        self.payment = Payment.objects.create(booking=self.booking, provider='revolut', status='paid')
        self.balance_payment = BalancePayment.objects.create(booking=self.booking, provider='revolut')
        self.url = reverse('bookings:balance_details', kwargs={'reference': self.booking.reference})
        self.pay_url = reverse('bookings:balance_pay', kwargs={'reference': self.booking.reference})
        self.confirmation_url = reverse('bookings:confirmation', kwargs={'reference': self.booking.reference})
        self.deposit_pay_url = reverse('bookings:pay', kwargs={'reference': self.booking.reference})

    def _post_data(self, first_names, last_names, ages, confirmed=False, **extras):
        data = {
            'first_name[]': first_names,
            'last_name[]': last_names,
            'age[]': [str(age) for age in ages],
        }
        if confirmed:
            data['confirmed'] = '1'
        data.update(extras)
        return data

    def _unchanged_party(self, **extras):
        return self._post_data(['Elena', 'Marco', 'Ines'], ['Costa', 'Costa', 'Costa'], [30, 32, 10], **extras)

    def test_get_redirects_to_confirmation_when_not_two_stage(self):
        self.balance_payment.delete()
        response = self.client.get(self.url)
        self.assertRedirects(response, self.confirmation_url, fetch_redirect_response=False)

    def test_get_redirects_to_deposit_pay_when_deposit_unpaid(self):
        self.payment.status = 'pending'
        self.payment.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertRedirects(response, self.deposit_pay_url, fetch_redirect_response=False)

    def test_get_redirects_to_confirmation_when_balance_already_paid(self):
        self.balance_payment.status = 'paid'
        self.balance_payment.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertRedirects(response, self.confirmation_url, fetch_redirect_response=False)

    def test_get_redirects_to_balance_pay_when_payment_in_progress(self):
        self.balance_payment.status = 'in_progress'
        self.balance_payment.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)

    def test_get_prefills_the_locked_in_guest_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['first_name'], 'Elena')
        self.assertEqual(rows[1]['first_name'], 'Marco')
        self.assertEqual(rows[2]['first_name'], 'Ines')
        self.assertIn('welcome_pack_items', response.context)
        self.assertFalse(response.context['show_cot_high_chair'])  # age 10 is a child, not an infant

    def test_show_cot_high_chair_reflects_a_submitted_infant_age(self):
        response = self.client.get(self.url)
        self.assertFalse(response.context['show_cot_high_chair'])
        # A deliberately invalid age on a throwaway 4th row guarantees a 200 re-render (not a
        # redirect), so the infant row's live effect on show_cot_high_chair can be asserted
        # directly - same trick BookingDetailsViewTests uses, without depending on whether adding
        # an infant (priced at zero, see get_stay_total_price) happens to also change the price.
        data = self._post_data(
            ['Elena', 'Marco', 'Baby', 'Oops'], ['Costa', 'Costa', 'Costa', 'Oops'], [30, 32, 1, 'not-a-number'],
        )
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['show_cot_high_chair'])

    def test_post_unchanged_party_persists_extras_and_redirects_to_balance_pay(self):
        settings = ExtrasSettings.load()
        settings.late_checkout_price = Decimal('20.00')
        settings.save()
        response = self.client.post(self.url, self._unchanged_party(late_checkout='on', late_checkout_time='13:00'))
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.booking.refresh_from_db()
        self.assertTrue(self.booking.extras.late_checkout)
        self.assertEqual(self.booking.extras.late_checkout_charge, Decimal('20.00'))
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.due_at_booking, Decimal('193.86'))  # untouched
        self.assertEqual(self.charge.due_at_balance, Decimal('581.57'))  # unchanged - same party

    def test_post_with_invalid_late_checkout_time_rerenders_with_error(self):
        response = self.client.post(self.url, self._unchanged_party(late_checkout='on', late_checkout_time='not-a-time'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['late_checkout_error'])
        self.assertFalse(hasattr(self.booking, 'extras'))

    def test_post_does_not_require_a_session_gate(self):
        # No _set_session() call anywhere in this test class - reference-alone auth is the point.
        response = self.client.post(self.url, self._unchanged_party())
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)

    def test_post_missing_guest_list_is_rejected(self):
        response = self.client.post(self.url, {})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['non_field_error'])

    def test_post_exceeding_max_guests_is_rejected(self):
        data = self._post_data(['A', 'B', 'C', 'D', 'E'], ['X'] * 5, [30, 30, 10, 10, 5])
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn('maximum', response.context['non_field_error'])

    def test_post_with_zero_adults_is_rejected(self):
        data = self._post_data(['A', 'B'], ['X', 'X'], [10, 8])
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn('adult', response.context['non_field_error'])

    def test_removing_a_guest_shows_the_price_change_interstitial_and_reduces_balance_when_confirmed(self):
        # Dropping Ines (the priced child): FREE_ADULTS=2 means Elena+Marco alone are no cheaper
        # or costlier than each other, but the child's extra_child_rate genuinely drops off.
        data = self._post_data(['Elena', 'Marco'], ['Costa', 'Costa'], [30, 32])
        response = self.client.post(self.url, data)
        self.assertTrue(response.context['price_changed'])
        self.assertLess(response.context['new_costs']['due_at_balance'], self.charge.due_at_balance)
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.due_at_balance, Decimal('581.57'))  # not yet persisted

        data['confirmed'] = '1'
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.charge.refresh_from_db()
        self.assertLess(self.charge.due_at_balance, Decimal('581.57'))
        self.assertEqual(self.charge.due_at_booking, Decimal('193.86'))  # deposit stays frozen
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.party.count(), 2)

    def test_adding_a_guest_increases_the_balance(self):
        # A 4th person (a 3rd adult, beyond FREE_ADULTS=2) genuinely adds extra_adult_rate on top
        # of the existing child.
        data = self._post_data(
            ['Elena', 'Marco', 'Ines', 'Sofia'], ['Costa', 'Costa', 'Costa', 'Costa'], [30, 32, 10, 25],
            confirmed=True,
        )
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.charge.refresh_from_db()
        self.assertGreater(self.charge.due_at_balance, Decimal('581.57'))
        self.assertEqual(self.charge.due_at_booking, Decimal('193.86'))  # deposit stays frozen

    def test_balance_never_goes_negative_even_if_new_total_undercuts_the_paid_deposit(self):
        # Force a deposit far larger than any recalculated total could produce.
        self.charge.due_at_booking = Decimal('100000.00')
        self.charge.save(update_fields=['due_at_booking'])
        data = self._post_data(['Elena'], ['Costa'], [30], confirmed=True)
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.due_at_balance, Decimal('0'))

    def test_price_change_clears_a_stale_revolut_checkout_url(self):
        self.balance_payment.revolut_order_id = 'old-order-id'
        self.balance_payment.revolut_checkout_url = 'https://checkout.revolut.com/old'
        self.balance_payment.save(update_fields=['revolut_order_id', 'revolut_checkout_url'])

        data = self._post_data(['Elena'], ['Costa'], [30], confirmed=True)
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.balance_payment.refresh_from_db()
        self.assertIsNone(self.balance_payment.revolut_order_id)
        self.assertIsNone(self.balance_payment.revolut_checkout_url)

    def test_unpriceable_stay_shows_contact_us_error(self):
        self.property.prices.all().delete()
        data = self._post_data(['Elena'], ['Costa'], [30])
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn('contact us', response.context['non_field_error'])

    def test_get_includes_arrival_departure_context_with_defaults(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context['arrival_method'], 'flight_faro')
        self.assertEqual(response.context['departure_method'], 'flight_faro')
        self.assertEqual(dict(response.context['departure_travel_methods'])['flight_faro'], 'Flight from Faro')

    def test_get_prefills_arrival_departure_from_existing_rows(self):
        Arrival.objects.create(
            booking=self.booking, method='driving', travelling_from='Lisbon', hiring_car=True, meet_greet=False,
        )
        Departure.objects.create(booking=self.booking, method='bus')
        response = self.client.get(self.url)
        self.assertEqual(response.context['arrival_method'], 'driving')
        self.assertEqual(response.context['arrival_travelling_from'], 'Lisbon')
        self.assertTrue(response.context['arrival_hiring_car'])
        self.assertEqual(response.context['departure_method'], 'bus')

    def test_post_unchanged_party_saves_arrival_and_departure(self):
        response = self.client.post(self.url, self._unchanged_party(
            arrival_method='flight_lisbon', arrival_flight_number='TP123', arrival_hiring_car='yes',
            departure_method='driving', departure_travelling_from='Lisbon',
        ))
        self.assertRedirects(response, self.pay_url, fetch_redirect_response=False)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.arrival.method, 'flight_lisbon')
        self.assertEqual(self.booking.arrival.flight_number, 'TP123')
        self.assertTrue(self.booking.arrival.hiring_car)
        self.assertEqual(self.booking.departure.method, 'driving')
        self.assertEqual(self.booking.departure.travelling_from, 'Lisbon')

    def test_post_invalid_flight_number_rerenders_without_saving_anything(self):
        response = self.client.post(self.url, self._unchanged_party(
            arrival_method='flight_faro', arrival_flight_number='DDPP-3QSK', late_checkout='on', late_checkout_time='13:00',
        ))
        self.assertEqual(response.status_code, 200)
        self.assertIn('arrival_flight_number', response.context['errors'])
        self.assertFalse(hasattr(self.booking, 'arrival'))
        self.assertFalse(hasattr(self.booking, 'extras'))  # nothing else silently saved either


class BookingBalancePaymentViewTests(TestCase):
    """Uses a Wise-path booking throughout (arrival month in WISE_MONTHS) - Wise never calls the
    live Revolut API (a static payment link, see BookingBalancePaymentView), so this can safely
    test the full render path. A Revolut-path booking would need the API mocked - out of scope
    here, matching the existing untested state of BookingPaymentView for the same reason."""

    def setUp(self):
        self.property = Property.objects.create(title='Test Property BALP', short_title='TESTBALP')
        PropertySpec.objects.create(property=self.property, max_guests=4)
        self.guest = Guest.objects.create(first_name='Marco', last_name='Reis', email='marco-balp@example.com')
        self.start = self._next_wise_season_date()
        self.end = self.start + timedelta(days=7)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.charge = Charge.objects.create(
            booking=self.booking, basic_rental=Decimal('700.00'), admin=Decimal('38.50'),
            due_at_booking=Decimal('184.63'), due_at_balance=Decimal('553.87'),
            balance_due_date=self.start - timedelta(days=56), currency='EUR',
            gbp_conversion_rate=Decimal('0.8600'),
        )
        self.payment = Payment.objects.create(booking=self.booking, provider='wise', status='paid')
        self.balance_payment = BalancePayment.objects.create(booking=self.booking, provider='wise')
        self.url = reverse('bookings:balance_pay', kwargs={'reference': self.booking.reference})
        self.confirmation_url = reverse('bookings:confirmation', kwargs={'reference': self.booking.reference})
        self.deposit_pay_url = reverse('bookings:pay', kwargs={'reference': self.booking.reference})

    def _next_wise_season_date(self):
        candidate = date.today() + timedelta(days=100)  # safely more than 56 days out
        while candidate.month not in (11, 12, 1, 2, 3):
            candidate += timedelta(days=1)
        return candidate

    def test_get_redirects_to_confirmation_when_not_two_stage(self):
        self.balance_payment.delete()
        response = self.client.get(self.url)
        self.assertRedirects(response, self.confirmation_url, fetch_redirect_response=False)

    def test_get_redirects_to_deposit_pay_when_deposit_unpaid(self):
        self.payment.status = 'pending'
        self.payment.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertRedirects(response, self.deposit_pay_url, fetch_redirect_response=False)

    def test_get_redirects_to_confirmation_when_balance_already_paid(self):
        self.balance_payment.status = 'paid'
        self.balance_payment.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertRedirects(response, self.confirmation_url, fetch_redirect_response=False)

    def test_get_renders_wise_payment_page_with_correct_amount(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['pay_amount'], Decimal('553.87'))
        self.assertEqual(response.context['pay_currency'], 'EUR')
        self.assertContains(response, "Pay via Wise")

    def test_get_includes_extras_summary(self):
        Extra.objects.create(
            booking=self.booking, late_checkout=True, late_checkout_charge=Decimal('20.00'),
        )
        response = self.client.get(self.url)
        self.assertEqual(response.context['extras']['total'], Decimal('20.00'))


class WelcomePackItemMatchesTests(TestCase):
    def test_variant_specific_item_only_matches_its_own_variant(self):
        ham = WelcomePackItem(name='Ham', category=WelcomePackItem.Category.FOOD_STANDARD)
        self.assertTrue(ham.matches(food_choice='standard', drinks_choice='alcoholic'))
        self.assertFalse(ham.matches(food_choice='vegan', drinks_choice='alcoholic'))

    def test_common_item_matches_either_variant_on_its_axis(self):
        water = WelcomePackItem(name='Water', category=WelcomePackItem.Category.DRINKS_COMMON)
        self.assertTrue(water.matches(food_choice='standard', drinks_choice='alcoholic'))
        self.assertTrue(water.matches(food_choice='vegan', drinks_choice='non_alcoholic'))


class ExtrasSettingsTransferPricingTests(TestCase):
    def setUp(self):
        self.settings = ExtrasSettings.load()
        self.settings.airport_transfer_price_1_4_guests = Decimal('25.00')
        self.settings.airport_transfer_price_5_8_guests = Decimal('45.00')
        self.settings.airport_transfer_night_surcharge = Decimal('10.00')
        self.settings.airport_transfer_night_window_start = time(22, 0)
        self.settings.airport_transfer_night_window_end = time(6, 0)
        self.settings.save()

    def test_one_to_four_guests_uses_the_lower_tier(self):
        self.assertEqual(self.settings.compute_transfer_price(1, time(14, 0)), Decimal('25.00'))
        self.assertEqual(self.settings.compute_transfer_price(4, time(14, 0)), Decimal('25.00'))

    def test_five_to_eight_guests_uses_the_higher_tier(self):
        self.assertEqual(self.settings.compute_transfer_price(5, time(14, 0)), Decimal('45.00'))
        self.assertEqual(self.settings.compute_transfer_price(8, time(14, 0)), Decimal('45.00'))

    def test_more_than_eight_guests_returns_none(self):
        # more guests than a single transfer can carry - staff book separate transfers instead of
        # there being a third price tier (see compute_transfer_price's docstring).
        self.assertIsNone(self.settings.compute_transfer_price(9, time(14, 0)))

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

    def test_non_wrapping_window_still_works(self):
        self.settings.airport_transfer_night_window_start = time(1, 0)
        self.settings.airport_transfer_night_window_end = time(5, 0)
        self.settings.save()
        self.assertEqual(self.settings.compute_transfer_price(2, time(3, 0)), Decimal('35.00'))
        self.assertEqual(self.settings.compute_transfer_price(2, time(14, 0)), Decimal('25.00'))


class ExtrasSettingsCotHighChairPricingTests(TestCase):
    def setUp(self):
        self.settings = ExtrasSettings.load()
        self.settings.cot_price_short_stay = Decimal('20.00')
        self.settings.cot_price_long_stay = Decimal('35.00')
        self.settings.high_chair_price_short_stay = Decimal('15.00')
        self.settings.high_chair_price_long_stay = Decimal('25.00')
        self.settings.cot_and_high_chair_combo_discount_percent = Decimal('10.00')
        self.settings.save()

    def test_cot_only_short_stay(self):
        self.assertEqual(self.settings.compute_cot_high_chair_price(7, True, False), Decimal('20.00'))

    def test_cot_only_long_stay(self):
        self.assertEqual(self.settings.compute_cot_high_chair_price(8, True, False), Decimal('35.00'))

    def test_high_chair_only_short_stay(self):
        self.assertEqual(self.settings.compute_cot_high_chair_price(7, False, True), Decimal('15.00'))

    def test_high_chair_only_long_stay(self):
        self.assertEqual(self.settings.compute_cot_high_chair_price(8, False, True), Decimal('25.00'))

    def test_neither_requested_is_zero(self):
        self.assertEqual(self.settings.compute_cot_high_chair_price(10, False, False), Decimal('0'))

    def test_combo_discount_applies_only_when_both_requested(self):
        # short stay: (20 + 15) * 0.90 = 31.50
        self.assertEqual(self.settings.compute_cot_high_chair_price(5, True, True), Decimal('31.50'))
        # long stay: (35 + 25) * 0.90 = 54.00
        self.assertEqual(self.settings.compute_cot_high_chair_price(10, True, True), Decimal('54.00'))

    def test_combo_discount_never_makes_price_negative(self):
        # a discount over 100% (bypassing the field's normal 0-100 validator here) should still clamp at 0
        self.settings.cot_and_high_chair_combo_discount_percent = Decimal('999.00')
        self.settings.save()
        self.assertEqual(self.settings.compute_cot_high_chair_price(5, True, True), Decimal('0'))


class ExtrasSummaryTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property ES', short_title='TESTES')
        self.guest = Guest.objects.create(first_name='Rui', last_name='Nunes', email='rui-es@example.com')
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest,
            arrival_date=date.today() + timedelta(days=30), departure_date=date.today() + timedelta(days=35),
            is_owner=False, enquiry_status='Awaiting payment', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_no_extras_requested_returns_empty_summary(self):
        summary = extras_summary(self.booking)
        self.assertEqual(summary['items'], [])
        self.assertEqual(summary['total'], Decimal('0'))

    def test_summary_itemises_every_requested_extra(self):
        Extra.objects.create(
            booking=self.booking,
            welcome_pack=True, welcome_pack_food='vegan', welcome_pack_drinks='non_alcoholic',
            welcome_pack_charge=Decimal('12.50'),
            cot=True, high_chair=True, cot_high_chair_charge=Decimal('25.00'),
            late_checkout=True, late_checkout_time=time(13, 0), late_checkout_charge=Decimal('30.00'),
        )
        AirportTransfer.objects.create(
            booking=self.booking, direction=AirportTransferDirection.INBOUND,
            flight_number='TP1234', time=time(14, 30), adults=2,
            price_at_request=Decimal('25.00'),
        )
        bed = RequestType.objects.create(name='Extra bed', default_price=Decimal('15.00'))
        BookingRequestedExtra.objects.create(
            booking=self.booking, request_type=bed, quantity=2, price_at_request=Decimal('15.00'),
        )

        summary = extras_summary(self.booking)
        labels = [item['label'] for item in summary['items']]
        self.assertEqual(labels, [
            'Welcome Pack (Vegan, Non-alcoholic)',
            'Cot & High Chair',
            'Late Checkout (13:00)',
            'Airport Transfer - Inbound (arrival) (TP1234)',
            'Extra bed x2',
        ])
        self.assertEqual(summary['total'], Decimal('12.50') + Decimal('25.00') + Decimal('30.00')
                          + Decimal('25.00') + Decimal('30.00'))

    def test_cot_only_uses_singular_label(self):
        Extra.objects.create(booking=self.booking, cot=True, cot_high_chair_charge=Decimal('20.00'))
        summary = extras_summary(self.booking)
        self.assertEqual(summary['items'], [{'label': 'Cot', 'price': Decimal('20.00')}])


class BookingDateAdjustmentTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property DA', short_title='TESTDA')
        self.guest = Guest.objects.create(last_name='Guest')
        self.start = date(2026, 9, 1)
        self.end = date(2026, 9, 8)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Airbnb',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_creating_an_adjustment_snapshots_previous_dates_and_updates_the_booking(self):
        new_departure = self.end + timedelta(days=2)
        adjustment = BookingDateAdjustment.objects.create(
            booking=self.booking, new_arrival_date=self.start, new_departure_date=new_departure,
            additional_charge=Decimal('120.00'), notes='Guest paying cash on arrival',
        )
        self.assertEqual(adjustment.previous_arrival_date, self.start)
        self.assertEqual(adjustment.previous_departure_date, self.end)

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.departure_date, new_departure)
        self.assertTrue(self.booking.manual_override)

    def test_second_chained_adjustment_snapshots_from_the_first_adjustments_result(self):
        first_new_departure = self.end + timedelta(days=2)
        BookingDateAdjustment.objects.create(
            booking=self.booking, new_arrival_date=self.start, new_departure_date=first_new_departure,
        )
        self.booking.refresh_from_db()

        second_new_departure = first_new_departure + timedelta(days=1)
        second = BookingDateAdjustment.objects.create(
            booking=self.booking, new_arrival_date=self.start, new_departure_date=second_new_departure,
        )
        self.assertEqual(second.previous_departure_date, first_new_departure)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.departure_date, second_new_departure)

    def test_editing_an_existing_adjustment_does_not_recascade(self):
        adjustment = BookingDateAdjustment.objects.create(
            booking=self.booking, new_arrival_date=self.start, new_departure_date=self.end + timedelta(days=2),
        )
        self.booking.refresh_from_db()
        booking_dates_after_first_save = (self.booking.arrival_date, self.booking.departure_date)

        adjustment.notes = 'corrected note, not a new date change'
        adjustment.save()

        self.booking.refresh_from_db()
        self.assertEqual((self.booking.arrival_date, self.booking.departure_date), booking_dates_after_first_save)
        adjustment.refresh_from_db()
        self.assertEqual(adjustment.previous_departure_date, self.end)  # unchanged by the edit


class BalanceReminderDueFilterTests(TestCase):
    """Direct test of the filter's queryset() logic (via RequestFactory, not a logged-in admin
    session) - the filter itself is a thin wrapper around a date-boundary query, and this exercises
    that boundary without the overhead of a full authenticated admin-view test."""

    def setUp(self):
        from bookings.admin import BalanceReminderDueFilter

        self.filter_class = BalanceReminderDueFilter
        self.settings = BookingSettings.load()
        self.settings.balance_reminder_days_before_arrival = 63
        self.settings.save()

        self.property = Property.objects.create(title='Test Property BR', short_title='TESTBR')
        self.guest = Guest.objects.create(first_name='Sara', last_name='Alves', email='sara-br@example.com')

    def _make_balance_payment(self, days_until_arrival, enquiry_status='Booking confirmed', **overrides):
        booking = Booking.objects.create(
            property=self.property, guest=self.guest,
            arrival_date=date.today() + timedelta(days=days_until_arrival),
            departure_date=date.today() + timedelta(days=days_until_arrival + 5),
            is_owner=False, enquiry_status=enquiry_status, enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        defaults = {'booking': booking, 'provider': 'revolut', 'status': 'pending'}
        defaults.update(overrides)
        return BalancePayment.objects.create(**defaults)

    def _filtered(self, value='yes'):
        request = RequestFactory().get('/admin/bookings/balancepayment/')
        params = {'reminder_due': [value]} if value is not None else {}
        filter_instance = self.filter_class(request, params, BalancePayment, None)
        return filter_instance.queryset(request, BalancePayment.objects.all())

    def test_includes_a_pending_unreminded_booking_inside_the_window(self):
        due = self._make_balance_payment(days_until_arrival=30)
        self.assertIn(due, self._filtered())

    def test_excludes_a_booking_still_outside_the_window(self):
        not_due = self._make_balance_payment(days_until_arrival=100)
        self.assertNotIn(not_due, self._filtered())

    def test_excludes_an_already_reminded_booking(self):
        already_reminded = self._make_balance_payment(days_until_arrival=30, reminder_sent=True)
        self.assertNotIn(already_reminded, self._filtered())

    def test_excludes_an_already_paid_balance(self):
        already_paid = self._make_balance_payment(days_until_arrival=30, status='paid')
        self.assertNotIn(already_paid, self._filtered())

    def test_excludes_a_booking_whose_deposit_failed_or_was_cancelled(self):
        failed = self._make_balance_payment(days_until_arrival=30, enquiry_status='Payment failed')
        cancelled = self._make_balance_payment(days_until_arrival=30, enquiry_status='Cancelled by guest')
        result = self._filtered()
        self.assertNotIn(failed, result)
        self.assertNotIn(cancelled, result)

    def test_unfiltered_value_returns_everything(self):
        due = self._make_balance_payment(days_until_arrival=30)
        not_due = self._make_balance_payment(days_until_arrival=100)
        result = self._filtered(value=None)
        self.assertIn(due, result)
        self.assertIn(not_due, result)


class ManageBookingViewTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property MNG', short_title='TESTMNG')
        PropertySpec.objects.create(property=self.property, max_guests=4)
        self.guest = Guest.objects.create(first_name='Nuno', last_name='Rocha', email='nuno-mng@example.com')
        self.start = date.today() + timedelta(days=200)
        self.end = self.start + timedelta(days=7)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.url = reverse('bookings:manage')

    def _post(self, reference, email):
        return self.client.post(self.url, {'reference': reference, 'email': email})

    def test_unknown_reference_shows_not_found(self):
        response = self._post('NOTAREF', self.guest.email)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['not_found'])

    def test_unpaid_booking_redirects_to_pay(self):
        Payment.objects.create(booking=self.booking, provider='revolut', status='pending')
        response = self._post(self.booking.reference, self.guest.email)
        self.assertRedirects(
            response, reverse('bookings:pay', kwargs={'reference': self.booking.reference}),
            fetch_redirect_response=False,
        )

    def test_paid_booking_redirects_to_manage_hub(self):
        Payment.objects.create(booking=self.booking, provider='revolut', status='paid')
        response = self._post(self.booking.reference, self.guest.email)
        self.assertRedirects(
            response, reverse('bookings:manage_hub', kwargs={'reference': self.booking.reference}),
            fetch_redirect_response=False,
        )


class BookingManageHubViewTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property HUB', short_title='TESTHUB')
        PropertySpec.objects.create(property=self.property, max_guests=4)
        self.guest = Guest.objects.create(first_name='Elena', last_name='Costa', email='elena-hub@example.com')
        self.start = date.today() + timedelta(days=200)
        self.end = self.start + timedelta(days=7)
        Price.objects.create(
            property=self.property, start_date=self.start, end_date=self.end,
            rate=Decimal('100.00'), extra_adult_rate=Decimal('10.00'), extra_child_rate=Decimal('5.00'),
        )
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        BookingGuest.objects.create(booking=self.booking, first_name='Elena', last_name='Costa', age=30, is_lead=True)
        BookingGuest.objects.create(booking=self.booking, first_name='Marco', last_name='Costa', age=32, is_lead=False)
        Charge.objects.create(
            booking=self.booking, basic_rental=Decimal('700.00'), admin=Decimal('38.50'),
            due_at_booking=Decimal('184.63'), due_at_balance=Decimal('553.87'),
            balance_due_date=self.start - timedelta(days=56), currency='EUR',
            gbp_conversion_rate=Decimal('0.8600'),
        )
        self.url = reverse('bookings:manage_hub', kwargs={'reference': self.booking.reference})
        self.details_url = reverse('bookings:details', kwargs={'reference': self.booking.reference})
        self.balance_details_url = reverse('bookings:balance_details', kwargs={'reference': self.booking.reference})

    def test_not_paid_redirects_to_details(self):
        Payment.objects.create(booking=self.booking, provider='revolut', status='pending')
        response = self.client.get(self.url)
        self.assertRedirects(response, self.details_url, fetch_redirect_response=False)

    def test_two_stage_not_balance_paid_shows_pay_balance_nav_item(self):
        Payment.objects.create(booking=self.booking, provider='revolut', status='paid')
        BalancePayment.objects.create(booking=self.booking, provider='revolut', status='pending')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['stage'], 'pre_balance')
        self.assertTrue(response.context['show_pay_balance'])
        self.assertEqual(response.context['active_section'], 'booking')
        self.assertContains(response, self.balance_details_url)

    def test_fully_paid_two_stage_hides_pay_balance_nav_item(self):
        Payment.objects.create(booking=self.booking, provider='revolut', status='paid')
        BalancePayment.objects.create(booking=self.booking, provider='revolut', status='paid')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['stage'], 'fully_paid')
        self.assertFalse(response.context['show_pay_balance'])
        self.assertNotContains(response, self.balance_details_url)

    def test_collapsed_booking_paid_deposit_only_counts_as_fully_paid(self):
        # No BalancePayment row at all - a collapsed (single-payment) booking.
        Payment.objects.create(booking=self.booking, provider='revolut', status='paid')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['stage'], 'fully_paid')

    def test_sidebar_links_to_every_section(self):
        Payment.objects.create(booking=self.booking, provider='revolut', status='paid')
        response = self.client.get(self.url)
        self.assertContains(response, reverse('bookings:manage_guests', kwargs={'reference': self.booking.reference}))
        self.assertContains(
            response, reverse('bookings:manage_arrival_departure', kwargs={'reference': self.booking.reference}),
        )
        self.assertContains(response, reverse('bookings:manage_extras', kwargs={'reference': self.booking.reference}))


class BookingManageGuestAddViewTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property GAD', short_title='TESTGAD')
        PropertySpec.objects.create(property=self.property, max_guests=4)
        self.guest = Guest.objects.create(first_name='Elena', last_name='Costa', email='elena-gad@example.com')
        self.start = date.today() + timedelta(days=200)
        self.end = self.start + timedelta(days=7)
        Price.objects.create(
            property=self.property, start_date=self.start, end_date=self.end,
            rate=Decimal('100.00'), extra_adult_rate=Decimal('10.00'), extra_child_rate=Decimal('5.00'),
        )
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.elena = BookingGuest.objects.create(booking=self.booking, first_name='Elena', last_name='Costa', age=30, is_lead=True)
        self.marco = BookingGuest.objects.create(booking=self.booking, first_name='Marco', last_name='Costa', age=32, is_lead=False)
        # 2 adults only: FREE_ADULTS=2 means this party is currently at the free-adult ceiling, so
        # a 3rd adult genuinely adds extra_adult_rate - see RecalculateCostsForPartyTests for the
        # same underlying pricing fixture.
        self.charge = Charge.objects.create(
            booking=self.booking, basic_rental=Decimal('700.00'), admin=Decimal('38.50'),
            due_at_booking=Decimal('184.63'), due_at_balance=Decimal('553.87'),
            balance_due_date=self.start - timedelta(days=56), currency='EUR',
            gbp_conversion_rate=Decimal('0.8600'),
        )
        Payment.objects.create(booking=self.booking, provider='revolut', status='paid')
        BalancePayment.objects.create(booking=self.booking, provider='revolut', status='paid')
        self.url = reverse('bookings:manage_hub_guest_add', kwargs={'reference': self.booking.reference})
        self.guests_url = reverse('bookings:manage_guests', kwargs={'reference': self.booking.reference})

    def _post_data(self, first_names, last_names, ages, confirmed=False):
        data = {
            'first_name[]': first_names,
            'last_name[]': last_names,
            'age[]': [str(age) for age in ages],
        }
        if confirmed:
            data['confirmed'] = '1'
        return data

    def test_get_redirects_to_hub_with_no_side_effects(self):
        response = self.client.get(self.url)
        self.assertRedirects(response, self.guests_url, fetch_redirect_response=False)
        self.assertEqual(GuestListAdjustment.objects.count(), 0)

    def test_post_while_not_fully_paid_redirects_and_does_nothing(self):
        self.balance_payment = BalancePayment.objects.get(booking=self.booking)
        self.balance_payment.status = 'pending'
        self.balance_payment.save(update_fields=['status'])
        response = self.client.post(self.url, self._post_data(['Sofia'], ['Costa'], [25], confirmed=True))
        self.assertRedirects(response, self.guests_url, fetch_redirect_response=False)
        self.assertEqual(GuestListAdjustment.objects.count(), 0)
        self.assertEqual(self.booking.party.count(), 2)

    def test_unconfirmed_post_shows_confirmation_banner_and_writes_nothing(self):
        response = self.client.post(self.url, self._post_data(['Sofia'], ['Costa'], [25]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('pending_guest_addition', response.context)
        self.assertGreater(response.context['pending_guest_addition']['additional_charge'], Decimal('0'))
        self.assertEqual(GuestListAdjustment.objects.count(), 0)
        self.assertEqual(self.booking.party.count(), 2)

    def test_unconfirmed_post_skips_the_interstitial_when_theres_nothing_to_confirm(self):
        """Real bug report: the interstitial used to show unconditionally, even asking a guest to
        'confirm' a charge that was actually 0.00 - starting from 1 named adult (Marco removed) and
        adding a 2nd stays within the free-adult ceiling (see setUp's own FREE_ADULTS=2 comment),
        so there's nothing to confirm and this should save immediately without a `confirmed` field
        at all, the same way a POST with confirmed=1 already does."""
        self.marco.delete()
        response = self.client.post(self.url, self._post_data(['Sofia'], ['Costa'], [25]))
        self.assertRedirects(response, f"{self.guests_url}?guest_added=1", fetch_redirect_response=False)
        self.assertEqual(GuestListAdjustment.objects.count(), 1)
        self.assertEqual(GuestListAdjustment.objects.get().additional_charge, Decimal('0'))

    def test_confirmed_post_adds_guest_without_touching_charge(self):
        response = self.client.post(self.url, self._post_data(['Sofia'], ['Costa'], [25], confirmed=True))
        self.assertRedirects(response, f"{self.guests_url}?guest_added=1", fetch_redirect_response=False)

        self.assertEqual(GuestListAdjustment.objects.count(), 1)
        adjustment = GuestListAdjustment.objects.get()
        self.assertEqual(adjustment.previous_party_size, 2)
        self.assertEqual(adjustment.new_party_size, 3)
        self.assertGreater(adjustment.additional_charge, Decimal('0'))

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.party.count(), 3)
        self.assertEqual(self.booking.adults, 3)
        new_guest = self.booking.party.get(first_name='Sofia')
        self.assertEqual(new_guest.added_via_adjustment, adjustment)

        self.charge.refresh_from_db()
        self.assertEqual(self.charge.basic_rental, Decimal('700.00'))  # untouched
        self.assertEqual(self.charge.due_at_balance, Decimal('553.87'))  # untouched

    def test_existing_guests_are_never_modified(self):
        self.client.post(self.url, self._post_data(['Sofia'], ['Costa'], [25], confirmed=True))
        self.elena.refresh_from_db()
        self.marco.refresh_from_db()
        self.assertIsNone(self.elena.added_via_adjustment)
        self.assertIsNone(self.marco.added_via_adjustment)
        self.assertEqual(self.elena.age, 30)
        self.assertEqual(self.marco.age, 32)

    def test_max_guests_exceeded_is_rejected(self):
        response = self.client.post(
            self.url, self._post_data(['A', 'B', 'C'], ['X', 'X', 'X'], [20, 20, 20], confirmed=True),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('maximum', response.context['guest_add_error'])
        self.assertEqual(GuestListAdjustment.objects.count(), 0)

    def test_unpriceable_stay_shows_contact_us_error(self):
        self.property.prices.all().delete()
        response = self.client.post(self.url, self._post_data(['Sofia'], ['Costa'], [25], confirmed=True))
        self.assertEqual(response.status_code, 200)
        self.assertIn('contact us', response.context['guest_add_error'])
        self.assertEqual(GuestListAdjustment.objects.count(), 0)

    def test_additional_charge_floors_at_zero(self):
        # An infant (priced at zero) added on top of a deliberately inflated existing charge can
        # never make additional_charge negative - mirrors the balance-stage floor-at-zero test.
        self.charge.basic_rental = Decimal('100000.00')
        self.charge.admin = Decimal('0.00')
        self.charge.save(update_fields=['basic_rental', 'admin'])
        response = self.client.post(self.url, self._post_data(['Baby'], ['Costa'], [1], confirmed=True))
        self.assertRedirects(response, f"{self.guests_url}?guest_added=1", fetch_redirect_response=False)
        adjustment = GuestListAdjustment.objects.get()
        self.assertEqual(adjustment.additional_charge, Decimal('0'))


class BookingManageGuestRemoveViewTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property GRM', short_title='TESTGRM')
        PropertySpec.objects.create(property=self.property, max_guests=4)
        self.guest = Guest.objects.create(first_name='Elena', last_name='Costa', email='elena-grm@example.com')
        self.start = date.today() + timedelta(days=200)
        self.end = self.start + timedelta(days=7)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.lead = BookingGuest.objects.create(booking=self.booking, first_name='Elena', last_name='Costa', age=30, is_lead=True)
        self.marco = BookingGuest.objects.create(booking=self.booking, first_name='Marco', last_name='Costa', age=32, is_lead=False)
        Charge.objects.create(
            booking=self.booking, basic_rental=Decimal('700.00'), admin=Decimal('38.50'),
            due_at_booking=Decimal('184.63'), due_at_balance=Decimal('553.87'),
            balance_due_date=self.start - timedelta(days=56), currency='EUR',
            gbp_conversion_rate=Decimal('0.8600'),
        )
        Payment.objects.create(booking=self.booking, provider='revolut', status='paid')
        BalancePayment.objects.create(booking=self.booking, provider='revolut', status='paid')
        self.url = reverse('bookings:manage_hub_guest_remove', kwargs={'reference': self.booking.reference})
        self.guests_url = reverse('bookings:manage_guests', kwargs={'reference': self.booking.reference})

    def test_removes_a_non_lead_guest(self):
        response = self.client.post(self.url, {'guest_id': self.marco.pk})
        self.assertRedirects(response, f"{self.guests_url}?guest_removed=1", fetch_redirect_response=False)
        self.assertFalse(BookingGuest.objects.filter(pk=self.marco.pk).exists())
        self.assertEqual(self.booking.party.count(), 1)

    def test_resyncs_adults_children_babies_from_the_remaining_party(self):
        """Real bug report: removing a guest left Booking.adults/children/babies stale, so
        staff's own booking-detail page kept showing the pre-removal headcount."""
        self.client.post(self.url, {'guest_id': self.marco.pk})
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.adults, 1)  # only lead-guest Elena (age 30) left
        self.assertEqual(self.booking.children, 0)
        self.assertEqual(self.booking.babies, 0)

    def test_resyncs_babies_count_when_an_infant_is_removed(self):
        infant = BookingGuest.objects.create(booking=self.booking, first_name='Baby', last_name='Costa', age=1, is_lead=False)
        self.booking.babies = 1
        self.booking.save(update_fields=['babies'])
        self.client.post(self.url, {'guest_id': infant.pk})
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.babies, 0)
        self.assertEqual(self.booking.adults, 2)  # Elena + Marco, both untouched

    def test_creates_an_audit_adjustment_with_no_charge(self):
        self.client.post(self.url, {'guest_id': self.marco.pk})
        adjustment = GuestListAdjustment.objects.get()
        self.assertEqual(adjustment.previous_party_size, 2)
        self.assertEqual(adjustment.new_party_size, 1)
        self.assertEqual(adjustment.additional_charge, Decimal('0'))

    def test_never_touches_charge(self):
        self.client.post(self.url, {'guest_id': self.marco.pk})
        charge = self.booking.charges
        self.assertEqual(charge.due_at_booking, Decimal('184.63'))
        self.assertEqual(charge.due_at_balance, Decimal('553.87'))

    def test_cannot_remove_the_lead_guest(self):
        response = self.client.post(self.url, {'guest_id': self.lead.pk})
        self.assertRedirects(response, self.guests_url, fetch_redirect_response=False)
        self.assertTrue(BookingGuest.objects.filter(pk=self.lead.pk).exists())
        self.assertEqual(GuestListAdjustment.objects.count(), 0)

    def test_unknown_guest_id_is_a_noop(self):
        response = self.client.post(self.url, {'guest_id': 999999})
        self.assertRedirects(response, self.guests_url, fetch_redirect_response=False)
        self.assertEqual(GuestListAdjustment.objects.count(), 0)

    def test_not_fully_paid_redirects_and_does_nothing(self):
        self.booking.balance_payment.status = 'pending'
        self.booking.balance_payment.save(update_fields=['status'])
        response = self.client.post(self.url, {'guest_id': self.marco.pk})
        self.assertRedirects(response, self.guests_url, fetch_redirect_response=False)
        self.assertTrue(BookingGuest.objects.filter(pk=self.marco.pk).exists())

    def test_lead_guest_has_no_remove_control_on_the_page(self):
        response = self.client.get(self.guests_url)
        self.assertNotContains(response, f'value="{self.lead.pk}"')
        self.assertContains(response, f'value="{self.marco.pk}"')


class BookingManageExtrasViewTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property MEX', short_title='TESTMEX')
        PropertySpec.objects.create(property=self.property, max_guests=4)
        self.guest = Guest.objects.create(first_name='Elena', last_name='Costa', email='elena-mex@example.com')
        self.start = date.today() + timedelta(days=200)
        self.end = self.start + timedelta(days=7)
        Price.objects.create(
            property=self.property, start_date=self.start, end_date=self.end,
            rate=Decimal('100.00'), extra_adult_rate=Decimal('10.00'), extra_child_rate=Decimal('5.00'),
        )
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        BookingGuest.objects.create(booking=self.booking, first_name='Elena', last_name='Costa', age=30, is_lead=True)
        Charge.objects.create(
            booking=self.booking, basic_rental=Decimal('700.00'), admin=Decimal('38.50'),
            due_at_booking=Decimal('184.63'), due_at_balance=Decimal('553.87'),
            balance_due_date=self.start - timedelta(days=56), currency='EUR',
            gbp_conversion_rate=Decimal('0.8600'),
        )
        Payment.objects.create(booking=self.booking, provider='revolut', status='paid')
        BalancePayment.objects.create(booking=self.booking, provider='revolut', status='paid')
        settings = ExtrasSettings.load()
        settings.late_checkout_price = Decimal('20.00')
        settings.save()
        self.url = reverse('bookings:manage_extras', kwargs={'reference': self.booking.reference})
        self.details_url = reverse('bookings:details', kwargs={'reference': self.booking.reference})
        self.balance_details_url = reverse('bookings:balance_details', kwargs={'reference': self.booking.reference})

    def test_get_redirects_to_details_when_not_paid(self):
        self.booking.payment.status = 'pending'
        self.booking.payment.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertRedirects(response, self.details_url, fetch_redirect_response=False)

    def test_get_is_accessible_when_two_stage_not_balance_paid(self):
        # Extras are reachable as soon as the deposit is paid now - no balance-paid gate at all.
        self.booking.balance_payment.status = 'pending'
        self.booking.balance_payment.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_get_within_cutoff_is_editable(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['extras_locked'])

    def test_post_within_cutoff_persists_extras_without_touching_charge(self):
        response = self.client.post(self.url, {'late_checkout': 'on', 'late_checkout_time': '13:00'})
        self.assertRedirects(response, f"{self.url}?extras_saved=1", fetch_redirect_response=False)
        self.booking.refresh_from_db()
        self.assertTrue(self.booking.extras.late_checkout)
        self.assertEqual(self.booking.extras.late_checkout_charge, Decimal('20.00'))
        charge = Charge.objects.get(booking=self.booking)
        self.assertEqual(charge.basic_rental, Decimal('700.00'))  # untouched

    def test_get_locked_past_cutoff_is_read_only(self):
        settings = BookingSettings.load()
        settings.extras_edit_cutoff_days_before_arrival = 3
        settings.save()
        self.booking.arrival_date = date.today() + timedelta(days=2)
        self.booking.save(update_fields=['arrival_date'])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['extras_locked'])

    def test_post_locked_past_cutoff_is_a_no_op(self):
        settings = BookingSettings.load()
        settings.extras_edit_cutoff_days_before_arrival = 3
        settings.save()
        self.booking.arrival_date = date.today() + timedelta(days=2)
        self.booking.save(update_fields=['arrival_date'])
        response = self.client.post(self.url, {'late_checkout': 'on', 'late_checkout_time': '13:00'})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertFalse(hasattr(self.booking, 'extras'))


class GuestListAdjustmentTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property GLA', short_title='TESTGLA')
        self.guest = Guest.objects.create(last_name='Guest')
        self.start = date(2026, 9, 1)
        self.end = date(2026, 9, 8)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_creation_persists_fields(self):
        adjustment = GuestListAdjustment.objects.create(
            booking=self.booking, previous_party_size=2, new_party_size=3,
            additional_charge=Decimal('45.00'), notes='Added via hub',
        )
        adjustment.refresh_from_db()
        self.assertEqual(adjustment.previous_party_size, 2)
        self.assertEqual(adjustment.new_party_size, 3)
        self.assertEqual(adjustment.additional_charge, Decimal('45.00'))
        self.assertEqual(adjustment.notes, 'Added via hub')

    def test_added_guests_relation(self):
        adjustment = GuestListAdjustment.objects.create(
            booking=self.booking, previous_party_size=2, new_party_size=3, additional_charge=Decimal('45.00'),
        )
        guest = BookingGuest.objects.create(
            booking=self.booking, first_name='Sofia', last_name='Costa', age=25, added_via_adjustment=adjustment,
        )
        self.assertIn(guest, adjustment.added_guests.all())

    def test_str(self):
        adjustment = GuestListAdjustment(previous_party_size=2, new_party_size=3, booking=self.booking)
        self.assertIn('+1 guest', str(adjustment))


class HasCompletedPreviousStayTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property RG', short_title='TESTRG')
        self.guest = Guest.objects.create(first_name='Rita', last_name='Alves', email='rita-rg@example.com')

    def _make_booking(self, days_until_departure, enquiry_status='Booking confirmed', guest=None):
        guest = guest or self.guest
        departure = date.today() + timedelta(days=days_until_departure)
        return Booking.objects.create(
            property=self.property, guest=guest, arrival_date=departure - timedelta(days=7),
            departure_date=departure, is_owner=False, enquiry_status=enquiry_status,
            enquiry_source='Website', adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_no_prior_booking_returns_false(self):
        self.assertFalse(has_completed_previous_stay(self.guest))

    def test_prior_booking_with_future_departure_returns_false(self):
        self._make_booking(days_until_departure=30)
        self.assertFalse(has_completed_previous_stay(self.guest))

    def test_prior_cancelled_booking_with_past_departure_returns_false(self):
        self._make_booking(days_until_departure=-30, enquiry_status='Cancelled by guest')
        self.assertFalse(has_completed_previous_stay(self.guest))

    def test_genuinely_completed_prior_stay_returns_true(self):
        self._make_booking(days_until_departure=-30, enquiry_status='Holiday completed')
        self.assertTrue(has_completed_previous_stay(self.guest))

    def test_exclude_booking_id_excludes_itself(self):
        booking = self._make_booking(days_until_departure=-30, enquiry_status='Holiday completed')
        self.assertFalse(has_completed_previous_stay(self.guest, exclude_booking_id=booking.pk))

    def test_blank_email_guest_never_matches(self):
        blank_guest_1 = Guest.objects.create(last_name='Blank1', email='')
        blank_guest_2 = Guest.objects.create(last_name='Blank2', email='')
        self._make_booking(days_until_departure=-30, enquiry_status='Holiday completed', guest=blank_guest_1)
        self.assertFalse(has_completed_previous_stay(blank_guest_2))


class BookingManageGuestsViewTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property MG', short_title='TESTMG')
        PropertySpec.objects.create(property=self.property, max_guests=4)
        self.guest = Guest.objects.create(first_name='Elena', last_name='Costa', email='elena-mg@example.com')
        self.start = date.today() + timedelta(days=200)
        self.end = self.start + timedelta(days=7)
        Price.objects.create(
            property=self.property, start_date=self.start, end_date=self.end,
            rate=Decimal('100.00'), extra_adult_rate=Decimal('10.00'), extra_child_rate=Decimal('5.00'),
        )
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=1, babies=0, last_updated=timezone.now(),
        )
        BookingGuest.objects.create(booking=self.booking, first_name='Elena', last_name='Costa', age=30, is_lead=True)
        BookingGuest.objects.create(booking=self.booking, first_name='Marco', last_name='Costa', age=32, is_lead=False)
        BookingGuest.objects.create(booking=self.booking, first_name='Ines', last_name='Costa', age=10, is_lead=False)
        self.charge = Charge.objects.create(
            booking=self.booking, basic_rental=Decimal('735.00'), admin=Decimal('40.43'),
            due_at_booking=Decimal('193.86'), due_at_balance=Decimal('581.57'),
            balance_due_date=self.start - timedelta(days=56), currency='EUR',
            gbp_conversion_rate=Decimal('0.8600'),
        )
        self.payment = Payment.objects.create(booking=self.booking, provider='revolut', status='paid')
        self.balance_payment = BalancePayment.objects.create(booking=self.booking, provider='revolut')
        self.url = reverse('bookings:manage_guests', kwargs={'reference': self.booking.reference})
        self.details_url = reverse('bookings:details', kwargs={'reference': self.booking.reference})

    def _post_data(self, first_names, last_names, ages, confirmed=False):
        data = {'first_name[]': first_names, 'last_name[]': last_names, 'age[]': [str(a) for a in ages]}
        if confirmed:
            data['confirmed'] = '1'
        return data

    def test_not_paid_redirects_to_details(self):
        self.payment.status = 'pending'
        self.payment.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertRedirects(response, self.details_url, fetch_redirect_response=False)

    def test_pre_balance_get_prefills_party(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['stage'], 'pre_balance')
        self.assertEqual(len(response.context['rows']), 3)

    def test_pre_balance_removing_a_guest_shows_interstitial_then_saves_and_stays_on_page(self):
        data = self._post_data(['Elena', 'Marco'], ['Costa', 'Costa'], [30, 32])
        response = self.client.post(self.url, data)
        self.assertTrue(response.context['price_changed'])
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.due_at_balance, Decimal('581.57'))  # not yet persisted

        data['confirmed'] = '1'
        response = self.client.post(self.url, data)
        self.assertRedirects(response, f"{self.url}?guests_saved=1", fetch_redirect_response=False)
        self.charge.refresh_from_db()
        self.assertLess(self.charge.due_at_balance, Decimal('581.57'))
        self.assertEqual(self.charge.due_at_booking, Decimal('193.86'))  # deposit stays frozen

    def test_balance_never_goes_negative(self):
        self.charge.due_at_booking = Decimal('100000.00')
        self.charge.save(update_fields=['due_at_booking'])
        response = self.client.post(self.url, self._post_data(['Elena'], ['Costa'], [30], confirmed=True))
        self.assertRedirects(response, f"{self.url}?guests_saved=1", fetch_redirect_response=False)
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.due_at_balance, Decimal('0'))

    def test_price_change_clears_a_stale_revolut_checkout_url(self):
        self.balance_payment.revolut_order_id = 'old-order-id'
        self.balance_payment.revolut_checkout_url = 'https://checkout.revolut.com/old'
        self.balance_payment.save(update_fields=['revolut_order_id', 'revolut_checkout_url'])
        self.client.post(self.url, self._post_data(['Elena'], ['Costa'], [30], confirmed=True))
        self.balance_payment.refresh_from_db()
        self.assertIsNone(self.balance_payment.revolut_checkout_url)

    def test_fully_paid_get_shows_read_only_party_and_add_form(self):
        self.balance_payment.status = 'paid'
        self.balance_payment.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertEqual(response.context['stage'], 'fully_paid')
        self.assertEqual(len(response.context['party']), 3)
        # Party already matches adults+children+babies (2+1+0=3) here, so the add-form only needs
        # its usual single floor row, not a bigger gap-filled seed.
        self.assertEqual(len(response.context['guest_add_rows']), 1)

    def test_fully_paid_get_seeds_blank_rows_for_the_gap_when_no_guests_named_yet(self):
        """The real bug report: adults+children+babies says 3 but no BookingGuest rows exist at
        all (a booking that went straight from deposit to fully-paid without the guest ever
        filling in Guest List) - the add-form must offer 3 blank rows up front, not the usual
        single-row default, so the guest doesn't have to click '+ Add another guest' twice."""
        self.booking.party.all().delete()
        self.balance_payment.status = 'paid'
        self.balance_payment.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertEqual(len(response.context['party']), 0)
        self.assertEqual(len(response.context['guest_add_rows']), 3)

    def test_fully_paid_get_seeds_only_the_remaining_gap_when_some_guests_already_named(self):
        self.booking.party.filter(first_name='Ines').delete()
        self.balance_payment.status = 'paid'
        self.balance_payment.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertEqual(len(response.context['party']), 2)
        self.assertEqual(len(response.context['guest_add_rows']), 1)

    def test_fully_paid_post_redirects_without_writing(self):
        self.balance_payment.status = 'paid'
        self.balance_payment.save(update_fields=['status'])
        response = self.client.post(self.url, self._post_data(['Elena'], ['Costa'], [30]))
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual(self.booking.party.count(), 3)  # untouched


class ArrivalTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property ARR', short_title='TESTARR')
        self.guest = Guest.objects.create(last_name='Guest')
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=date(2026, 9, 1), departure_date=date(2026, 9, 8),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_str_uses_booking_arrival_date(self):
        arrival = Arrival.objects.create(booking=self.booking, meet_greet=False)
        self.assertIn('2026-09-01', str(arrival))


class DepartureTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property DEP', short_title='TESTDEP')
        self.guest = Guest.objects.create(last_name='Guest')
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=date(2026, 9, 1), departure_date=date(2026, 9, 8),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_str_uses_booking_departure_date(self):
        departure = Departure.objects.create(booking=self.booking)
        self.assertIn('2026-09-08', str(departure))

    def test_clean_and_manual_date_default_to_false(self):
        departure = Departure.objects.create(booking=self.booking)
        self.assertFalse(departure.clean)
        self.assertFalse(departure.manual_date)


class BookingManageArrivalDepartureViewTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property AD', short_title='TESTAD')
        PropertySpec.objects.create(property=self.property, max_guests=4)
        self.guest = Guest.objects.create(first_name='Elena', last_name='Costa', email='elena-ad@example.com')
        self.start = date.today() + timedelta(days=200)
        self.end = self.start + timedelta(days=7)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Payment.objects.create(booking=self.booking, provider='revolut', status='paid')
        self.url = reverse('bookings:manage_arrival_departure', kwargs={'reference': self.booking.reference})
        self.details_url = reverse('bookings:details', kwargs={'reference': self.booking.reference})

    def test_not_paid_redirects_to_details(self):
        self.booking.payment.status = 'pending'
        self.booking.payment.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertRedirects(response, self.details_url, fetch_redirect_response=False)

    def test_get_with_no_existing_rows_renders_blank_form(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['arrival_flight_number'], '')
        self.assertEqual(response.context['arrival_method'], 'flight_faro')
        self.assertEqual(response.context['departure_method'], 'flight_faro')
        self.assertFalse(response.context['arrival_hiring_car'])

    def test_post_creates_arrival_and_departure_with_method(self):
        response = self.client.post(self.url, {
            'arrival_method': 'flight_lisbon',
            'arrival_flight_number': 'TP123', 'arrival_time': '14:00', 'arrival_details': 'Renting a car',
            'arrival_hiring_car': 'yes',
            'departure_method': 'flight_faro',
            'departure_flight_number': 'TP456', 'departure_time': '09:00', 'departure_details': 'Early flight',
        })
        self.assertRedirects(response, f"{self.url}?saved=1", fetch_redirect_response=False)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.arrival.method, 'flight_lisbon')
        self.assertEqual(self.booking.arrival.flight_number, 'TP123')
        self.assertEqual(self.booking.arrival.time, time(14, 0))
        self.assertTrue(self.booking.arrival.hiring_car)
        self.assertEqual(self.booking.departure.method, 'flight_faro')
        self.assertEqual(self.booking.departure.flight_number, 'TP456')
        self.assertFalse(self.booking.departure.clean)
        self.assertFalse(self.booking.departure.manual_date)

    def test_post_driving_method_saves_travelling_from(self):
        self.client.post(self.url, {
            'arrival_method': 'driving', 'arrival_travelling_from': 'Lisbon', 'arrival_time': '16:00',
        })
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.arrival.method, 'driving')
        self.assertEqual(self.booking.arrival.travelling_from, 'Lisbon')

    def test_post_invalid_method_falls_back_to_flight_faro(self):
        self.client.post(self.url, {'arrival_method': 'hot-air-balloon'})
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.arrival.method, 'flight_faro')

    def test_hiring_car_defaults_to_no_when_not_posted(self):
        self.client.post(self.url, {'arrival_method': 'flight_faro'})
        self.booking.refresh_from_db()
        self.assertFalse(self.booking.arrival.hiring_car)

    def test_details_truncated_to_140_chars_server_side(self):
        self.client.post(self.url, {'arrival_details': 'x' * 200, 'departure_details': 'y' * 200})
        self.booking.refresh_from_db()
        self.assertEqual(len(self.booking.arrival.details), 140)
        self.assertEqual(len(self.booking.departure.details), 140)

    def test_check_in_no_longer_guest_settable(self):
        Arrival.objects.create(booking=self.booking, self_check_in=True, meet_greet=False)
        self.client.post(self.url, {'arrival_method': 'flight_faro'})
        self.booking.refresh_from_db()
        self.assertTrue(self.booking.arrival.self_check_in)
        self.assertFalse(self.booking.arrival.meet_greet)

    def test_second_post_never_resets_staff_set_ops_fields(self):
        Departure.objects.create(booking=self.booking, clean=True, manual_date=True)
        self.client.post(self.url, {'departure_flight_number': 'TP789'})
        self.booking.refresh_from_db()
        self.assertTrue(self.booking.departure.clean)
        self.assertTrue(self.booking.departure.manual_date)
        self.assertEqual(self.booking.departure.flight_number, 'TP789')

    def test_get_prefills_from_existing_rows(self):
        Arrival.objects.create(
            booking=self.booking, method='driving', travelling_from='Lisbon', hiring_car=True,
            flight_number='TP999', meet_greet=True, self_check_in=False,
        )
        response = self.client.get(self.url)
        self.assertEqual(response.context['arrival_flight_number'], 'TP999')
        self.assertEqual(response.context['arrival_method'], 'driving')
        self.assertEqual(response.context['arrival_travelling_from'], 'Lisbon')
        self.assertTrue(response.context['arrival_hiring_car'])

    def test_departure_dropdown_wording_reads_the_opposite_direction_from_arrival(self):
        response = self.client.get(self.url)
        arrival_labels = dict(response.context['arrival_travel_methods'])
        departure_labels = dict(response.context['departure_travel_methods'])
        self.assertEqual(arrival_labels['flight_faro'], 'Flight to Faro')
        self.assertEqual(departure_labels['flight_faro'], 'Flight from Faro')
        self.assertEqual(arrival_labels['driving'], 'Driving from another location')
        self.assertEqual(departure_labels['driving'], 'Driving to another location')

    def test_valid_flight_numbers_save(self):
        for flight_number in ['TP1234', 'FR123', 'LH 1234', 'BA-12345', 'U21234', 'U2 1234']:
            with self.subTest(flight_number=flight_number):
                response = self.client.post(self.url, {
                    'arrival_method': 'flight_faro', 'arrival_flight_number': flight_number,
                })
                self.assertRedirects(response, f"{self.url}?saved=1", fetch_redirect_response=False)
                self.booking.refresh_from_db()
                self.assertEqual(self.booking.arrival.flight_number, flight_number)

    def test_booking_reference_rejected_as_flight_number(self):
        response = self.client.post(self.url, {
            'arrival_method': 'flight_faro', 'arrival_flight_number': 'DDPP-3QSK',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('arrival_flight_number', response.context['errors'])
        self.assertFalse(hasattr(self.booking, 'arrival'))

    def test_all_digit_prefix_rejected_as_flight_number(self):
        # the widened alphanumeric prefix (for codes like easyJet's "U2") must still require at
        # least one letter somewhere, or a plain numeric reference would pass as "valid"
        response = self.client.post(self.url, {
            'arrival_method': 'flight_faro', 'arrival_flight_number': '1231234',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('arrival_flight_number', response.context['errors'])

    def test_invalid_flight_number_preserves_other_entered_values_on_re_render(self):
        response = self.client.post(self.url, {
            'arrival_method': 'flight_lisbon', 'arrival_flight_number': 'not a flight number',
            'arrival_details': 'Travelling with a toddler',
        })
        self.assertEqual(response.context['arrival_method'], 'flight_lisbon')
        self.assertEqual(response.context['arrival_details'], 'Travelling with a toddler')

    def test_flight_number_not_validated_for_non_flight_methods(self):
        response = self.client.post(self.url, {
            'arrival_method': 'bus', 'arrival_flight_number': 'DDPP-3QSK',
        })
        self.assertRedirects(response, f"{self.url}?saved=1", fetch_redirect_response=False)


class ConfirmationDetailsDisplayTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property CD', short_title='TESTCD')
        self.guest = Guest.objects.create(first_name='Nadia', last_name='Silva', email='nadia-cd@example.com')
        self.start = date.today() + timedelta(days=200)
        self.end = self.start + timedelta(days=7)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.charge = Charge.objects.create(
            booking=self.booking, basic_rental=Decimal('700.00'), admin=Decimal('38.50'),
            due_at_booking=Decimal('738.50'), due_at_balance=Decimal('0'), currency='EUR',
            security=Decimal('200.00'), gbp_conversion_rate=Decimal('0.8600'),
        )
        Payment.objects.create(booking=self.booking, provider='revolut', status='paid')
        self.url = reverse('bookings:confirmation', kwargs={'reference': self.booking.reference})

    def test_due_now_row_says_paid(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Paid')
        self.assertNotContains(response, 'Due now')

    def test_gbp_charge_shows_pound_amounts_with_no_toggle(self):
        self.charge.currency = 'GBP'
        self.charge.save(update_fields=['currency'])
        response = self.client.get(self.url)
        self.assertContains(response, '&pound;')
        self.assertNotContains(response, 'currency-toggle')

    def test_collapsed_booking_says_all_payments_received(self):
        """due_at_balance=0 (set in setUp) means the full total was already required at booking
        time - paid_amount == subtotal from the start, so the message should never claim only a
        deposit was taken."""
        response = self.client.get(self.url)
        text = _normalized_text(response)
        self.assertIn('all payments have been received', text)
        self.assertNotIn('your deposit has been received', text)

    def test_non_returning_guest_sees_the_real_security_amount(self):
        response = self.client.get(self.url)
        self.assertContains(response, '&euro;200.00')
        self.assertNotContains(response, 'Not required for a returning guest')

    def test_returning_guest_sees_the_waiver_message(self):
        Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start - timedelta(days=300),
            departure_date=self.start - timedelta(days=293), is_owner=False,
            enquiry_status='Holiday completed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        response = self.client.get(self.url)
        self.assertContains(response, 'Not required for a returning guest')


class ConfirmationDetailsBalanceDueRowTests(TestCase):
    """Regression coverage for a real bug: the 'Due by <date> €X' row used to key off
    charge.balance_due_date alone, which is set once at booking creation and never cleared - so
    it kept showing a two-stage booking's original balance as still owed even after staff marked
    the BalancePayment paid by hand (see staff/views.py::StaffBookingDetailView's Balance status
    dropdown). The row must track the same 'balance_due' context flag the Pay Balance button
    itself already used (see bookings/utils.py::booking_confirmation_context), so the two can
    never disagree again."""
    def setUp(self):
        self.property = Property.objects.create(title='Test Property CDBD', short_title='TESTCDBD')
        self.guest = Guest.objects.create(first_name='Ines', last_name='Rocha', email='ines-cdbd@example.com')
        self.start = date.today() + timedelta(days=200)
        self.end = self.start + timedelta(days=7)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(
            booking=self.booking, basic_rental=Decimal('700.00'), admin=Decimal('38.50'),
            due_at_booking=Decimal('184.63'), due_at_balance=Decimal('553.87'),
            balance_due_date=self.start - timedelta(days=56), currency='EUR',
            gbp_conversion_rate=Decimal('0.8600'),
        )
        Payment.objects.create(booking=self.booking, provider='revolut', status='paid')
        self.balance_payment = BalancePayment.objects.create(
            booking=self.booking, provider='revolut', status='pending',
        )
        self.url = reverse('bookings:confirmation', kwargs={'reference': self.booking.reference})
        self.hub_url = reverse('bookings:manage_hub', kwargs={'reference': self.booking.reference})

    def test_due_by_row_shows_while_balance_is_still_outstanding(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Due by')
        self.assertContains(response, '553.87')

    def test_due_by_row_hides_once_balance_is_marked_paid(self):
        self.balance_payment.status = 'paid'
        self.balance_payment.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertNotContains(response, 'Due by')

    def test_due_by_row_also_hides_on_the_manage_hub(self):
        """Same partial is shared by the confirmation page and the Manage Hub's Booking tab (see
        manage_hub.html's include) - the fix has to hold on both, not just the one this bug report
        happened to be noticed on."""
        self.balance_payment.status = 'paid'
        self.balance_payment.save(update_fields=['status'])
        response = self.client.get(self.hub_url)
        self.assertNotContains(response, 'Due by')

    def test_paid_row_shows_deposit_only_while_balance_still_outstanding(self):
        # 738.50 (the full total) is expected exactly once here - the Total row always shows it
        # regardless of payment status - so this checks the Paid row isn't ALSO showing it.
        response = self.client.get(self.url)
        self.assertContains(response, '184.63', count=1)
        self.assertContains(response, '738.50', count=1)

    def test_paid_row_shows_full_total_once_balance_is_marked_paid(self):
        # Now 738.50 should appear twice - once for Total, once for Paid.
        self.balance_payment.status = 'paid'
        self.balance_payment.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertContains(response, '738.50', count=2)

    def test_message_says_deposit_received_while_balance_outstanding(self):
        response = self.client.get(self.url)
        text = _normalized_text(response)
        self.assertIn('your deposit has been received', text)
        self.assertNotIn('all payments have been received', text)

    def test_message_says_all_payments_received_once_balance_paid(self):
        self.balance_payment.status = 'paid'
        self.balance_payment.save(update_fields=['status'])
        response = self.client.get(self.url)
        text = _normalized_text(response)
        self.assertIn('all payments have been received', text)
        self.assertNotIn('your deposit has been received', text)

    def test_paid_row_excludes_balance_for_a_cancelled_never_paid_booking(self):
        """balance_due (which gates the Due-by row/Pay Balance button) forces False once
        cancelled, regardless of payment status - Paid must not piggyback on that flag, or a
        cancelled-but-never-paid booking would wrongly show the balance as paid too."""
        self.booking.enquiry_status = 'Cancelled by guest'
        self.booking.save(update_fields=['enquiry_status'])
        response = self.client.get(self.url)
        self.assertContains(response, '184.63', count=1)
        self.assertContains(response, '738.50', count=1)  # Total only, not doubled into Paid


class BookingCancelViewTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property CXL', short_title='TESTCXL')
        PropertySpec.objects.create(property=self.property, max_guests=4)
        self.guest = Guest.objects.create(first_name='Cara', last_name='Costa', email='cara-cxl@example.com')
        self.start = date.today() + timedelta(days=100)
        self.end = self.start + timedelta(days=7)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(
            booking=self.booking, basic_rental=Decimal('700.00'), admin=Decimal('38.50'),
            due_at_booking=Decimal('184.63'), due_at_balance=Decimal('553.87'),
            balance_due_date=self.start - timedelta(days=56), currency='EUR',
            gbp_conversion_rate=Decimal('0.8600'),
        )
        Payment.objects.create(booking=self.booking, provider='revolut', status='paid')
        BalancePayment.objects.create(booking=self.booking, provider='revolut', status='pending')
        self.url = reverse('bookings:manage_cancel', kwargs={'reference': self.booking.reference})
        self.hub_url = reverse('bookings:manage_hub', kwargs={'reference': self.booking.reference})
        self.details_url = reverse('bookings:details', kwargs={'reference': self.booking.reference})

    def test_not_paid_redirects_to_details(self):
        self.booking.payment.status = 'pending'
        self.booking.payment.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertRedirects(response, self.details_url, fetch_redirect_response=False)

    def test_get_shows_confirm_form(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.booking.reference)
        self.assertContains(response, 'reference_confirm')

    def test_platform_sourced_booking_blocks_direct_access(self):
        self.booking.enquiry_source = 'Airbnb'
        self.booking.save(update_fields=['enquiry_source'])
        response = self.client.get(self.url)
        self.assertRedirects(response, self.hub_url, fetch_redirect_response=False)

    def test_already_cancelled_redirects_to_hub(self):
        self.booking.enquiry_status = 'Cancelled by guest'
        self.booking.save(update_fields=['enquiry_status'])
        response = self.client.get(self.url)
        self.assertRedirects(response, self.hub_url, fetch_redirect_response=False)

    def test_arrival_already_passed_blocks_cancel(self):
        self.booking.arrival_date = date.today() - timedelta(days=1)
        self.booking.departure_date = date.today() + timedelta(days=5)
        self.booking.save(update_fields=['arrival_date', 'departure_date'])
        response = self.client.get(self.url)
        self.assertRedirects(response, self.hub_url, fetch_redirect_response=False)

    def test_post_with_wrong_reference_shows_error_and_does_not_cancel(self):
        response = self.client.post(self.url, {'reference_confirm': 'WRONG-REF'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['reference_error'])
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.enquiry_status, 'Booking confirmed')

    def test_post_with_correct_reference_cancels_and_redirects(self):
        response = self.client.post(self.url, {'reference_confirm': self.booking.reference})
        self.assertRedirects(response, f"{self.hub_url}?cancelled=1", fetch_redirect_response=False)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.enquiry_status, 'Cancelled by guest')

    def test_post_reference_match_is_case_insensitive(self):
        response = self.client.post(self.url, {'reference_confirm': self.booking.reference.lower()})
        self.assertRedirects(response, f"{self.hub_url}?cancelled=1", fetch_redirect_response=False)

    def test_double_cancel_post_is_a_no_op(self):
        self.client.post(self.url, {'reference_confirm': self.booking.reference})
        response = self.client.post(self.url, {'reference_confirm': self.booking.reference})
        self.assertRedirects(response, self.hub_url, fetch_redirect_response=False)

    def test_hub_shows_cancel_link_for_direct_booking(self):
        response = self.client.get(self.hub_url)
        self.assertContains(response, self.url)

    def test_hub_hides_cancel_link_for_platform_booking(self):
        self.booking.enquiry_source = 'Booking.com'
        self.booking.save(update_fields=['enquiry_source'])
        response = self.client.get(self.hub_url)
        self.assertNotContains(response, self.url)

    def test_hub_shows_cancelled_banner_and_hides_pay_balance_after_cancelling(self):
        self.booking.enquiry_status = 'Cancelled by guest'
        self.booking.save(update_fields=['enquiry_status'])
        response = self.client.get(self.hub_url)
        self.assertContains(response, 'This booking has been cancelled')
        self.assertFalse(response.context['show_pay_balance'])
        self.assertNotContains(
            response, reverse('bookings:balance_details', kwargs={'reference': self.booking.reference}),
        )


class BookingManageAmenitiesViewTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Test Property AM', short_title='TESTAM')
        self.guest = Guest.objects.create(first_name='Sara', last_name='Nunes', email='sara-am@example.com')
        self.start = date.today() + timedelta(days=200)
        self.end = self.start + timedelta(days=7)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Payment.objects.create(booking=self.booking, provider='revolut', status='paid')
        self.url = reverse('bookings:manage_amenities', kwargs={'reference': self.booking.reference})
        self.details_url = reverse('bookings:details', kwargs={'reference': self.booking.reference})

    def test_not_paid_redirects_to_details(self):
        self.booking.payment.status = 'pending'
        self.booking.payment.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertRedirects(response, self.details_url, fetch_redirect_response=False)

    def test_shows_provided_amenities(self):
        Amenity.objects.create(property=self.property, wifi=True, coffee_machine=True, pool=False)
        response = self.client.get(self.url)
        self.assertContains(response, 'WiFi')
        self.assertContains(response, 'Filter coffee machine')
        self.assertNotContains(response, '<li>Pool</li>')

    def test_no_amenity_row_renders_without_error_or_side_effect(self):
        """A public bearer-link GET must never create data as a side effect - unlike the staff
        detail page's own get_or_create(), a property with no Amenity row yet just shows an
        empty section here."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['amenities'])
        self.assertFalse(Amenity.objects.filter(property=self.property).exists())

    def test_shows_towel_line_items_scaled_by_guest_count(self):
        # setUp's booking has adults=2, no party list yet, so total_guests falls back to 2.
        Amenity.objects.create(property=self.property, beach_towels_per_guest=0)
        response = self.client.get(self.url)
        self.assertContains(response, '<li>2 Hand towels</li>', html=True)
        self.assertContains(response, '<li>2 Bath towels</li>', html=True)

    def test_active_section_is_amenities(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context['active_section'], 'amenities')


class LinkifyFilterTests(TestCase):
    def test_wraps_a_bare_url_in_a_new_tab_link(self):
        result = linkify('See it here: https://maps.app.goo.gl/abc123')
        self.assertIn('<a href="https://maps.app.goo.gl/abc123" target="_blank" rel="noopener noreferrer">', result)
        self.assertIn('See it here:', result)

    def test_links_more_than_one_url(self):
        result = linkify('First https://a.example.com then https://b.example.com')
        self.assertEqual(result.count('<a href='), 2)

    def test_leaves_plain_text_with_no_url_untouched(self):
        self.assertEqual(linkify('Just outside the main gate'), 'Just outside the main gate')

    def test_escapes_surrounding_text(self):
        result = linkify('Tom & Jerry <script>')
        self.assertIn('Tom &amp; Jerry &lt;script&gt;', result)

    def test_empty_value_returned_as_is(self):
        self.assertEqual(linkify(''), '')
        self.assertIsNone(linkify(None))


class BookingManageLocationViewTests(TestCase):
    def setUp(self):
        self.location = Location.objects.create(
            title='Test Location Manage', street='1 Manage Street', zip_code='8000-000', city='Faro',
            coordinates='37.0,-7.9', map_link='https://maps.example.com/manage',
            directions='Follow the coast road past the marina.',
            nearest_supermarket='Intermarché, 500m away',
        )
        self.property = Property.objects.create(
            title='Test Property LOC', short_title='TESTLOC', location=self.location,
        )
        self.guest = Guest.objects.create(first_name='Tiago', last_name='Alves', email='tiago-loc@example.com')
        self.start = date.today() + timedelta(days=200)
        self.end = self.start + timedelta(days=7)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Payment.objects.create(booking=self.booking, provider='revolut', status='paid')
        self.url = reverse('bookings:manage_location', kwargs={'reference': self.booking.reference})
        self.details_url = reverse('bookings:details', kwargs={'reference': self.booking.reference})

    def test_not_paid_redirects_to_details(self):
        self.booking.payment.status = 'pending'
        self.booking.payment.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertRedirects(response, self.details_url, fetch_redirect_response=False)

    def test_shows_address_and_directions(self):
        response = self.client.get(self.url)
        self.assertContains(response, '1 Manage Street')
        self.assertContains(response, 'Follow the coast road past the marina.')
        self.assertContains(response, 'Intermarché, 500m away')

    def test_shows_house_rules_when_present(self):
        LocationRules.objects.create(
            location=self.location, quiet_hours_start=time(22, 0), quiet_hours_end=time(8, 0),
            pool_hours_start=time(9, 0), pool_hours_end=time(20, 0), pool_rules='No diving.',
        )
        response = self.client.get(self.url)
        self.assertContains(response, '22:00')
        self.assertContains(response, 'No diving.')

    def test_no_location_renders_fallback_message(self):
        self.property.location = None
        self.property.save(update_fields=['location'])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "aren't available yet")

    def test_active_section_is_location(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context['active_section'], 'location')

    def test_a_raw_url_in_good_to_know_text_becomes_a_clickable_new_tab_link(self):
        self.location.nearest_corner_shop = 'See it here: https://maps.app.goo.gl/xyz789'
        self.location.save(update_fields=['nearest_corner_shop'])
        response = self.client.get(self.url)
        self.assertContains(
            response,
            '<a href="https://maps.app.goo.gl/xyz789" target="_blank" rel="noopener noreferrer">',
        )


class BookingManageFAQViewTests(TestCase):
    def setUp(self):
        self.location = Location.objects.create(
            title='Test Location FAQ', street='1 FAQ Street', zip_code='8000-000', city='Faro',
            coordinates='37.0,-7.9', map_link='https://maps.example.com/faq',
        )
        self.other_location = Location.objects.create(
            title='Other Location FAQ', street='2 FAQ Street', zip_code='8000-001', city='Faro',
            coordinates='37.1,-7.8', map_link='https://maps.example.com/faq-other',
        )
        self.property = Property.objects.create(
            title='Test Property FAQ', short_title='TESTFAQ', location=self.location,
        )
        self.guest = Guest.objects.create(first_name='Rita', last_name='Sousa', email='rita-faq@example.com')
        self.start = date.today() + timedelta(days=200)
        self.end = self.start + timedelta(days=7)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Payment.objects.create(booking=self.booking, provider='revolut', status='paid')
        self.url = reverse('bookings:manage_faq', kwargs={'reference': self.booking.reference})
        self.details_url = reverse('bookings:details', kwargs={'reference': self.booking.reference})

    def test_not_paid_redirects_to_details(self):
        self.booking.payment.status = 'pending'
        self.booking.payment.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertRedirects(response, self.details_url, fetch_redirect_response=False)

    def test_shows_a_universal_faq(self):
        FAQ.objects.create(question='Can I have a late check-out?', answer='Yes, ask us.', order=0)
        response = self.client.get(self.url)
        self.assertContains(response, 'Can I have a late check-out?')

    def test_shows_a_faq_scoped_to_this_booking_own_location(self):
        FAQ.objects.create(question='Is there parking?', answer='Yes, private parking.', location=self.location)
        response = self.client.get(self.url)
        self.assertContains(response, 'Is there parking?')

    def test_hides_a_faq_scoped_to_a_different_location(self):
        FAQ.objects.create(question='Is there parking?', answer='Street parking only.', location=self.other_location)
        response = self.client.get(self.url)
        self.assertNotContains(response, 'Is there parking?')

    def test_property_with_no_location_only_sees_universal_faqs(self):
        self.property.location = None
        self.property.save(update_fields=['location'])
        FAQ.objects.create(question='Universal question', answer='Answer.', order=0)
        FAQ.objects.create(question='Location-specific question', answer='Answer.', location=self.location)
        response = self.client.get(self.url)
        self.assertContains(response, 'Universal question')
        self.assertNotContains(response, 'Location-specific question')

    def test_no_faqs_renders_fallback_message(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No frequently asked questions yet')

    def test_active_section_is_faq(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context['active_section'], 'faq')


def _ics_feed(events):
    """events: list of (uid, start_date, end_date) tuples -> minimal valid .ics text, matching the
    shape a real Airbnb/Booking.com/Vrbo reservations feed has (one all-day VEVENT per booking)."""
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Test//EN']
    for uid, start, end in events:
        lines += [
            'BEGIN:VEVENT',
            f'UID:{uid}',
            f'DTSTART;VALUE=DATE:{start.strftime("%Y%m%d")}',
            f'DTEND;VALUE=DATE:{end.strftime("%Y%m%d")}',
            'SUMMARY:Reserved',
            'END:VEVENT',
        ]
    lines.append('END:VCALENDAR')
    return '\r\n'.join(lines)


class SyncIcalLinkTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Sync Test Property', short_title='SYNCTEST')
        self.link = iCalLink.objects.create(
            property=self.property, ical_source='airbnb', ical_url='https://example.com/feed.ics',
        )
        self.start = date.today() + timedelta(days=100)
        self.end = self.start + timedelta(days=7)

    def _other_booking(self, start, end, enquiry_source='Website', enquiry_status='Booking confirmed'):
        guest = Guest.objects.create(last_name='Other Guest')
        return Booking.objects.create(
            property=self.property, guest=guest, arrival_date=start, departure_date=end,
            is_owner=False, enquiry_status=enquiry_status, enquiry_source=enquiry_source,
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_creates_new_booking_from_feed_event(self):
        summary = sync_ical_link(self.link, _ics_feed([('uid-1', self.start, self.end)]))
        self.assertEqual(summary['created'], 1)
        booking = Booking.objects.get(ical_uid='uid-1')
        self.assertEqual(booking.property, self.property)
        self.assertEqual(booking.arrival_date, self.start)
        self.assertEqual(booking.departure_date, self.end)
        self.assertEqual(booking.enquiry_source, 'Airbnb')
        self.assertEqual(booking.enquiry_status, 'Booking confirmed')
        self.assertEqual(booking.guest.last_name, 'Airbnb Guest')
        self.assertEqual(len(summary['events']), 1)
        self.assertEqual(summary['events'][0]['result'], 'created')
        self.assertEqual(summary['events'][0]['booking'], booking)

    def test_unchanged_event_reports_that_result(self):
        existing = self._other_booking(self.start, self.end, enquiry_source='Airbnb')
        existing.ical_uid = 'uid-1'
        existing.save(update_fields=['ical_uid'])

        summary = sync_ical_link(self.link, _ics_feed([('uid-1', self.start, self.end)]))
        self.assertEqual(summary['events'], [
            {'uid': 'uid-1', 'start': self.start, 'end': self.end, 'result': 'unchanged', 'booking': existing},
        ])

    def test_manual_override_reports_that_result_instead_of_silently_doing_nothing(self):
        existing = self._other_booking(self.start, self.end, enquiry_source='Airbnb')
        existing.ical_uid = 'uid-1'
        existing.manual_override = True
        existing.save(update_fields=['ical_uid', 'manual_override'])

        new_start, new_end = self.start + timedelta(days=1), self.end + timedelta(days=1)
        summary = sync_ical_link(self.link, _ics_feed([('uid-1', new_start, new_end)]))
        self.assertEqual(len(summary['events']), 1)
        self.assertEqual(summary['events'][0]['result'], 'manual_override')

    def test_updates_existing_matched_bookings_dates(self):
        existing = self._other_booking(self.start, self.end, enquiry_source='Airbnb')
        existing.ical_uid = 'uid-1'
        existing.save(update_fields=['ical_uid'])

        new_start, new_end = self.start + timedelta(days=1), self.end + timedelta(days=1)
        summary = sync_ical_link(self.link, _ics_feed([('uid-1', new_start, new_end)]))
        self.assertEqual(summary['updated'], 1)
        self.assertEqual(summary['created'], 0)
        existing.refresh_from_db()
        self.assertEqual(existing.arrival_date, new_start)
        self.assertEqual(existing.departure_date, new_end)

    def test_manual_override_blocks_date_update(self):
        existing = self._other_booking(self.start, self.end, enquiry_source='Airbnb')
        existing.ical_uid = 'uid-1'
        existing.manual_override = True
        existing.save(update_fields=['ical_uid', 'manual_override'])

        new_start, new_end = self.start + timedelta(days=1), self.end + timedelta(days=1)
        sync_ical_link(self.link, _ics_feed([('uid-1', new_start, new_end)]))
        existing.refresh_from_db()
        self.assertEqual(existing.arrival_date, self.start)
        self.assertEqual(existing.departure_date, self.end)

    def test_skips_new_event_that_overlaps_existing_booking(self):
        self._other_booking(self.start, self.end)  # a direct booking already occupies these dates
        summary = sync_ical_link(self.link, _ics_feed([('uid-1', self.start, self.end)]))
        self.assertEqual(summary['created'], 0)
        self.assertEqual(len(summary['conflicts']), 1)
        self.assertEqual(summary['events'][0]['result'], 'conflict')
        self.assertIsNone(summary['events'][0]['booking'])
        self.assertFalse(Booking.objects.filter(ical_uid='uid-1').exists())

    def test_skips_date_update_that_would_create_overlap(self):
        existing = self._other_booking(self.start, self.end, enquiry_source='Airbnb')
        existing.ical_uid = 'uid-1'
        existing.save(update_fields=['ical_uid'])
        conflict_start, conflict_end = self.start + timedelta(days=100), self.end + timedelta(days=100)
        self._other_booking(conflict_start, conflict_end)  # occupies the dates uid-1 is about to move to

        summary = sync_ical_link(self.link, _ics_feed([('uid-1', conflict_start, conflict_end)]))
        self.assertEqual(summary['updated'], 0)
        self.assertEqual(len(summary['conflicts']), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.arrival_date, self.start)  # untouched

    def test_cancels_booking_missing_from_latest_feed(self):
        existing = self._other_booking(self.start, self.end, enquiry_source='Airbnb')
        existing.ical_uid = 'uid-1'
        existing.save(update_fields=['ical_uid'])

        summary = sync_ical_link(self.link, _ics_feed([]))  # empty feed - uid-1 has disappeared
        self.assertEqual(summary['cancelled'], 1)
        self.assertEqual(summary['cancelled_bookings'], [existing])
        existing.refresh_from_db()
        self.assertEqual(existing.enquiry_status, 'Cancelled by platform')

    def test_does_not_cancel_a_booking_that_has_already_departed(self):
        past_start = date.today() - timedelta(days=30)
        past_end = date.today() - timedelta(days=23)
        existing = self._other_booking(past_start, past_end, enquiry_source='Airbnb')
        existing.ical_uid = 'uid-1'
        existing.save(update_fields=['ical_uid'])

        summary = sync_ical_link(self.link, _ics_feed([]))
        self.assertEqual(summary['cancelled'], 0)
        existing.refresh_from_db()
        self.assertEqual(existing.enquiry_status, 'Booking confirmed')

    def test_resurrects_a_previously_cancelled_booking_that_reappears(self):
        existing = self._other_booking(
            self.start, self.end, enquiry_source='Airbnb', enquiry_status='Cancelled by platform',
        )
        existing.ical_uid = 'uid-1'
        existing.save(update_fields=['ical_uid'])

        summary = sync_ical_link(self.link, _ics_feed([('uid-1', self.start, self.end)]))
        self.assertEqual(summary['resurrected'], 1)
        existing.refresh_from_db()
        self.assertEqual(existing.enquiry_status, 'Booking confirmed')

    def test_never_touches_a_manually_entered_platform_booking_without_ical_uid(self):
        manual_booking = self._other_booking(self.start, self.end, enquiry_source='Airbnb')
        self.assertIsNone(manual_booking.ical_uid)

        summary = sync_ical_link(self.link, _ics_feed([]))
        self.assertEqual(summary['cancelled'], 0)
        manual_booking.refresh_from_db()
        self.assertEqual(manual_booking.enquiry_status, 'Booking confirmed')

    def test_unrecognised_ical_source_is_a_noop(self):
        self.link.ical_source = None
        self.link.save(update_fields=['ical_source'])
        summary = sync_ical_link(self.link, _ics_feed([('uid-1', self.start, self.end)]))
        self.assertEqual(summary, {
            'created': 0, 'updated': 0, 'resurrected': 0, 'cancelled': 0, 'conflicts': [],
            'events': [], 'cancelled_bookings': [],
        })
        self.assertFalse(Booking.objects.filter(ical_uid='uid-1').exists())

    def test_updates_last_synced(self):
        self.assertIsNone(self.link.last_synced)
        sync_ical_link(self.link, _ics_feed([]))
        self.link.refresh_from_db()
        self.assertIsNotNone(self.link.last_synced)
