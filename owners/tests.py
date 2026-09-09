from datetime import time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from bookings.models import (
    Arrival, Booking, BookingRequestedExtra, Charge, Departure, Extra, ExtrasSettings, PaymentSettings,
    RequestType,
)
from bookings.utils import create_owner_booking, guest_for_owner
from finance.models import Memo, PayoutRecord
from guests.models import Guest
from libraries.phone_country_codes import join_phone, split_phone
from properties.models import Location, ManagementCompany, Owner, Property, PropertySpec
from staff.models import CleaningTask, LateCheckoutGrant
from staff.utils import grant_late_checkout

User = get_user_model()


class OwnerSuiteTests(TestCase):
    """The Owner Suite login gate and its two pages - see properties.models.Owner.user and
    owners/permissions.py::owner_login_required for how this differs from both staff auth
    (StaffProfile/StaffRole) and the guest Manage Booking hub (reference+email, no accounts)."""

    def setUp(self):
        self.owner = Owner.objects.create(
            name='Portal Owner', email='portal-owner@example.com', currency=Owner.Currency.EUR, is_paid_regularly=False,
        )
        self.owner_user = User.objects.create_user(username='portalowner', password='pw')
        self.owner.user = self.owner_user
        self.owner.save(update_fields=['user'])

        self.other_owner = Owner.objects.create(
            name='Other Owner', email='other-owner@example.com', currency=Owner.Currency.EUR, is_paid_regularly=False,
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

    def test_logout_via_post_logs_out_and_redirects_to_login(self):
        # Real bug, fixed 2026-09-03: Django 5's LogoutView is POST-only, but the sidebar's "Log
        # out" link was a plain GET <a> - a 405 on click, same bug class as the staff nav's own
        # Logout link (see staff.tests.StaffAuthGateTests).
        self.client.login(username='portalowner', password='pw')
        self.assertEqual(self.client.get(reverse('owners:home')).status_code, 200)

        response = self.client.post(reverse('owners:logout'))
        self.assertRedirects(response, reverse('owners:login'))
        self.assertEqual(self.client.get(reverse('owners:home')).status_code, 302)  # session cleared

    def test_logout_via_get_is_not_allowed(self):
        self.client.login(username='portalowner', password='pw')
        response = self.client.get(reverse('owners:logout'))
        self.assertEqual(response.status_code, 405)

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

    def test_report_never_shows_klt_internal_commission_columns(self):
        """commission/klt_net_commission (2026-08-30) are KLT's own internal Pre-IVA/Post-IVA
        commission figures - staff-only, same principle as admin fee above. OwnerReportView.
        COLUMNS uses OWNER_SAFE_REPORT_COLUMNS rather than the full staff REPORT_COLUMNS
        specifically to keep these two out."""
        self.client.login(username='portalowner', password='pw')
        response = self.client.get(reverse('owners:reports'), {
            'start': self.today.isoformat(), 'end': (self.today + timedelta(days=14)).isoformat(),
        })
        self.assertNotIn('commission', response.context['selected_columns'])
        self.assertNotIn('klt_net_commission', response.context['selected_columns'])
        self.assertNotIn('klt_net_revenue', response.context['selected_columns'])
        self.assertNotContains(response, 'KLT Net Commission')
        self.assertNotContains(response, 'KLT Net Revenue')


class OwnerAcceptInviteViewTests(TestCase):
    """owners:accept_invite - mirrors staff.tests.StaffAcceptInviteViewTests exactly (same
    PasswordResetConfirmView base, same token mechanics), added 2026-09-08 alongside
    staff.views.StaffSettingsView._invite_owner so an owner can set their own password instead of
    a staff member setting one for them."""

    def setUp(self):
        self.owner = Owner.objects.create(
            name='Invited Owner', email='invited-owner@example.com', currency=Owner.Currency.EUR, is_paid_regularly=False,
        )
        self.user = User.objects.create_user(username='invited-owner@example.com', email='invited-owner@example.com')
        self.user.set_unusable_password()
        self.user.save()
        self.owner.user = self.user
        self.owner.save(update_fields=['user'])
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        self.url = reverse('owners:accept_invite', kwargs={'uidb64': uid, 'token': token})

    def test_valid_link_shows_the_set_password_form(self):
        response = self.client.get(self.url, follow=True)
        self.assertContains(response, 'Set Your Password')

    def test_invalid_token_shows_expired_message(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        bad_url = reverse('owners:accept_invite', kwargs={'uidb64': uid, 'token': 'bad-token'})
        response = self.client.get(bad_url)
        self.assertContains(response, 'expired')

    def test_setting_a_password_logs_the_owner_in_and_makes_it_usable(self):
        get_response = self.client.get(self.url, follow=True)
        response = self.client.post(get_response.request['PATH_INFO'], {
            'new_password1': 'a-genuinely-strong-pw-98x',
            'new_password2': 'a-genuinely-strong-pw-98x',
        }, follow=True)
        self.user.refresh_from_db()
        self.assertTrue(self.user.has_usable_password())
        self.assertIn('_auth_user_id', self.client.session)
        self.assertRedirects(response, reverse('owners:home'))

    def test_mismatched_passwords_rejected(self):
        get_response = self.client.get(self.url, follow=True)
        self.client.post(get_response.request['PATH_INFO'], {
            'new_password1': 'a-genuinely-strong-pw-98x',
            'new_password2': 'does-not-match-99y',
        })
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_usable_password())


class PhoneCountryCodeTests(TestCase):
    """split_phone/join_phone (libraries/phone_country_codes.py) - the round-trip backing the
    Contact Details country-code dropdown, tested standalone since these are pure functions."""

    def test_split_recognises_a_known_calling_code_with_a_space(self):
        self.assertEqual(split_phone('+351 912345678'), ('+351', '912345678'))

    def test_split_recognises_a_known_calling_code_with_no_separator(self):
        self.assertEqual(split_phone('+351912345678'), ('+351', '912345678'))

    def test_split_falls_back_to_no_country_selected_for_an_unrecognised_or_blank_value(self):
        self.assertEqual(split_phone('0791 123456'), ('', '0791 123456'))
        self.assertEqual(split_phone(''), ('', ''))
        self.assertEqual(split_phone(None), ('', ''))

    def test_join_combines_code_and_local_number(self):
        self.assertEqual(join_phone('+351', '912345678'), '+351 912345678')

    def test_join_with_no_code_selected_stores_the_local_number_alone(self):
        self.assertEqual(join_phone('', '0791 123456'), '0791 123456')

    def test_join_with_nothing_entered_is_blank(self):
        self.assertEqual(join_phone('', ''), '')


class OwnerContactDetailsTests(TestCase):
    """Self-service phone/email/NIF editing (owners:contact_details) - added 2026-09-07 per
    Thomas so an owner can update their own contact details without emailing staff. Owner.email/
    phone/nif_number are all `unique=True`, so the validation-error path (mirroring the staff-side
    Owner edit form's own _flash_validation_error usage) is worth pinning down here too.

    Phone is posted as two fields (phone_country_code, phone) - the country-code dropdown added
    2026-09-07 - and stored back as the single Owner.phone string; see
    libraries/phone_country_codes.py::split_phone/join_phone for the round-trip this relies on."""

    def setUp(self):
        self.owner = Owner.objects.create(
            name='Contact Details Owner', email='contact-owner@example.com', phone='+351900000001',
            nif_number='111111111', currency=Owner.Currency.EUR, is_paid_regularly=False,
        )
        self.owner_user = User.objects.create_user(username='contactowner', password='pw')
        self.owner.user = self.owner_user
        self.owner.save(update_fields=['user'])

        self.other_owner = Owner.objects.create(
            name='Other Contact Owner', email='other-contact-owner@example.com', phone='+351900000002',
            nif_number='222222222', currency=Owner.Currency.EUR, is_paid_regularly=False,
        )

        self.client.login(username='contactowner', password='pw')

    def test_get_shows_current_contact_details(self):
        response = self.client.get(reverse('owners:contact_details'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'contact-owner@example.com')
        self.assertEqual(response.context['phone_code'], '+351')
        self.assertEqual(response.context['phone_local'], '900000001')
        self.assertContains(response, '900000001')
        self.assertContains(response, '111111111')

    def test_get_offers_a_calling_code_per_unique_code_not_per_country(self):
        response = self.client.get(reverse('owners:contact_details'))
        codes = [code for code, _label in response.context['phone_country_choices']]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertIn('+351', codes)
        self.assertIn('+44', codes)

    def test_post_updates_email_phone_and_nif(self):
        response = self.client.post(reverse('owners:contact_details'), {
            'email': 'updated-owner@example.com', 'phone_country_code': '+44',
            'phone': '7911123456', 'nif_number': '999999999',
        }, follow=True)
        self.assertRedirects(response, reverse('owners:contact_details'))
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.email, 'updated-owner@example.com')
        self.assertEqual(self.owner.phone, '+44 7911123456')
        self.assertEqual(self.owner.nif_number, '999999999')
        self.assertContains(response, 'Contact details updated.')

    def test_post_allows_clearing_phone_and_nif(self):
        """phone/nif_number are blank=True, null=True - clearing the input should null them out,
        not fail validation or silently keep the old value."""
        response = self.client.post(reverse('owners:contact_details'), {
            'email': self.owner.email, 'phone_country_code': '', 'phone': '', 'nif_number': '',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.owner.refresh_from_db()
        self.assertIsNone(self.owner.phone)
        self.assertIsNone(self.owner.nif_number)

    def test_post_rejects_an_email_already_used_by_another_owner(self):
        response = self.client.post(reverse('owners:contact_details'), {
            'email': self.other_owner.email, 'phone_country_code': '+351', 'phone': '900000001',
            'nif_number': self.owner.nif_number,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already exists')
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.email, 'contact-owner@example.com')

    def test_post_never_shows_another_owners_contact_details(self):
        response = self.client.get(reverse('owners:contact_details'))
        self.assertNotContains(response, self.other_owner.email)

    def test_anonymous_visitor_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse('owners:contact_details'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('owners:login'), response.url)


class OwnerBookingsTests(TestCase):
    """My Stays / New Reservation / booking detail - the three capabilities Thomas asked for
    2026-08-30 (consult/alter/cancel own dates, reserve new dates, arrival & departure details
    incl. clean/meet & greet toggles). See bookings/tests.py::CreateOwnerBookingTests for the
    lower-level create_owner_booking()/guest_for_owner() coverage this builds on."""

    def setUp(self):
        self.owner = Owner.objects.create(
            name='Stays Owner', email='stays-owner@example.com', currency=Owner.Currency.EUR, is_paid_regularly=False,
        )
        self.owner_user = User.objects.create_user(username='staysowner', password='pw')
        self.owner.user = self.owner_user
        self.owner.save(update_fields=['user'])

        self.other_owner = Owner.objects.create(
            name='Other Stays Owner', email='other-stays-owner@example.com', currency=Owner.Currency.EUR, is_paid_regularly=False,
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
        # create_owner_booking() creates the turnover CleaningTask eagerly, long before the
        # cleaning manager would actually staff it - assign someone here so late-checkout tests
        # below exercise the "clean scheduled" (fixed-slot) branch by default, same as a real
        # assigned clean would (2026-09-09, per Thomas: clean_scheduled_today requires an actual
        # assignment now, not just row existence - see late_checkout_eligibility()).
        CleaningTask.objects.get(booking=self.upcoming_booking, task_type='turnover').assigned_to.add(
            User.objects.create_user(username='owner_tests_cleaner', password='x'),
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

    def test_update_guests_changes_the_booking(self):
        self.client.login(username='staysowner', password='pw')
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {'action': 'update_guests', 'adults': '3', 'children': '2', 'babies': '1'},
        )
        self.upcoming_booking.refresh_from_db()
        self.assertEqual(self.upcoming_booking.adults, 3)
        self.assertEqual(self.upcoming_booking.children, 2)
        self.assertEqual(self.upcoming_booking.babies, 1)

    def test_update_guests_rejects_exceeding_the_propertys_max_guests(self):
        """self.property's PropertySpec caps at 6 guests - see Booking.clean()'s own
        adults+children+babies check, the same validation _update_dates() already relies on via
        full_clean()."""
        self.client.login(username='staysowner', password='pw')
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {'action': 'update_guests', 'adults': '5', 'children': '2', 'babies': '0'},
        )
        self.upcoming_booking.refresh_from_db()
        self.assertEqual(self.upcoming_booking.adults, 2)  # unchanged from setUp

    def test_update_guests_rejects_zero_adults(self):
        self.client.login(username='staysowner', password='pw')
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {'action': 'update_guests', 'adults': '0', 'children': '0', 'babies': '0'},
        )
        self.upcoming_booking.refresh_from_db()
        self.assertEqual(self.upcoming_booking.adults, 2)  # unchanged from setUp

    def test_update_guests_cannot_be_changed_on_a_past_stay(self):
        self.client.login(username='staysowner', password='pw')
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.past_booking.reference}),
            {'action': 'update_guests', 'adults': '4', 'children': '0', 'babies': '0'},
        )
        self.past_booking.refresh_from_db()
        self.assertEqual(self.past_booking.adults, 2)  # unchanged from setUp

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

    def test_save_arrival_departure_applies_property_check_in_policy(self):
        company = ManagementCompany.objects.create(
            name='Owner Suite Self Check-in Co',
            check_in_method=ManagementCompany.CheckInMethod.SELF_CHECK_IN,
        )
        self.property.booking_company = company
        self.property.save(update_fields=['booking_company'])
        self.client.login(username='staysowner', password='pw')
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {'action': 'save_arrival_departure', 'arrival_method': 'flight_faro'},
        )
        self.upcoming_booking.refresh_from_db()
        self.assertTrue(self.upcoming_booking.arrival.self_check_in)

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

    def test_extras_cot_high_chair_shown_only_when_booking_has_infants(self):
        """Owner bookings have no BookingGuest party rows to check ages from (unlike the guest
        Manage Booking hub's own Cot & High Chair gate) - Booking.babies is the only infant
        signal that actually exists here, per Thomas 2026-08-30."""
        self.client.login(username='staysowner', password='pw')
        response = self.client.get(reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}))
        self.assertFalse(response.context['show_cot_high_chair'])

        self.upcoming_booking.babies = 1
        self.upcoming_booking.save(update_fields=['babies'])
        response = self.client.get(reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}))
        self.assertTrue(response.context['show_cot_high_chair'])

    def test_update_extras_saves_welcome_pack_cot_high_chair_and_late_checkout(self):
        # create_owner_booking() creates a real Departure(clean=True) eagerly, so this booking has
        # a genuine turnover CleaningTask scheduled with no same-day arrival - both 11:00/12:00 are
        # eligible (2026-09-09: late checkout is validated against real permissibility rules now,
        # no longer any freeform time - '13:00' would be rejected).
        self.client.login(username='staysowner', password='pw')
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {
                'action': 'update_extras',
                'welcome_pack': 'on', 'welcome_pack_food': 'vegan', 'welcome_pack_drinks': 'non_alcoholic',
                'cot': 'on', 'high_chair': 'on',
                'late_checkout': 'on', 'late_checkout_time': '11:00',
            },
        )
        extra = Extra.objects.get(booking=self.upcoming_booking)
        self.assertTrue(extra.welcome_pack)
        self.assertEqual(extra.welcome_pack_food, 'vegan')
        self.assertEqual(extra.welcome_pack_drinks, 'non_alcoholic')
        self.assertTrue(extra.cot)
        self.assertTrue(extra.high_chair)
        self.assertIsNotNone(extra.cot_high_chair_charge)
        self.assertTrue(extra.late_checkout)
        self.assertEqual(extra.late_checkout_time.strftime('%H:%M'), '11:00')
        self.assertEqual(extra.late_checkout_charge, ExtrasSettings.load().late_checkout_price)

    def test_update_extras_grants_an_unlimited_late_checkout_and_creates_a_block_booking(self):
        # Departure.clean=False means no clean is ever scheduled for this departure at all (see
        # sync_cleaning_tasks_for_booking()'s own docstring) - late_checkout_eligibility() then
        # returns unlimited=True, same rule an owner's own stay is subject to as a guest's
        # (2026-09-09, per Thomas). Proves this form now calls the real grant/block-booking engine
        # rather than only setting Extra fields, unlike before this date. (Directly deleting the
        # CleaningTask instead of doing this doesn't work - the Extra.objects.get_or_create() at
        # the top of _save_owner_extras() fires Extra's post_save sync signal, which would just
        # recreate it from Departure.clean=True before the late-checkout check even runs.)
        self.upcoming_booking.departure.clean = False
        self.upcoming_booking.departure.save(update_fields=['clean'])
        self.client.login(username='staysowner', password='pw')
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {'action': 'update_extras', 'late_checkout': 'on', 'late_checkout_time': ''},
        )
        extra = Extra.objects.get(booking=self.upcoming_booking)
        self.assertTrue(extra.late_checkout)
        self.assertIsNone(extra.late_checkout_time)
        grant = LateCheckoutGrant.objects.get(booking=self.upcoming_booking)
        self.assertIsNone(grant.time)
        self.assertIsNotNone(grant.block_booking)
        self.assertEqual(grant.block_booking.arrival_date, self.upcoming_booking.departure_date)

    def test_update_extras_rejects_a_late_checkout_time_already_claimed_in_the_same_location(self):
        # Same location-wide 1-slot-per-time cap a guest's own request is subject to
        # (staff/utils.py::late_checkout_still_available) - grabbing the only 11:00 slot for
        # another property in the same Location before the owner submits must reject the owner's
        # request too, proving the cap is shared across guest and owner bookings alike.
        location = Location.objects.create(title='Owner Grant Location')
        self.property.location = location
        self.property.save(update_fields=['location'])

        # A same-day arrival at self.property forces the "fixed slots only, if the crew has slack"
        # branch of late_checkout_eligibility() rather than "no same-day arrival, both times free".
        Booking.objects.create(
            property=self.property, guest=guest_for_owner(self.owner),
            arrival_date=self.upcoming_booking.departure_date,
            departure_date=self.upcoming_booking.departure_date + timedelta(days=3),
            is_owner=True, enquiry_status='Booking confirmed', enquiry_source='Owner Suite',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        # Two other same-day turnover cleans in the same Location unlock the 11:00 slot (2 needed).
        other_bookings = []
        for n in range(2):
            other_property = Property.objects.create(
                title=f'Owner Grant Other {n}', short_title=f'OWNGRNT{n}', location=location,
            )
            other_guest = Guest.objects.create(
                first_name='Other', last_name=f'OwnerGrant{n}', email=f'owner-grant-{n}@example.com',
            )
            other_booking = Booking.objects.create(
                property=other_property, guest=other_guest,
                arrival_date=self.upcoming_booking.departure_date - timedelta(days=5),
                departure_date=self.upcoming_booking.departure_date,
                is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
                adults=2, children=0, babies=0, last_updated=timezone.now(),
            )
            Departure.objects.create(booking=other_booking, clean=True)
            CleaningTask.objects.get(booking=other_booking, task_type='turnover').assigned_to.add(
                User.objects.create_user(username=f'owner_grant_other_cleaner_{n}', password='x'),
            )
            other_bookings.append(other_booking)

        # One of those other bookings claims the only 11:00 slot first.
        grant, error = grant_late_checkout(other_bookings[0], time(11, 0))
        self.assertIsNone(error)

        self.client.login(username='staysowner', password='pw')
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {'action': 'update_extras', 'late_checkout': 'on', 'late_checkout_time': '11:00'},
        )
        self.assertFalse(Extra.objects.filter(booking=self.upcoming_booking, late_checkout=True).exists())
        self.assertFalse(LateCheckoutGrant.objects.filter(booking=self.upcoming_booking).exists())

    def test_extras_shows_owner_is_paying_only_when_meet_greet_is_required(self):
        """create_owner_booking() defaults meet_greet=True, so self.upcoming_booking's Arrival
        starts out requiring one - Owner is paying for extras only makes sense to show while
        that's the case, per Thomas 2026-08-30."""
        self.client.login(username='staysowner', password='pw')
        response = self.client.get(reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}))
        self.assertTrue(response.context['show_owner_is_paying'])

        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {'action': 'save_arrival_departure', 'arrival_method': 'flight_faro'},  # meet_greet omitted = unchecked
        )
        response = self.client.get(reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}))
        self.assertFalse(response.context['show_owner_is_paying'])

    def test_update_extras_saves_owner_is_paying(self):
        self.client.login(username='staysowner', password='pw')
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {'action': 'update_extras', 'owner_is_paying': 'on'},
        )
        extra = Extra.objects.get(booking=self.upcoming_booking)
        self.assertTrue(extra.owner_is_paying)

    def test_update_extras_never_clobbers_owner_is_paying_when_meet_greet_is_off(self):
        """Owner is paying for extras is hidden (and so never posted) once Meet & Greet is off -
        a later Extras save with the checkbox absent from the form must not silently reset a
        previously-set True back to False."""
        self.client.login(username='staysowner', password='pw')
        url = reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference})
        self.client.post(url, {'action': 'update_extras', 'owner_is_paying': 'on'})
        self.client.post(url, {'action': 'save_arrival_departure', 'arrival_method': 'flight_faro'})  # meet_greet off
        self.client.post(url, {'action': 'update_extras'})  # owner_is_paying not posted - hidden
        extra = Extra.objects.get(booking=self.upcoming_booking)
        self.assertTrue(extra.owner_is_paying)

    def test_update_extras_saves_an_airport_transfer_row(self):
        self.client.login(username='staysowner', password='pw')
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {
                'action': 'update_extras',
                'transfer_direction[]': ['inbound'], 'transfer_airport[]': ['faro'],
                'transfer_flight_number[]': ['TP1234'], 'transfer_time[]': ['14:00'],
                'transfer_adults[]': ['2'], 'transfer_children[]': ['0'], 'transfer_infants[]': ['0'],
                'transfer_child_seats[]': [''], 'transfer_excess_baggage[]': [''], 'transfer_notes[]': [''],
            },
        )
        self.upcoming_booking.refresh_from_db()
        transfer = self.upcoming_booking.airport_transfers.get()
        self.assertEqual(transfer.direction, 'inbound')
        self.assertEqual(transfer.flight_number, 'TP1234')

    def test_update_extras_requires_a_time_when_late_checkout_is_checked(self):
        self.client.login(username='staysowner', password='pw')
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {'action': 'update_extras', 'late_checkout': 'on', 'late_checkout_time': ''},
        )
        self.assertFalse(Extra.objects.filter(booking=self.upcoming_booking, late_checkout=True).exists())

    def test_update_extras_never_touches_mid_stay_clean_or_special_requests(self):
        """This owner form deliberately doesn't present Mid-stay Clean or Special Requests (see
        OwnerBookingDetailView._save_owner_extras()'s own docstring) - saving it must never wipe
        out either, even though BookingFormMixin._save_extras() (the guest-facing equivalent)
        would happily delete/rebuild both from a POST body that simply doesn't mention them."""
        request_type = RequestType.objects.create(name='Extra pillows', default_price=Decimal('5.00'), active=True)
        extra = Extra.objects.create(
            booking=self.upcoming_booking, mid_stay_clean=True, mid_stay_clean_charge=Decimal('30.00'),
        )
        BookingRequestedExtra.objects.create(
            booking=self.upcoming_booking, request_type=request_type, quantity=2, price_at_request=Decimal('5.00'),
        )

        self.client.login(username='staysowner', password='pw')
        self.client.post(
            reverse('owners:booking_detail', kwargs={'reference': self.upcoming_booking.reference}),
            {'action': 'update_extras', 'welcome_pack': 'on', 'welcome_pack_food': 'standard', 'welcome_pack_drinks': 'alcoholic'},
        )

        extra.refresh_from_db()
        self.assertTrue(extra.mid_stay_clean)
        self.assertEqual(extra.mid_stay_clean_charge, Decimal('30.00'))
        self.assertEqual(self.upcoming_booking.requested_extras.count(), 1)


class OwnerCalendarTests(TestCase):
    """The Calendar tab - the same availability.utils.get_property_calendar()/
    availability/calendar.html the public property page uses, scoped to a property dropdown that
    defaults to the owner's lowest-pk property, per Thomas 2026-08-30."""

    def setUp(self):
        self.owner = Owner.objects.create(
            name='Calendar Owner', email='calendar-owner@example.com', currency=Owner.Currency.EUR, is_paid_regularly=False,
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
            name='Other Calendar Owner', email='other-calendar-owner@example.com', currency=Owner.Currency.EUR, is_paid_regularly=False,
        )
        other_property = Property.objects.create(
            title='Other Calendar Property', short_title='OTHCAL', owner=other_owner,
        )
        self.client.login(username='calendarowner', password='pw')
        response = self.client.get(reverse('owners:calendar'), {'property_id': other_property.pk})
        # An unrecognised property_id falls back to the default rather than honouring it.
        self.assertEqual(response.context['property'], self.first_property)
        self.assertNotIn(other_property, response.context['properties'])


class OwnerPayoutsMemosTests(TestCase):
    """Payouts & Memos - a unified ledger of finance.models.PayoutRecord/Memo rows against this
    owner's own properties, per Thomas 2026-08-30. PayoutRecord/Memo are created via the real
    staff views (mirroring finance/tests.py::PayoutRecordTests's own convention), not
    .objects.create() directly, so this exercises the exact same code path staff actually use."""

    def setUp(self):
        self.owner = Owner.objects.create(
            name='Payouts Owner', email='payouts-owner@example.com', currency=Owner.Currency.EUR, is_paid_regularly=False,
        )
        self.owner_user = User.objects.create_user(username='payoutsowner', password='pw')
        self.owner.user = self.owner_user
        self.owner.save(update_fields=['user'])

        self.other_owner = Owner.objects.create(
            name='Other Payouts Owner', email='other-payouts-owner@example.com', currency=Owner.Currency.EUR, is_paid_regularly=False,
        )
        self.other_owner_user = User.objects.create_user(username='otherpayoutsowner', password='pw')
        self.other_owner.user = self.other_owner_user
        self.other_owner.save(update_fields=['user'])

        self.company = ManagementCompany.objects.create(
            name='Payouts Test Co', finances_managed_internally=True,
        )
        self.property = Property.objects.create(
            title='Payouts Property', short_title='PAYOUTPROP', owner=self.owner,
            cleaning_company=self.company, booking_company=self.company, standard_cleaning_fee=Decimal('80.00'),
        )
        PropertySpec.objects.create(property=self.property, bedrooms=2)
        guest = Guest.objects.create(first_name='Pay', last_name='Out', email='payouts-guest@example.com')
        self.today = timezone.now().date()
        self.booking = Booking.objects.create(
            property=self.property, guest=guest,
            arrival_date=self.today + timedelta(days=10), departure_date=self.today + timedelta(days=14),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=self.booking, basic_rental=Decimal('300.00'))
        Departure.objects.create(booking=self.booking, clean=True)  # auto-creates a Memo via signal

        staffer = User.objects.create_user(username='payoutsstaff', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='payoutsstaff', password='pw')
        self.client.post(reverse('staff:finance_payout_mark_paid', kwargs={'reference': self.booking.reference}))
        self.memo = Memo.objects.get(property=self.property)
        self.client.post(reverse('staff:finance_memo_send', kwargs={'pk': self.memo.pk}))
        self.client.logout()
        self.payout_record = PayoutRecord.objects.get(booking=self.booking)
        self.memo.refresh_from_db()

    def test_lists_both_a_payout_and_a_memo_row(self):
        self.client.login(username='payoutsowner', password='pw')
        response = self.client.get(reverse('owners:payouts_memos'))
        rows_by_type = {row['type']: row for row in response.context['rows']}
        self.assertEqual(set(rows_by_type), {'Payout', 'Memo'})
        self.assertEqual(rows_by_type['Payout']['reference'], self.booking.reference)
        self.assertEqual(rows_by_type['Payout']['amount'], self.payout_record.amount)
        self.assertFalse(rows_by_type['Payout']['is_charge'])
        self.assertEqual(rows_by_type['Memo']['reference'], self.booking.reference)
        self.assertEqual(rows_by_type['Memo']['amount'], self.memo.total())
        self.assertTrue(rows_by_type['Memo']['is_charge'])

    def test_never_shows_another_owners_rows(self):
        other_property = Property.objects.create(
            title='Other Payouts Property', short_title='OTHPAYOUT', owner=self.other_owner,
            cleaning_company=self.company, booking_company=self.company, standard_cleaning_fee=Decimal('80.00'),
        )
        PropertySpec.objects.create(property=other_property, bedrooms=2)
        other_guest = Guest.objects.create(first_name='Other', last_name='Pay', email='other-payouts-guest@example.com')
        other_booking = Booking.objects.create(
            property=other_property, guest=other_guest,
            arrival_date=self.today + timedelta(days=10), departure_date=self.today + timedelta(days=14),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=other_booking, basic_rental=Decimal('300.00'))
        Departure.objects.create(booking=other_booking, clean=True)
        self.client.login(username='payoutsstaff', password='pw')
        self.client.post(reverse('staff:finance_payout_mark_paid', kwargs={'reference': other_booking.reference}))
        other_memo = Memo.objects.get(property=other_property)
        self.client.post(reverse('staff:finance_memo_send', kwargs={'pk': other_memo.pk}))
        self.client.logout()

        self.client.login(username='payoutsowner', password='pw')
        response = self.client.get(reverse('owners:payouts_memos'))
        references = {row['reference'] for row in response.context['rows']}
        self.assertNotIn(other_booking.reference, references)

    def test_payout_detail_url_scopes_to_the_single_booking_for_a_regularly_paid_owner(self):
        self.owner.is_paid_regularly = True
        self.owner.save(update_fields=['is_paid_regularly'])
        self.client.login(username='payoutsowner', password='pw')
        response = self.client.get(reverse('owners:payouts_memos'))
        payout_row = next(row for row in response.context['rows'] if row['type'] == 'Payout')
        self.assertIn(f"start={self.booking.arrival_date.isoformat()}", payout_row['detail_url'])
        self.assertIn(f"end={self.booking.departure_date.isoformat()}", payout_row['detail_url'])

    def test_payout_detail_url_scopes_to_the_arrival_month_for_a_non_regular_owner(self):
        self.client.login(username='payoutsowner', password='pw')
        response = self.client.get(reverse('owners:payouts_memos'))
        payout_row = next(row for row in response.context['rows'] if row['type'] == 'Payout')
        month_start = self.booking.arrival_date.replace(day=1)
        self.assertIn(f"start={month_start.isoformat()}", payout_row['detail_url'])
        self.assertNotIn(f"start={self.booking.arrival_date.isoformat()}", payout_row['detail_url'])

    def test_memo_detail_shows_breakdown_and_total(self):
        self.client.login(username='payoutsowner', password='pw')
        response = self.client.get(reverse('owners:memo_detail', kwargs={'pk': self.memo.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['memo'], self.memo)

    def test_memo_detail_404s_for_another_owners_memo(self):
        self.client.login(username='otherpayoutsowner', password='pw')
        response = self.client.get(reverse('owners:memo_detail', kwargs={'pk': self.memo.pk}))
        self.assertEqual(response.status_code, 404)

    def test_memo_detail_404s_for_an_unsent_memo(self):
        unsent_booking = Booking.objects.create(
            property=self.property, guest=self.booking.guest,
            arrival_date=self.today + timedelta(days=20), departure_date=self.today + timedelta(days=24),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=unsent_booking, basic_rental=Decimal('300.00'))
        Departure.objects.create(booking=unsent_booking, clean=True)
        unsent_memo = Memo.objects.get(property=self.property, cleaning_task__booking=unsent_booking)

        self.client.login(username='payoutsowner', password='pw')
        response = self.client.get(reverse('owners:memo_detail', kwargs={'pk': unsent_memo.pk}))
        self.assertEqual(response.status_code, 404)
