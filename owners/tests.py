from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bookings.models import Arrival, Booking, Charge, Departure, PaymentSettings
from bookings.utils import create_owner_booking, guest_for_owner
from guests.models import Guest
from properties.models import ManagementCompany, Owner, Property, PropertySpec

User = get_user_model()


class OwnerSuiteTests(TestCase):
    """The Owner Suite login gate and its two pages - see properties.models.Owner.user and
    owners/permissions.py::owner_login_required for how this differs from both staff auth
    (StaffProfile/StaffRole) and the guest Manage Booking hub (reference+email, no accounts)."""

    def setUp(self):
        self.owner = Owner.objects.create(
            name='Portal Owner', email='portal-owner@example.com', default_clean=False,
            default_meet_greet=False, takes_euros=True, takes_pounds=False,
            cleans_are_invoiced=False, rental_commissions_are_invoiced=False, is_paid_regularly=False,
        )
        self.owner_user = User.objects.create_user(username='portalowner', password='pw')
        self.owner.user = self.owner_user
        self.owner.save(update_fields=['user'])

        self.other_owner = Owner.objects.create(
            name='Other Owner', email='other-owner@example.com', default_clean=False,
            default_meet_greet=False, takes_euros=True, takes_pounds=False,
            cleans_are_invoiced=False, rental_commissions_are_invoiced=False, is_paid_regularly=False,
        )

        self.non_owner_user = User.objects.create_user(username='randomuser', password='pw')

        self.company = ManagementCompany.objects.create(name='Owner Suite Co', finances_managed_internally=True)
        self.property = Property.objects.create(
            title='Owner Suite Property', short_title='OWNSUITE', owner=self.owner,
            cleaning_company=self.company, booking_company=self.company, standard_cleaning_fee=Decimal('80.00'),
        )
        PropertySpec.objects.create(property=self.property, bedrooms=2)
        self.other_property = Property.objects.create(
            title='Other Owner Property', short_title='OTHOWN', owner=self.other_owner,
        )
        PropertySpec.objects.create(property=self.other_property, bedrooms=2)

        self.guest = Guest.objects.create(first_name='Own', last_name='Er', email='owner-suite-guest@example.com')
        settings_obj = PaymentSettings.load()
        settings_obj.meet_greet_fee = Decimal('28.00')
        settings_obj.save()
        self.today = timezone.now().date()

        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.today + timedelta(days=3),
            departure_date=self.today + timedelta(days=7), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=self.booking, basic_rental=Decimal('300.00'))
        Departure.objects.create(booking=self.booking, clean=True)
        Arrival.objects.create(booking=self.booking, meet_greet=True)

        self.other_booking = Booking.objects.create(
            property=self.other_property, guest=self.guest, arrival_date=self.today + timedelta(days=3),
            departure_date=self.today + timedelta(days=7), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_home_redirects_anonymous_visitor_to_login(self):
        response = self.client.get(reverse('owners:home'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('owners:login'), response.url)

    def test_a_user_with_no_owner_profile_is_rejected_at_login(self):
        """The template deliberately shows the same generic 'incorrect username or password'
        message for both a wrong password and a valid-but-unlinked account (see login.html) -
        not distinguishing account existence is the more secure default - so this only asserts
        the session was never established, not the wording."""
        response = self.client.post(reverse('owners:login'), {'username': 'randomuser', 'password': 'pw'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        home_response = self.client.get(reverse('owners:home'))
        self.assertEqual(home_response.status_code, 302)

    def test_a_linked_owner_can_log_in_and_reach_home(self):
        response = self.client.post(reverse('owners:login'), {'username': 'portalowner', 'password': 'pw'}, follow=True)
        self.assertRedirects(response, reverse('owners:home'))
        self.assertContains(response, 'Owner Suite Property')

    def test_report_only_shows_this_owners_own_bookings(self):
        self.client.login(username='portalowner', password='pw')
        response = self.client.get(reverse('owners:reports'), {
            'start': self.today.isoformat(), 'end': (self.today + timedelta(days=14)).isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['booking'], self.booking)

    def test_report_shows_every_report_column_by_default(self):
        self.client.login(username='portalowner', password='pw')
        response = self.client.get(reverse('owners:reports'), {
            'start': self.today.isoformat(), 'end': (self.today + timedelta(days=14)).isoformat(),
        })
        self.assertEqual(response.context['selected_columns'], {
            'rental_to_owner', 'basic_rental', 'platform_fee', 'platform_fee_vat', 'clean_cost',
            'meet_greet_cost', 'maintenance_cost', 'net_revenue',
        })
        self.assertContains(response, '&euro;300.00')

    def test_report_never_shows_an_admin_fee_column(self):
        """Thomas: owners shouldn't see the admin fee - it was never part of REPORT_COLUMNS to
        begin with (basic_rental is Charge.total_rental, which excludes admin fee by
        construction), so this just pins that down as a regression guard."""
        self.client.login(username='portalowner', password='pw')
        response = self.client.get(reverse('owners:reports'), {
            'start': self.today.isoformat(), 'end': (self.today + timedelta(days=14)).isoformat(),
        })
        self.assertNotIn('admin_fee', response.context['selected_columns'])
        self.assertNotContains(response, 'Admin fee')


class OwnerBookingsTests(TestCase):
    """My Stays / New Reservation / booking detail - the three capabilities Thomas asked for
    2026-08-30 (consult/alter/cancel own dates, reserve new dates, arrival & departure details
    incl. clean/meet & greet toggles). See bookings/tests.py::CreateOwnerBookingTests for the
    lower-level create_owner_booking()/guest_for_owner() coverage this builds on."""

    def setUp(self):
        self.owner = Owner.objects.create(
            name='Stays Owner', email='stays-owner@example.com', default_clean=False,
            default_meet_greet=False, takes_euros=True, takes_pounds=False,
            cleans_are_invoiced=False, rental_commissions_are_invoiced=False, is_paid_regularly=False,
        )
        self.owner_user = User.objects.create_user(username='staysowner', password='pw')
        self.owner.user = self.owner_user
        self.owner.save(update_fields=['user'])

        self.other_owner = Owner.objects.create(
            name='Other Stays Owner', email='other-stays-owner@example.com', default_clean=False,
            default_meet_greet=False, takes_euros=True, takes_pounds=False,
            cleans_are_invoiced=False, rental_commissions_are_invoiced=False, is_paid_regularly=False,
        )
        self.other_owner_user = User.objects.create_user(username='otherstaysowner', password='pw')
        self.other_owner.user = self.other_owner_user
        self.other_owner.save(update_fields=['user'])

        self.property = Property.objects.create(
            title='Stays Property', short_title='STAYSPROP', owner=self.owner,
        )
        PropertySpec.objects.create(property=self.property, max_guests=6)
        self.other_property = Property.objects.create(
            title='Other Stays Property', short_title='OTHSTAYS', owner=self.other_owner,
        )
        PropertySpec.objects.create(property=self.other_property, max_guests=6)

        self.today = timezone.now().date()
        self.upcoming_booking = create_owner_booking(
            self.property, self.owner, self.today + timedelta(days=30), self.today + timedelta(days=34),
            adults=2, children=0, babies=0,
        )
        # Built directly rather than via create_owner_booking() - that helper now rejects a past
        # arrival date outright (2026-08-30), which a *real* past stay would only have picked up
        # after time simply passed, not at creation time.
        self.past_booking = Booking.objects.create(
            property=self.property, guest=guest_for_owner(self.owner),
            arrival_date=self.today - timedelta(days=10), departure_date=self.today - timedelta(days=6),
            is_owner=True, enquiry_status='Booking confirmed', enquiry_source='Owner Suite',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Departure.objects.create(booking=self.past_booking)
        self.other_owner_booking = create_owner_booking(
            self.other_property, self.other_owner, self.today + timedelta(days=30), self.today + timedelta(days=34),
            adults=2, children=0, babies=0,
        )

    def test_list_splits_upcoming_and_past(self):
        self.client.login(username='staysowner', password='pw')
        response = self.client.get(reverse('owners:bookings'))
        self.assertEqual(response.status_code, 200)
        upcoming = [row['booking'] for row in response.context['upcoming_rows']]
        self.assertEqual(upcoming, [self.upcoming_booking])
        self.assertEqual(list(response.context['history_bookings']), [self.past_booking])

    def test_list_never_shows_another_owners_booking(self):
        self.client.login(username='staysowner', password='pw')
        response = self.client.get(reverse('owners:bookings'))
        all_shown = [row['booking'] for row in response.context['upcoming_rows']]
        all_shown += list(response.context['history_bookings'])
        self.assertNotIn(self.other_owner_booking, all_shown)

    def test_upcoming_row_flags_missing_arrival_details(self):
        """A freshly-reserved stay has an eagerly-created but blank Arrival row (no flight
        number/time/travelling-from yet) - the Action column's red flag, per Thomas 2026-08-30."""
        self.client.login(username='staysowner', password='pw')
        response = self.client.get(reverse('owners:bookings'))
        row = next(r for r in response.context['upcoming_rows'] if r['booking'] == self.upcoming_booking)
        self.assertTrue(row['needs_action'])
        self.assertContains(response, 'Action needed')

    def test_upcoming_row_flag_clears_once_arrival_and_guest_details_are_saved(self):
        self.client.login(username='staysowner', password='pw')
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {
                'action': 'save_arrival_departure', 'arrival_method': 'flight_faro',
                'arrival_flight_number': 'TP1234', 'meet_greet': 'on',
                'guest_first_name': 'Jane', 'guest_last_name': 'Doe', 'guest_phone': '+351911111111',
            },
        )
        response = self.client.get(reverse('owners:bookings'))
        row = next(r for r in response.context['upcoming_rows'] if r['booking'] == self.upcoming_booking)
        self.assertFalse(row['needs_action'])

    def test_flag_ignores_missing_guest_email(self):
        """Email is a bonus, not crucial - Thomas 2026-08-30: its absence must never raise the
        flag once name/phone/travel details are all in and meet & greet is still required."""
        self.client.login(username='staysowner', password='pw')
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {
                'action': 'save_arrival_departure', 'arrival_method': 'flight_faro',
                'arrival_flight_number': 'TP1234', 'meet_greet': 'on',
                'guest_first_name': 'Jane', 'guest_last_name': 'Doe', 'guest_phone': '+351911111111',
                'guest_email': '',
            },
        )
        response = self.client.get(reverse('owners:bookings'))
        row = next(r for r in response.context['upcoming_rows'] if r['booking'] == self.upcoming_booking)
        self.assertFalse(row['needs_action'])

    def test_flag_stays_set_when_meet_greet_required_but_guest_name_missing(self):
        self.client.login(username='staysowner', password='pw')
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {
                'action': 'save_arrival_departure', 'arrival_method': 'flight_faro',
                'arrival_flight_number': 'TP1234', 'meet_greet': 'on',
            },
        )
        response = self.client.get(reverse('owners:bookings'))
        row = next(r for r in response.context['upcoming_rows'] if r['booking'] == self.upcoming_booking)
        self.assertTrue(row['needs_action'])

    def test_source_column_shows_enquiry_source(self):
        self.client.login(username='staysowner', password='pw')
        response = self.client.get(reverse('owners:bookings'))
        self.assertContains(response, 'Owner Suite')

    def test_detail_404s_for_another_owners_booking(self):
        self.client.login(username='staysowner', password='pw')
        response = self.client.get(reverse('owners:booking_detail', kwargs={'reference': self.other_owner_booking.reference}))
        self.assertEqual(response.status_code, 404)

    def test_create_view_rejects_a_property_not_owned_by_this_owner(self):
        self.client.login(username='staysowner', password='pw')
        response = self.client.post(reverse('owners:booking_create'), {
            'property_id': self.other_property.pk,
            'arrival_date': (self.today + timedelta(days=60)).isoformat(),
            'departure_date': (self.today + timedelta(days=64)).isoformat(),
            'adults': '2', 'children': '0', 'babies': '0',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['error'])
        self.assertEqual(Booking.objects.filter(property=self.other_property, guest__email=self.owner.email).count(), 0)

    def test_create_view_shows_an_error_when_no_property_is_selected(self):
        """A blank property_id ('Select a property…' left selected) used to 500 - Property.
        objects.filter(pk='', ...) tries int('') and raises ValueError instead of matching
        nothing - confirmed live 2026-08-30."""
        self.client.login(username='staysowner', password='pw')
        response = self.client.post(reverse('owners:booking_create'), {
            'property_id': '',
            'arrival_date': (self.today + timedelta(days=60)).isoformat(),
            'departure_date': (self.today + timedelta(days=64)).isoformat(),
            'adults': '2', 'children': '0', 'babies': '0',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['error'])

    def test_create_view_creates_a_booking_and_redirects_to_its_detail_page(self):
        self.client.login(username='staysowner', password='pw')
        arrival = self.today + timedelta(days=60)
        departure = self.today + timedelta(days=64)
        response = self.client.post(reverse('owners:booking_create'), {
            'property_id': self.property.pk,
            'arrival_date': arrival.isoformat(), 'departure_date': departure.isoformat(),
            'adults': '2', 'children': '1', 'babies': '0',
        })
        booking = Booking.objects.get(property=self.property, arrival_date=arrival)
        self.assertRedirects(response, reverse('owners:booking_detail', kwargs={'reference': booking.reference}))
        self.assertTrue(booking.is_owner)
        self.assertEqual(booking.children, 1)

    def test_create_view_shows_an_error_for_overlapping_dates(self):
        self.client.login(username='staysowner', password='pw')
        response = self.client.post(reverse('owners:booking_create'), {
            'property_id': self.property.pk,
            'arrival_date': (self.upcoming_booking.arrival_date + timedelta(days=1)).isoformat(),
            'departure_date': (self.upcoming_booking.departure_date + timedelta(days=1)).isoformat(),
            'adults': '2', 'children': '0', 'babies': '0',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['error'])

    def test_update_dates_changes_the_booking(self):
        self.client.login(username='staysowner', password='pw')
        new_arrival = self.upcoming_booking.arrival_date + timedelta(days=1)
        new_departure = self.upcoming_booking.departure_date + timedelta(days=1)
        response = self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {'action': 'update_dates', 'arrival_date': new_arrival.isoformat(), 'departure_date': new_departure.isoformat()},
        )
        self.assertRedirects(response, reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}))
        self.upcoming_booking.refresh_from_db()
        self.assertEqual(self.upcoming_booking.arrival_date, new_arrival)
        self.assertEqual(self.upcoming_booking.departure_date, new_departure)

    def test_update_dates_cannot_be_changed_on_a_past_stay(self):
        self.client.login(username='staysowner', password='pw')
        original_arrival = self.past_booking.arrival_date
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.past_booking.reference}),
            {'action': 'update_dates', 'arrival_date': self.today.isoformat(), 'departure_date': (self.today + timedelta(days=2)).isoformat()},
        )
        self.past_booking.refresh_from_db()
        self.assertEqual(self.past_booking.arrival_date, original_arrival)

    def test_update_dates_rejects_a_new_arrival_date_in_the_past(self):
        self.client.login(username='staysowner', password='pw')
        original_arrival = self.upcoming_booking.arrival_date
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {
                'action': 'update_dates', 'arrival_date': (self.today - timedelta(days=1)).isoformat(),
                'departure_date': (self.today + timedelta(days=2)).isoformat(),
            },
        )
        self.upcoming_booking.refresh_from_db()
        self.assertEqual(self.upcoming_booking.arrival_date, original_arrival)

    def test_save_arrival_departure_sets_meet_greet_and_clean(self):
        """meet_greet/clean ARE owner-editable here (unlike the guest-facing Manage Booking hub) -
        see OwnerBookingDetailView's own docstring."""
        self.client.login(username='staysowner', password='pw')
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {
                'action': 'save_arrival_departure', 'arrival_method': 'flight_faro',
                'arrival_flight_number': 'TP1234', 'departure_method': 'driving',
                'departure_travelling_from': 'Lisbon', 'meet_greet': 'on',
            },
        )
        self.upcoming_booking.refresh_from_db()
        self.assertTrue(self.upcoming_booking.arrival.meet_greet)
        self.assertEqual(self.upcoming_booking.arrival.flight_number, 'TP1234')
        self.assertFalse(self.upcoming_booking.departure.clean)  # 'clean' checkbox omitted = unchecked

    def test_save_arrival_departure_saves_guest_details_when_meet_greet_is_on(self):
        self.client.login(username='staysowner', password='pw')
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {
                'action': 'save_arrival_departure', 'arrival_method': 'flight_faro', 'meet_greet': 'on',
                'guest_first_name': 'Jane', 'guest_last_name': 'Doe', 'guest_phone': '+351911111111',
                'guest_email': 'jane@example.com',
            },
        )
        self.upcoming_booking.refresh_from_db()
        guest = self.upcoming_booking.guest
        self.assertEqual(guest.first_name, 'Jane')
        self.assertEqual(guest.last_name, 'Doe')
        self.assertEqual(guest.phone, '+351911111111')
        self.assertEqual(guest.email, 'jane@example.com')

    def test_save_arrival_departure_never_clobbers_guest_details_when_meet_greet_is_off(self):
        """The Guest Details panel is hidden AND disabled client-side while Meet & Greet is
        unticked, so a resubmit with it off must never overwrite what's already on record - same
        disabled-field-omitted-from-POST guard as the meet_greet/clean fields themselves."""
        self.client.login(username='staysowner', password='pw')
        url = reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference})
        self.client.post(url, {
            'action': 'save_arrival_departure', 'arrival_method': 'flight_faro', 'meet_greet': 'on',
            'guest_first_name': 'Jane', 'guest_last_name': 'Doe', 'guest_phone': '+351911111111',
        })
        self.client.post(url, {'action': 'save_arrival_departure', 'arrival_method': 'flight_faro'})
        self.upcoming_booking.refresh_from_db()
        guest = self.upcoming_booking.guest
        self.assertEqual(guest.first_name, 'Jane')
        self.assertEqual(guest.phone, '+351911111111')

    def test_cancel_sets_status_and_is_excluded_from_upcoming(self):
        self.client.login(username='staysowner', password='pw')
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {'action': 'cancel'},
        )
        self.upcoming_booking.refresh_from_db()
        self.assertEqual(self.upcoming_booking.enquiry_status, 'Cancelled by owner')
        response = self.client.get(reverse('owners:bookings'))
        upcoming = [row['booking'] for row in response.context['upcoming_rows']]
        self.assertNotIn(self.upcoming_booking, upcoming)


class OwnerCalendarTests(TestCase):
    """The Calendar tab - the same availability.utils.get_property_calendar()/
    availability/calendar.html the public property page uses, scoped to a property dropdown that
    defaults to the owner's lowest-pk property, per Thomas 2026-08-30."""

    def setUp(self):
        self.owner = Owner.objects.create(
            name='Calendar Owner', email='calendar-owner@example.com', default_clean=False,
            default_meet_greet=False, takes_euros=True, takes_pounds=False,
            cleans_are_invoiced=False, rental_commissions_are_invoiced=False, is_paid_regularly=False,
        )
        self.owner_user = User.objects.create_user(username='calendarowner', password='pw')
        self.owner.user = self.owner_user
        self.owner.save(update_fields=['user'])

        self.first_property = Property.objects.create(
            title='Calendar First Property', short_title='CALFIRST', owner=self.owner,
        )
        self.second_property = Property.objects.create(
            title='Calendar Second Property', short_title='CALSECOND', owner=self.owner,
        )

    def test_defaults_to_the_lowest_pk_property(self):
        self.client.login(username='calendarowner', password='pw')
        response = self.client.get(reverse('owners:calendar'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['property'], self.first_property)
        self.assertTrue(response.context['calendar_months'])

    def test_a_chosen_property_overrides_the_default(self):
        self.client.login(username='calendarowner', password='pw')
        response = self.client.get(reverse('owners:calendar'), {'property_id': self.second_property.pk})
        self.assertEqual(response.context['property'], self.second_property)

    def test_property_dropdown_never_offers_another_owners_property(self):
        other_owner = Owner.objects.create(
            name='Other Calendar Owner', email='other-calendar-owner@example.com', default_clean=False,
            default_meet_greet=False, takes_euros=True, takes_pounds=False,
            cleans_are_invoiced=False, rental_commissions_are_invoiced=False, is_paid_regularly=False,
        )
        other_property = Property.objects.create(
            title='Other Calendar Property', short_title='OTHCAL', owner=other_owner,
        )
        self.client.login(username='calendarowner', password='pw')
        response = self.client.get(reverse('owners:calendar'), {'property_id': other_property.pk})
        # An unrecognised property_id falls back to the default rather than honouring it.
        self.assertEqual(response.context['property'], self.first_property)
        self.assertNotIn(other_property, response.context['properties'])
