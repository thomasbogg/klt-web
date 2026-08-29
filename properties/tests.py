import importlib
from datetime import date, timedelta
from decimal import Decimal

from django.apps import apps
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bookings.models import Booking
from guests.models import Guest
from properties.models import (
    Amenity, Location, ManagementCompany, Owner, Price, Property, PropertyOwnership, PropertySpec,
)
from properties.utils import get_stay_total_price


def make_owner(name, email):
    """Owner's 7 BooleanFields have no model default - every test that needs an Owner has to
    supply all of them explicitly, so this is shared across test classes below."""
    return Owner.objects.create(
        name=name, email=email,
        default_clean=False, default_meet_greet=False, takes_euros=True, takes_pounds=False,
        cleans_are_invoiced=False, rental_commissions_are_invoiced=False, is_paid_regularly=False,
    )


class ReserveOwnPendingBookingTests(TestCase):
    """Covers the 'wrong currency, clicked back' scenario: a guest's own not-yet-paid hold
    shouldn't be a dead end - see bookings/utils.py::cancel_booking_hold()/reservation_retry_url()."""

    def setUp(self):
        self.location = Location.objects.create(
            title='Test Location', street='Test St', zip_code='0000',
            city='Test City', coordinates='37.0,-8.0', map_link='https://example.com',
        )
        self.management_company = ManagementCompany.objects.create(name='Test Management Co')
        self.property = Property.objects.create(
            title=f'{self.location} - RETRYTEST', short_title='RETRYTEST',
            location=self.location, booking_company=self.management_company,
        )
        PropertySpec.objects.create(property=self.property, max_guests=4, bedrooms=1, bathrooms=1, minimum_nights=1)
        self.start = date.today() + timedelta(days=330)
        self.end = self.start + timedelta(days=5)
        Price.objects.create(
            property=self.property,
            start_date=date.today(), end_date=self.end + timedelta(days=30), rate=100,
        )
        self.reserve_url = f'/properties/{self.location.slug}/retrytest/reserve/'
        self.query = {
            'start': self.start.strftime('%d/%m/%Y'),
            'end': self.end.strftime('%d/%m/%Y'),
            'guests': '2 adults,0 children,0 infants',
        }

    def submit_reservation(self, currency='EUR'):
        return self.client.post(self.reserve_url, {
            **self.query, 'currency': currency,
            'first_name': 'Test', 'last_name': 'Guest', 'email': 'retrytest@example.com', 'phone': '',
            'country': 'GB',
        })

    def test_full_wrong_currency_recovery_flow(self):
        # Fresh reserve page - available, form present
        response = self.client.get(self.reserve_url, self.query)
        self.assertContains(response, 'Proceed to Reservation')

        # Submit with the "wrong" currency by mistake
        response = self.submit_reservation(currency='EUR')
        self.assertEqual(response.status_code, 302)
        reference = response.url.rstrip('/').split('/')[-2]
        booking = Booking.objects.get(reference=reference)
        self.assertEqual(booking.enquiry_status, 'Awaiting payment')
        self.assertEqual(self.client.session.get('pending_booking_reference'), reference)

        # Simulate clicking browser back - reserve page, same session
        response = self.client.get(self.reserve_url, self.query)
        content = response.content.decode()
        self.assertIn('already started a reservation', content)
        self.assertIn(reference, content)
        self.assertNotIn('is no longer available', content)

        # A second submission attempt should fail - own hold still blocks it
        second_attempt = self.submit_reservation(currency='GBP')
        self.assertEqual(second_attempt.status_code, 200)  # re-rendered with a form error, not redirected

        # Cancel and start over
        cancel_response = self.client.post(f'/bookings/{reference}/pay/cancel/')
        self.assertEqual(cancel_response.status_code, 302)
        self.assertIn(self.reserve_url, cancel_response.url)
        booking.refresh_from_db()
        self.assertEqual(booking.enquiry_status, 'Cancelled by guest')

        # Now the reserve page should be genuinely available again, and a corrected resubmission works
        response = self.client.get(self.reserve_url, self.query)
        self.assertContains(response, 'Proceed to Reservation')

        retry_response = self.submit_reservation(currency='GBP')
        self.assertEqual(retry_response.status_code, 302)
        new_reference = retry_response.url.rstrip('/').split('/')[-2]
        self.assertNotEqual(new_reference, reference)
        new_booking = Booking.objects.get(reference=new_reference)
        self.assertEqual(new_booking.charges.currency, 'GBP')

    def test_cancel_does_not_touch_a_confirmed_booking(self):
        response = self.submit_reservation()
        reference = response.url.rstrip('/').split('/')[-2]
        booking = Booking.objects.get(reference=reference)
        booking.enquiry_status = 'Booking confirmed'
        booking.save()

        self.client.post(f'/bookings/{reference}/pay/cancel/')
        booking.refresh_from_db()
        self.assertEqual(booking.enquiry_status, 'Booking confirmed')

    def test_cancel_requires_matching_session_not_just_a_known_reference(self):
        from django.test import Client

        response = self.submit_reservation()
        reference = response.url.rstrip('/').split('/')[-2]
        booking = Booking.objects.get(reference=reference)

        stranger = Client()  # a different browser session, never created this booking
        stranger.post(f'/bookings/{reference}/pay/cancel/')
        booking.refresh_from_db()
        self.assertEqual(booking.enquiry_status, 'Awaiting payment')


class PropertyCalendarExportViewTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Export Test Property', short_title='EXPORTTEST')
        self.guest = Guest.objects.create(first_name='Zbigniew', last_name='Guest', email='export-test@example.com')
        self.start = date.today() + timedelta(days=100)
        self.end = self.start + timedelta(days=7)
        self.website_booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.url = reverse('properties:calendar_export', kwargs={'token': self.property.ical_export_token})

    def test_wrong_token_404s(self):
        response = self.client.get(reverse('properties:calendar_export', kwargs={'token': 'not-a-real-token'}))
        self.assertEqual(response.status_code, 404)

    def test_export_includes_our_own_booking_with_no_guest_pii(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/calendar; charset=utf-8')
        content = response.content.decode()
        self.assertIn(f"UID:{self.website_booking.reference}@algarvebeachapartments.com", content)
        self.assertIn('SUMMARY:Reserved', content)
        self.assertNotIn(self.guest.first_name, content)

    def test_export_excludes_platform_sourced_bookings(self):
        Booking.objects.create(
            property=self.property, guest=self.guest,
            arrival_date=self.start + timedelta(days=100), departure_date=self.end + timedelta(days=100),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Airbnb',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        response = self.client.get(self.url)
        content = response.content.decode()
        self.assertEqual(content.count('BEGIN:VEVENT'), 1)

    def test_export_excludes_non_holding_bookings(self):
        Booking.objects.create(
            property=self.property, guest=self.guest,
            arrival_date=self.start + timedelta(days=200), departure_date=self.end + timedelta(days=200),
            is_owner=False, enquiry_status='Cancelled by guest', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        response = self.client.get(self.url)
        content = response.content.decode()
        self.assertEqual(content.count('BEGIN:VEVENT'), 1)


class PropertyOwnershipTests(TestCase):
    """Model-level: NULL-safe overlap validation and the record_initial_ownership()/
    record_handover() helpers - see properties/models.py::PropertyOwnership."""

    def setUp(self):
        self.property = Property.objects.create(title='Ownership Test Property', short_title='OWNERSHIPTEST')
        self.owner_a = make_owner('Ownership Owner A', 'ownership-a@example.com')
        self.owner_b = make_owner('Ownership Owner B', 'ownership-b@example.com')

    def test_clean_rejects_start_date_after_end_date(self):
        row = PropertyOwnership(
            property=self.property, owner=self.owner_a,
            start_date=date(2024, 6, 1), end_date=date(2024, 1, 1),
        )
        with self.assertRaises(ValidationError):
            row.full_clean()

    def test_overlapping_bounded_ranges_that_touch_is_an_overlap(self):
        PropertyOwnership.objects.create(
            property=self.property, owner=self.owner_a,
            start_date=date(2024, 1, 1), end_date=date(2024, 6, 30),
        )
        conflicting = PropertyOwnership(
            property=self.property, owner=self.owner_b,
            start_date=date(2024, 6, 30), end_date=date(2024, 12, 31),
        )
        with self.assertRaises(ValidationError):
            conflicting.full_clean()

    def test_overlapping_bounded_ranges_adjacent_by_one_day_do_not_overlap(self):
        PropertyOwnership.objects.create(
            property=self.property, owner=self.owner_a,
            start_date=date(2024, 1, 1), end_date=date(2024, 6, 30),
        )
        adjacent = PropertyOwnership(
            property=self.property, owner=self.owner_b,
            start_date=date(2024, 7, 1), end_date=None,
        )
        adjacent.full_clean()  # should not raise

    def test_null_start_date_overlaps_any_earlier_candidate(self):
        PropertyOwnership.objects.create(
            property=self.property, owner=self.owner_a, start_date=None, end_date=date(2024, 6, 30),
        )
        candidate = PropertyOwnership(
            property=self.property, owner=self.owner_b,
            start_date=date(2020, 1, 1), end_date=date(2020, 6, 1),
        )
        with self.assertRaises(ValidationError):
            candidate.full_clean()

    def test_null_end_date_overlaps_any_later_candidate(self):
        PropertyOwnership.objects.create(
            property=self.property, owner=self.owner_a, start_date=date(2024, 1, 1), end_date=None,
        )
        candidate = PropertyOwnership(
            property=self.property, owner=self.owner_b,
            start_date=date(2030, 1, 1), end_date=date(2030, 6, 1),
        )
        with self.assertRaises(ValidationError):
            candidate.full_clean()

    def test_two_fully_open_rows_for_same_property_overlap(self):
        PropertyOwnership.objects.create(
            property=self.property, owner=self.owner_a, start_date=None, end_date=None,
        )
        candidate = PropertyOwnership(property=self.property, owner=self.owner_b, start_date=None, end_date=None)
        with self.assertRaises(ValidationError):
            candidate.full_clean()

    def test_exclude_pk_excludes_self_when_editing_in_place(self):
        row = PropertyOwnership.objects.create(
            property=self.property, owner=self.owner_a, start_date=date(2024, 1, 1), end_date=None,
        )
        row.end_date = date(2024, 12, 31)
        row.full_clean()  # should not raise - exclude_pk keeps this row from conflicting with itself

    def test_clean_allows_non_overlapping_rows_for_different_properties(self):
        other_property = Property.objects.create(title='Other Ownership Property', short_title='OTHEROWNERSHIP')
        PropertyOwnership.objects.create(
            property=self.property, owner=self.owner_a, start_date=None, end_date=None,
        )
        candidate = PropertyOwnership(property=other_property, owner=self.owner_a, start_date=None, end_date=None)
        candidate.full_clean()  # should not raise - different property, no shared scope

    def test_record_initial_ownership_creates_null_start_and_end_row(self):
        row = PropertyOwnership.record_initial_ownership(self.property, self.owner_a)
        self.assertIsNone(row.start_date)
        self.assertIsNone(row.end_date)
        self.assertEqual(row.owner, self.owner_a)

    def test_record_handover_on_property_with_no_current_owner_skips_close_out(self):
        new_row = PropertyOwnership.record_handover(self.property, self.owner_a, date(2024, 1, 1))
        self.assertEqual(PropertyOwnership.objects.filter(property=self.property).count(), 1)
        self.assertEqual(new_row.start_date, date(2024, 1, 1))
        self.assertIsNone(new_row.end_date)

    def test_record_handover_closes_prior_open_row_the_day_before_effective_date(self):
        PropertyOwnership.record_initial_ownership(self.property, self.owner_a)
        PropertyOwnership.record_handover(self.property, self.owner_b, date(2024, 7, 1))
        prior = PropertyOwnership.objects.get(owner=self.owner_a)
        self.assertEqual(prior.end_date, date(2024, 6, 30))

    def test_record_handover_creates_new_open_ended_row_for_new_owner(self):
        PropertyOwnership.record_initial_ownership(self.property, self.owner_a)
        new_row = PropertyOwnership.record_handover(self.property, self.owner_b, date(2024, 7, 1))
        self.assertEqual(new_row.owner, self.owner_b)
        self.assertEqual(new_row.start_date, date(2024, 7, 1))
        self.assertIsNone(new_row.end_date)

    def test_record_handover_syncs_property_owner(self):
        PropertyOwnership.record_initial_ownership(self.property, self.owner_a)
        PropertyOwnership.record_handover(self.property, self.owner_b, date(2024, 7, 1))
        self.property.refresh_from_db()
        self.assertEqual(self.property.owner, self.owner_b)

    def test_record_handover_rejects_effective_date_not_after_current_owners_start_date(self):
        PropertyOwnership.record_handover(self.property, self.owner_a, date(2024, 1, 1))
        with self.assertRaises(ValidationError):
            PropertyOwnership.record_handover(self.property, self.owner_b, date(2024, 1, 1))

    def test_record_handover_rejects_handover_to_the_same_owner(self):
        PropertyOwnership.record_initial_ownership(self.property, self.owner_a)
        with self.assertRaises(ValidationError):
            PropertyOwnership.record_handover(self.property, self.owner_a, date(2024, 7, 1))


class PropertyOwnershipBackfillMigrationTests(TestCase):
    """No existing precedent in this codebase for testing a RunPython data migration either way -
    importing the function directly and calling it against the real model classes (rather than
    apps.get_model's frozen historical models) is a pragmatic substitute for a small app like
    this, since the backfill only does plain field assignment that works identically either way."""

    def setUp(self):
        migration_module = importlib.import_module('properties.migrations.0020_propertyownership')
        self.backfill = migration_module.backfill_property_ownership

    def test_backfill_creates_one_row_per_owned_property(self):
        owner = make_owner('Backfill Owner', 'backfill-owner@example.com')
        owned = Property.objects.create(title='Backfill Owned', short_title='BACKFILLOWNED', owner=owner)
        self.backfill(apps, None)
        self.assertEqual(PropertyOwnership.objects.filter(property=owned, owner=owner).count(), 1)

    def test_backfill_skips_properties_with_no_owner(self):
        ownerless = Property.objects.create(title='Backfill Ownerless', short_title='BACKFILLOWNERLESS')
        self.backfill(apps, None)
        self.assertFalse(PropertyOwnership.objects.filter(property=ownerless).exists())

    def test_backfill_rows_have_null_start_and_end_date(self):
        owner = make_owner('Backfill Owner 2', 'backfill-owner-2@example.com')
        owned = Property.objects.create(title='Backfill Owned 2', short_title='BACKFILLOWNED2', owner=owner)
        self.backfill(apps, None)
        row = PropertyOwnership.objects.get(property=owned)
        self.assertIsNone(row.start_date)
        self.assertIsNone(row.end_date)


class GetStayTotalPriceTests(TestCase):
    """get_stay_total_price() returns {'basic_total', 'discount_total', 'extra_guest_total'} - see
    bookings.models.Charge.basic_rental/discount_total/extra_guest_total, which store these three
    components separately rather than one combined final total (2026-08-25)."""

    def setUp(self):
        self.property = Property.objects.create(title='Pricing Property', short_title='PRICEPROP')
        # Far enough out that last-minute-discount tests can control days_to_arrival precisely
        # without also (accidentally) landing inside another test's window.
        self.far_future = date.today() + timedelta(days=300)

    def _make_price(self, nights, start=None, **overrides):
        start = start or self.far_future
        end = start + timedelta(days=nights)
        Price.objects.create(
            property=self.property, start_date=start, end_date=end,
            rate=Decimal('100.00'), extra_adult_rate=Decimal('10.00'), extra_child_rate=Decimal('5.00'),
            **overrides,
        )
        return start, end

    def test_no_discount_no_extra_guests(self):
        start, end = self._make_price(3)
        pricing = get_stay_total_price(self.property, start, end, {'adults': 2})
        self.assertEqual(pricing['basic_total'], Decimal('300.00'))
        self.assertEqual(pricing['discount_total'], Decimal('0'))
        self.assertEqual(pricing['extra_guest_total'], Decimal('0'))

    def test_weekly_discount(self):
        start, end = self._make_price(7, weekly_discount_percent=Decimal('10.00'))
        pricing = get_stay_total_price(self.property, start, end, {'adults': 2})
        self.assertEqual(pricing['basic_total'], Decimal('700.00'))
        self.assertEqual(pricing['discount_total'], Decimal('70.00'))  # 7 * 100 * 10%

    def test_monthly_discount_overrides_weekly(self):
        start, end = self._make_price(
            28, weekly_discount_percent=Decimal('10.00'), monthly_discount_percent=Decimal('20.00'),
        )
        pricing = get_stay_total_price(self.property, start, end, {'adults': 2})
        self.assertEqual(pricing['basic_total'], Decimal('2800.00'))
        self.assertEqual(pricing['discount_total'], Decimal('560.00'))  # 28 * 100 * 20%, not 10%

    def test_last_minute_discount_stacks_on_top_of_weekly(self):
        start, end = self._make_price(
            7, start=date.today(),
            weekly_discount_percent=Decimal('10.00'), last_minute_discount_percent=Decimal('5.00'),
            last_minute_discount_days=7,
        )
        pricing = get_stay_total_price(self.property, start, end, {'adults': 2})
        self.assertEqual(pricing['discount_total'], Decimal('105.00'))  # 7 * 100 * (10% + 5%)

    def test_extra_guests_only(self):
        start, end = self._make_price(3)
        pricing = get_stay_total_price(self.property, start, end, {'adults': 3, 'children': 1})
        self.assertEqual(pricing['basic_total'], Decimal('300.00'))
        self.assertEqual(pricing['discount_total'], Decimal('0'))
        # 3 nights * (1 extra adult @ 10 + 1 child @ 5)
        self.assertEqual(pricing['extra_guest_total'], Decimal('45.00'))

    def test_combined_discount_and_extra_guests(self):
        start, end = self._make_price(7, weekly_discount_percent=Decimal('10.00'))
        pricing = get_stay_total_price(self.property, start, end, {'adults': 3, 'children': 1})
        self.assertEqual(pricing['basic_total'], Decimal('700.00'))
        self.assertEqual(pricing['discount_total'], Decimal('70.00'))
        self.assertEqual(pricing['extra_guest_total'], Decimal('105.00'))  # 7 * (10 + 5)

    def test_unpriceable_stay_returns_none(self):
        start = self.far_future
        end = start + timedelta(days=3)
        pricing = get_stay_total_price(self.property, start, end, {'adults': 2})
        self.assertIsNone(pricing)


class AmenityTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Towel Property', short_title='TOWELPROP')

    def test_towel_summary_leads_with_beach_towels_when_provided(self):
        amenities = Amenity.objects.create(property=self.property)
        self.assertEqual(amenities.towel_summary(), 'Bath and beach towels')

    def test_towel_summary_falls_back_to_hand_and_bath_without_beach_towels(self):
        amenities = Amenity.objects.create(property=self.property, beach_towels_per_guest=0)
        self.assertEqual(amenities.towel_summary(), 'Hand and bath towels')

    def test_towel_summary_ignores_count_values_only_presence_matters(self):
        amenities = Amenity.objects.create(
            property=self.property, hand_towels_per_guest=5, bath_towels_per_guest=3, beach_towels_per_guest=2,
        )
        self.assertEqual(amenities.towel_summary(), 'Bath and beach towels')

    def test_towel_summary_none_when_nothing_provided(self):
        amenities = Amenity.objects.create(
            property=self.property, hand_towels_per_guest=0, bath_towels_per_guest=0, beach_towels_per_guest=0,
        )
        self.assertIsNone(amenities.towel_summary())

    def test_towel_line_items_scales_by_guest_count(self):
        amenities = Amenity.objects.create(property=self.property)
        self.assertEqual(
            amenities.towel_line_items(2),
            [(2, 'Hand towels'), (2, 'Bath towels'), (2, 'Beach towels')],
        )

    def test_towel_line_items_singular_noun_for_a_total_of_one(self):
        amenities = Amenity.objects.create(property=self.property)
        self.assertEqual(
            amenities.towel_line_items(1),
            [(1, 'Hand towel'), (1, 'Bath towel'), (1, 'Beach towel')],
        )

    def test_towel_line_items_omits_zero_count_type(self):
        amenities = Amenity.objects.create(property=self.property, beach_towels_per_guest=0)
        self.assertEqual(amenities.towel_line_items(3), [(3, 'Hand towels'), (3, 'Bath towels')])

    def test_towel_line_items_empty_when_nothing_provided(self):
        amenities = Amenity.objects.create(
            property=self.property, hand_towels_per_guest=0, bath_towels_per_guest=0, beach_towels_per_guest=0,
        )
        self.assertEqual(amenities.towel_line_items(4), [])

    def test_full_feature_list_includes_only_true_fields_in_declared_order(self):
        amenities = Amenity.objects.create(property=self.property, wifi=True, pool=True, coffee_machine=True)
        # wifi and pool are both True by model default too, but only the fields explicitly set
        # True here (plus their defaults) should show, and 'WiFi' - first in GUEST_FEATURE_LIST -
        # must come before 'Pool' regardless of the order they were set in this call.
        features = amenities.full_feature_list()
        self.assertIn('WiFi', features)
        self.assertIn('Filter coffee machine', features)
        self.assertLess(features.index('WiFi'), features.index('Filter coffee machine'))

    def test_full_feature_list_excludes_false_fields(self):
        amenities = Amenity.objects.create(property=self.property, pool=False, barbecue=False)
        features = amenities.full_feature_list()
        self.assertNotIn('Pool', features)
        self.assertNotIn('Barbecue', features)

    def test_full_feature_list_drops_generic_air_conditioning_when_room_specific_is_set(self):
        amenities = Amenity.objects.create(
            property=self.property, air_conditioning=True, air_conditioning_in_bedrooms=True,
            air_conditioning_in_living_room=False,
        )
        features = amenities.full_feature_list()
        self.assertNotIn('Air conditioning', features)
        self.assertIn('Air conditioning in bedrooms', features)

    def test_full_feature_list_drops_generic_heating_when_either_room_specific_is_set(self):
        amenities = Amenity.objects.create(
            property=self.property, heating=True, heating_in_bedrooms=False, heating_in_living_room=True,
        )
        features = amenities.full_feature_list()
        self.assertNotIn('Heating', features)
        self.assertIn('Heating in living room', features)

    def test_full_feature_list_keeps_generic_air_conditioning_when_no_room_specific_is_set(self):
        amenities = Amenity.objects.create(
            property=self.property, air_conditioning=True,
            air_conditioning_in_bedrooms=False, air_conditioning_in_living_room=False,
        )
        features = amenities.full_feature_list()
        self.assertIn('Air conditioning', features)
