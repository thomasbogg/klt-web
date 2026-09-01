from datetime import date, time, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

import requests
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bookings.models import (
    AirportTransfer, AirportTransferDirection, Arrival, BalancePayment, Booking, BookingCondition,
    BookingSettings, Charge, CheckinSettings, Departure, Extra, FAQ, Payment, PaymentSettings,
    PlatformPayout, TravelMethod,
)
from finance.models import AdHocService
from guests.models import Guest
from properties.models import (
    Accountant, Amenity, Location, LocationImage, LocationRules, LocationSpec, ManagementCompany,
    Owner, Platform, Price, Property, PropertyImage, PropertyOwnership, PropertyPlatformID,
    PropertySpec, SEFDetail, WashingMaterial, iCalLink,
)
from staff.models import Checkin, CleaningTask, Deduction, OwnerPayment, StaffProfile, StaffRole, TaskHistoryEntry
from staff.monthly_reports import (
    bookings_trend_rows, commissions_trend_rows, extras_trend_rows, location_groups,
    management_trend_rows, monthly_bookings_rows, monthly_commissions_rows, monthly_extras_rows,
    monthly_management_rows, monthly_revenue_rows, monthly_stays_rows, revenue_trend_rows,
    stays_trend_rows,
)
from staff.reports import booking_report_rows, report_totals
from staff.utils import (
    apply_manual_checkin_time, apply_manual_task_date, booking_stage, checkin_valid_range,
    cleaning_task_valid_range, compute_arrival_eta, next_step_hint, status_bucket,
)

User = get_user_model()


def make_owner(**overrides):
    defaults = dict(
        name='Test Owner', email='owner@example.com', default_clean=False, default_meet_greet=False,
        takes_euros=True, takes_pounds=False, cleans_are_invoiced=False,
        rental_commissions_are_invoiced=False, is_paid_regularly=True,
    )
    defaults.update(overrides)
    return Owner.objects.create(**defaults)


def make_accountant(**overrides):
    defaults = dict(
        company='Test Accounting Co', name='Accountant Person', email='accountant@example.com',
        phone='+351900000005',
    )
    defaults.update(overrides)
    return Accountant.objects.create(**defaults)


def make_management_company(**overrides):
    defaults = dict(name='Test Management Co')
    defaults.update(overrides)
    return ManagementCompany.objects.create(**defaults)


def make_location(**overrides):
    defaults = dict(
        title='Test Location', street='1 Test Street', zip_code='8000-000', city='Faro',
        coordinates='37.0,-7.9', map_link='https://maps.example.com/test',
    )
    defaults.update(overrides)
    return Location.objects.create(**defaults)


class StaffAuthGateTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Staff Auth Property', short_title='STAFFAUTH')
        self.guest = Guest.objects.create(first_name='Auth', last_name='Guest', email='staff-auth@example.com')
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest,
            arrival_date=date.today() + timedelta(days=50), departure_date=date.today() + timedelta(days=57),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.detail_url = reverse('staff:booking_detail', kwargs={'reference': self.booking.reference})
        self.lookup_url = reverse('staff:booking_lookup')

    def test_anonymous_redirected_to_admin_login(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

    def test_non_staff_user_redirected_to_admin_login(self):
        User.objects.create_user(username='notstaff', password='pw', is_staff=False)
        self.client.login(username='notstaff', password='pw')
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

    def test_staff_user_can_view(self):
        User.objects.create_user(username='areal_staffer', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='areal_staffer', password='pw')
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)

    def test_lookup_view_also_gated(self):
        response = self.client.get(self.lookup_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

    def test_home_also_gated(self):
        response = self.client.get(reverse('staff:home'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

    def test_guest_list_also_gated(self):
        response = self.client.get(reverse('staff:guest_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

    def test_guest_detail_also_gated(self):
        response = self.client.get(reverse('staff:guest_detail', kwargs={'pk': self.guest.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

    def test_property_list_also_gated(self):
        response = self.client.get(reverse('staff:property_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

    def test_property_detail_also_gated(self):
        response = self.client.get(reverse('staff:property_detail', kwargs={'pk': self.property.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)


class StaffRolePermissionTests(TestCase):
    """staff.permissions.staff_page_required + the Settings > Staff/Roles superuser carve-out -
    see StaffRole/StaffProfile (staff/models.py) and STAFF_PAGE_PERMISSION_FIELDS (staff/utils.py).
    Page-level, visibility-only, one role per user - see the design plan this was built from."""

    def setUp(self):
        self.role = StaffRole.objects.create(name='Guests only', can_view_guests=True)
        self.role_less_user = User.objects.create_user(username='roleless', password='pw', is_staff=True)
        self.guests_user = User.objects.create_user(username='guestsonly', password='pw', is_staff=True)
        StaffProfile.objects.create(user=self.guests_user, role=self.role)
        self.superuser = User.objects.create_user(
            username='rolessuperuser', password='pw', is_staff=True, is_superuser=True,
        )

    def test_role_less_staff_user_gets_403_on_every_page(self):
        self.client.login(username='roleless', password='pw')
        for url in (reverse('staff:home'), reverse('staff:guest_list'), reverse('staff:property_list'),
                    reverse('staff:location_list'), reverse('staff:settings')):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_role_grants_only_its_own_pages(self):
        self.client.login(username='guestsonly', password='pw')
        self.assertEqual(self.client.get(reverse('staff:guest_list')).status_code, 200)
        for url in (reverse('staff:home'), reverse('staff:property_list'),
                    reverse('staff:location_list'), reverse('staff:settings')):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_superuser_bypasses_role_entirely(self):
        self.client.login(username='rolessuperuser', password='pw')
        for url in (reverse('staff:home'), reverse('staff:guest_list'), reverse('staff:property_list'),
                    reverse('staff:location_list'), reverse('staff:settings')):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_nav_hides_links_for_a_limited_role(self):
        self.client.login(username='guestsonly', password='pw')
        response = self.client.get(reverse('staff:guest_list'))
        self.assertContains(response, reverse('staff:guest_list'))
        self.assertNotContains(response, reverse('staff:property_list'))
        self.assertNotContains(response, reverse('staff:location_list'))
        self.assertNotContains(response, reverse('staff:settings'))

    def test_can_view_settings_does_not_grant_staff_or_roles_panels(self):
        self.role.can_view_settings = True
        self.role.save()
        self.client.login(username='guestsonly', password='pw')

        response = self.client.get(reverse('staff:settings'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Booking settings')
        self.assertNotContains(response, 'Staff accounts')
        self.assertNotContains(response, 'id="settings-pane-roles"')

        # ?panel= tampering must not leak the panel's HTML either, not just fail to mark it active.
        tampered = self.client.get(reverse('staff:settings') + '?panel=staff')
        self.assertNotContains(tampered, 'Staff accounts')

        # Direct POST of a superuser-only action must be rejected outright, independent of nav/UI.
        post_response = self.client.post(reverse('staff:settings'), {'action': 'add_role', 'name': 'Sneaky'})
        self.assertEqual(post_response.status_code, 403)
        self.assertFalse(StaffRole.objects.filter(name='Sneaky').exists())

    def test_superuser_sees_staff_and_roles_panels(self):
        self.client.login(username='rolessuperuser', password='pw')
        response = self.client.get(reverse('staff:settings'))
        self.assertContains(response, 'Staff accounts')
        self.assertContains(response, 'id="settings-pane-roles"')


class StaffBookingDetailViewTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(username='staffer', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='staffer', password='pw')

        self.property = Property.objects.create(title='Staff Detail Property', short_title='STAFFDET')
        self.other_property = Property.objects.create(title='Other Property', short_title='OTHERPROP')
        self.guest = Guest.objects.create(first_name='Elena', last_name='Costa', email='staff-detail@example.com')
        self.start = date.today() + timedelta(days=100)
        self.end = self.start + timedelta(days=7)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.charge = Charge.objects.create(
            booking=self.booking, basic_rental=Decimal('700.00'), admin=Decimal('38.50'),
            due_at_booking=Decimal('184.63'), due_at_balance=Decimal('553.87'), currency='EUR',
        )
        self.payment = Payment.objects.create(booking=self.booking, provider='revolut', status='paid')
        self.balance_payment = BalancePayment.objects.create(booking=self.booking, provider='revolut')
        self.url = reverse('staff:booking_detail', kwargs={'reference': self.booking.reference})

    def test_unknown_reference_404s(self):
        response = self.client.get(reverse('staff:booking_detail', kwargs={'reference': 'NOPE-NOPE'}))
        self.assertEqual(response.status_code, 404)

    def test_get_context_includes_everything(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['current_stage'], 'Confirmed Booking')
        self.assertEqual(list(response.context['deductions']), [])
        self.assertEqual(list(response.context['task_history']), [])
        self.assertIn('items', response.context['extras'])

    def test_platform_reference_hidden_for_a_direct_booking(self):
        # self.booking's enquiry_source is 'Website' (direct) in this fixture.
        response = self.client.get(self.url)
        self.assertFalse(response.context['is_platform_booking'])
        self.assertNotContains(response, "Platform Reference")

    def test_platform_reference_shown_for_a_platform_booking(self):
        self.booking.enquiry_source = 'Airbnb'
        self.booking.platform_id = 'HMABC12345'
        self.booking.save(update_fields=['enquiry_source', 'platform_id'])
        response = self.client.get(self.url)
        self.assertTrue(response.context['is_platform_booking'])
        self.assertContains(response, "Platform Reference")
        self.assertContains(response, "HMABC12345")

    def test_owner_payout_unavailable_reason_shown_when_property_has_no_owner(self):
        # self.property has no owner assigned in this fixture, so this is the natural "not
        # available" case to exercise on the default setup.
        response = self.client.get(self.url)
        owner_payout = response.context['owner_payout']
        self.assertFalse(owner_payout['available'])
        self.assertEqual(owner_payout['reason'], "Property has no owner assigned.")
        self.assertContains(response, "Property has no owner assigned.")

    def test_owner_payout_panel_shows_computed_figures_when_available(self):
        owner = make_owner()
        self.property.owner = owner
        self.property.save()
        response = self.client.get(self.url)
        owner_payout = response.context['owner_payout']
        self.assertTrue(owner_payout['available'])
        self.assertContains(response, "Owner balance")

    def test_split_mismatch_false_when_subtotal_matches_due_total(self):
        # Fixture: basic_rental(700) + admin(38.50) = 738.50 = due_at_booking(184.63) + due_at_balance(553.87).
        response = self.client.get(self.url)
        self.assertEqual(response.context['subtotal'], Decimal('738.50'))
        self.assertEqual(response.context['due_total'], Decimal('738.50'))
        self.assertFalse(response.context['split_mismatch'])
        self.assertNotContains(response, "recalculate below")

    def test_split_mismatch_true_after_basic_rental_edited_without_recalculating(self):
        self.client.post(self.url, {'action': 'update_booking', 'basic_rental': '750.00'})
        response = self.client.get(self.url)
        self.assertEqual(response.context['subtotal'], Decimal('788.50'))  # 750 + 38.50
        self.assertEqual(response.context['due_total'], Decimal('738.50'))  # untouched
        self.assertTrue(response.context['split_mismatch'])
        self.assertContains(response, "recalculate below")

    def test_recalculate_payment_split_with_paid_deposit_only_moves_due_at_balance(self):
        self.client.post(self.url, {'action': 'update_booking', 'basic_rental': '750.00'})
        response = self.client.post(self.url, {'action': 'recalculate_payment_split'})
        self.assertRedirects(response, self.url)
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.due_at_booking, Decimal('184.63'))  # frozen - deposit already paid
        self.assertEqual(self.charge.due_at_balance, Decimal('603.87'))  # 788.50 - 184.63

    def test_recalculate_payment_split_with_unpaid_deposit_recomputes_both(self):
        # Uses split_subtotal(), not compute_costs() - the subtotal (788.50) already has admin
        # baked in, and compute_costs() would derive its own fresh admin fee on top of it and
        # double-count. See BookingSettings.split_subtotal()'s own docstring.
        self.payment.status = 'pending'
        self.payment.save(update_fields=['status'])
        self.client.post(self.url, {'action': 'update_booking', 'basic_rental': '750.00'})
        response = self.client.post(self.url, {'action': 'recalculate_payment_split'})
        self.assertRedirects(response, self.url)
        self.charge.refresh_from_db()
        due_at_booking, due_at_balance, _balance_due_date = BookingSettings.load().split_subtotal(
            Decimal('788.50'), arrival_date=self.start
        )
        self.assertEqual(self.charge.due_at_booking, due_at_booking)
        self.assertEqual(self.charge.due_at_balance, due_at_balance)

    def test_recalculate_payment_split_without_a_charge_is_a_noop(self):
        self.charge.delete()
        response = self.client.post(self.url, {'action': 'recalculate_payment_split'})
        self.assertRedirects(response, self.url)

    def test_clearing_discount_and_extra_guest_actually_clears_them(self):
        self.charge.discount_total = Decimal('10.00')
        self.charge.extra_guest_total = Decimal('1.00')
        self.charge.save(update_fields=['discount_total', 'extra_guest_total'])
        response = self.client.post(self.url, {
            'action': 'update_booking', 'discount_total': '', 'extra_guest_total': '',
        })
        self.assertRedirects(response, self.url)
        self.charge.refresh_from_db()
        self.assertIsNone(self.charge.discount_total)
        self.assertIsNone(self.charge.extra_guest_total)

    def test_blank_basic_rental_leaves_it_untouched(self):
        # Unlike discount_total/extra_guest_total, the other Charge fields keep the existing
        # "blank means don't touch" safety net against an accidental empty submit.
        response = self.client.post(self.url, {'action': 'update_booking', 'basic_rental': ''})
        self.assertRedirects(response, self.url)
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.basic_rental, Decimal('700.00'))

    def test_update_booking_saves_fields_across_every_panel_at_once(self):
        # Regression guard for the whole point of the merge: one POST, one action, and every
        # panel's fields land together - see StaffBookingDetailView._update_booking's docstring.
        response = self.client.post(self.url, {
            'action': 'update_booking', 'property': self.other_property.pk,
            'arrival_date': (self.start + timedelta(days=1)).isoformat(),
            'departure_date': (self.end + timedelta(days=1)).isoformat(),
            'adults': '3', 'children': '1', 'babies': '0', 'is_owner': 'on',
            'first_name': 'Elena', 'last_name': 'Costa-Silva', 'email': 'new-email@example.com',
            'phone': '999888777',
            'currency': 'GBP', 'basic_rental': '750.00', 'admin': '41.25', 'security': '200.00',
            'due_at_booking': '190.00', 'due_at_balance': '600.00',
            'enquiry_status': 'Hold expired',
            'payment_status': 'paid', 'balance_payment_status': 'in_progress',
        })
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.guest.refresh_from_db()
        self.charge.refresh_from_db()
        self.payment.refresh_from_db()
        self.balance_payment.refresh_from_db()

        self.assertEqual(self.booking.property, self.other_property)
        self.assertEqual(self.booking.arrival_date, self.start + timedelta(days=1))
        self.assertEqual(self.booking.adults, 3)
        self.assertTrue(self.booking.is_owner)
        self.assertEqual(self.booking.enquiry_status, 'Hold expired')
        self.assertEqual(self.guest.last_name, 'Costa-Silva')
        self.assertEqual(self.guest.email, 'new-email@example.com')
        self.assertEqual(self.charge.basic_rental, Decimal('750.00'))
        self.assertEqual(self.charge.currency, 'GBP')
        self.assertEqual(self.payment.status, 'paid')
        self.assertEqual(self.balance_payment.status, 'in_progress')

    def test_update_booking_saves_discount_and_extra_guest_and_total_rental_reflects_them(self):
        response = self.client.post(self.url, {
            'action': 'update_booking', 'basic_rental': '700.00',
            'discount_total': '70.00', 'extra_guest_total': '35.00', 'admin': '38.50',
        })
        self.assertRedirects(response, self.url)
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.discount_total, Decimal('70.00'))
        self.assertEqual(self.charge.extra_guest_total, Decimal('35.00'))
        self.assertEqual(self.charge.total_rental, Decimal('665.00'))  # 700 - 70 + 35

        response = self.client.get(self.url)
        self.assertContains(response, 'Total Rental')
        self.assertContains(response, '665.00')

    def test_update_booking_only_touches_fields_actually_submitted(self):
        # A minimal POST (as if only the Enquiry data panel's inputs existed) must leave every
        # other panel's data untouched - each field is still independently optional server-side,
        # even though every field now shares one form/button client-side.
        response = self.client.post(self.url, {
            'action': 'update_booking', 'enquiry_status': 'Hold expired',
        })
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.guest.refresh_from_db()
        self.charge.refresh_from_db()
        self.assertEqual(self.booking.enquiry_status, 'Hold expired')
        self.assertEqual(self.booking.arrival_date, self.start)  # untouched
        self.assertEqual(self.booking.adults, 2)  # untouched
        self.assertEqual(self.guest.last_name, 'Costa')  # untouched
        self.assertEqual(self.charge.basic_rental, Decimal('700.00'))  # untouched

    def test_update_booking_ignores_enquiry_source_and_date_even_if_posted(self):
        # Source/date of enquiry are read-only display fields now, not editable - a POST that
        # somehow still includes them (a stray field, a tampered form) must have no effect.
        response = self.client.post(self.url, {
            'action': 'update_booking', 'enquiry_source': 'Phone',
            'enquiry_date': (date.today() - timedelta(days=5)).isoformat(),
        })
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.enquiry_source, 'Website')  # untouched, from setUp
        self.assertEqual(self.booking.enquiry_date, None)  # untouched, from setUp

    def test_update_booking_rejects_an_unrecognised_enquiry_status(self):
        # The dropdown only ever offers ENQUIRY_STATUSES - a submitted value outside that list
        # (client tampering, since the <select> itself can't produce one) is silently ignored
        # rather than let a fresh typo/garbage value back in as free text again.
        response = self.client.post(self.url, {
            'action': 'update_booking', 'enquiry_status': 'Made Up Status',
        })
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.enquiry_status, 'Booking confirmed')  # untouched, from setUp

    def test_update_booking_keeps_an_already_unrecognised_status_if_resubmitted_unchanged(self):
        # The dropdown renders the booking's own current value as a fallback option when it isn't
        # one of the known statuses (a real booking already drifted to 'Booking cancelled') -
        # resubmitting that same value back must not be treated as a rejected/tampered value.
        self.booking.enquiry_status = 'Booking cancelled'
        self.booking.save(update_fields=['enquiry_status'])
        response = self.client.post(self.url, {
            'action': 'update_booking', 'enquiry_status': 'Booking cancelled',
        })
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.enquiry_status, 'Booking cancelled')

    def test_get_context_includes_enquiry_status_groups(self):
        response = self.client.get(self.url)
        groups = dict(response.context['enquiry_status_groups'])
        self.assertIn('Booking confirmed', groups['Valid'])
        self.assertIn('Awaiting payment', groups['Provisional'])
        self.assertIn('Cancelled by staff', groups['Closed'])

    def test_update_booking_rejects_overlap(self):
        Booking.objects.create(
            property=self.property, guest=self.guest,
            arrival_date=self.start + timedelta(days=30), departure_date=self.end + timedelta(days=30),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        response = self.client.post(self.url, {
            'action': 'update_booking',
            'arrival_date': (self.start + timedelta(days=30)).isoformat(),
            'departure_date': (self.end + timedelta(days=30)).isoformat(),
            'adults': '2', 'children': '0', 'babies': '0',
        })
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.arrival_date, self.start)  # untouched

    def test_update_booking_rejects_party_over_max_guests(self):
        PropertySpec.objects.create(property=self.property, max_guests=4)
        response = self.client.post(self.url, {
            'action': 'update_booking', 'adults': '2', 'children': '3', 'babies': '1',
        })
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.adults, 2)  # untouched (was 2 already, but children/babies too)
        self.assertEqual(self.booking.children, 0)

    def test_update_booking_allows_party_over_max_guests_when_property_has_no_specs(self):
        # No PropertySpec row for self.property - can't enforce a cap that isn't known.
        response = self.client.post(self.url, {
            'action': 'update_booking', 'adults': '2', 'children': '3', 'babies': '1',
        })
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.children, 3)

    def test_update_booking_never_blanks_required_last_name(self):
        self.client.post(self.url, {'action': 'update_booking', 'last_name': ''})
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.last_name, 'Costa')

    def test_update_booking_rejects_invalid_amount_and_saves_nothing_else(self):
        # The invalid-amount error is caught after full_clean() but before the atomic save block,
        # so a bad charge amount must block the whole combined save, not just the charge fields.
        response = self.client.post(self.url, {
            'action': 'update_booking', 'basic_rental': 'not-a-number', 'currency': 'EUR',
            'first_name': 'Should Not Save',
        })
        self.assertRedirects(response, self.url)
        self.charge.refresh_from_db()
        self.guest.refresh_from_db()
        self.assertEqual(self.charge.basic_rental, Decimal('700.00'))  # untouched
        self.assertEqual(self.guest.first_name, 'Elena')  # untouched

    def test_update_booking_logs_task_history_for_status_change_and_charges_change(self):
        response = self.client.post(self.url, {
            'action': 'update_booking', 'enquiry_status': 'Hold expired', 'basic_rental': '750.00',
        })
        self.assertRedirects(response, self.url)
        entries = list(TaskHistoryEntry.objects.filter(booking=self.booking).order_by('pk'))
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].description, 'Rental charges updated')
        self.assertEqual(entries[0].created_by, self.staff_user)
        self.assertEqual(entries[1].description, 'Status changed')
        self.assertIn("'Booking confirmed' to 'Hold expired'", entries[1].detail)

    def test_update_booking_no_task_entries_when_nothing_relevant_changed(self):
        self.client.post(self.url, {'action': 'update_booking', 'enquiry_status': 'Booking confirmed'})
        self.assertEqual(TaskHistoryEntry.objects.filter(booking=self.booking).count(), 0)

    def test_update_booking_logs_task_history_for_a_date_change(self):
        new_arrival = self.start + timedelta(days=1)
        response = self.client.post(self.url, {
            'action': 'update_booking', 'arrival_date': new_arrival.isoformat(),
            'departure_date': self.end.isoformat(),
        })
        self.assertRedirects(response, self.url)
        entry = TaskHistoryEntry.objects.get(booking=self.booking)
        self.assertEqual(entry.description, 'Booking dates updated')
        self.assertEqual(entry.created_by, self.staff_user)
        self.assertIn(f"Arrival {self.start} → {new_arrival}", entry.detail)

    def test_update_booking_no_date_history_entry_when_dates_unchanged(self):
        self.client.post(self.url, {
            'action': 'update_booking', 'arrival_date': self.start.isoformat(),
            'departure_date': self.end.isoformat(),
        })
        self.assertEqual(TaskHistoryEntry.objects.filter(booking=self.booking).count(), 0)

    def test_update_booking_on_a_non_owner_booking_never_touches_clean_or_meet_greet(self):
        # Confirmed live 2026-08-27: saving any other field (here, just the departure date) on a
        # normal booking was silently resetting Departure.clean to False and vanishing its
        # CleaningTask from the cleaning calendar. Cause: arrival_departure.js disables every
        # field inside the Owner-booking-conditional row while it's collapsed, so a disabled
        # checkbox is omitted from the POST body entirely (HTML form spec) - post.get('clean')
        # then reads as absent/unchecked regardless of the real value. _update_booking() must only
        # write these two fields when is_owner is actually set in this exact submission.
        Departure.objects.create(booking=self.booking, clean=True)
        Arrival.objects.create(booking=self.booking, meet_greet=True)
        new_departure = self.end + timedelta(days=1)
        response = self.client.post(self.url, {
            'action': 'update_booking', 'arrival_date': self.start.isoformat(),
            'departure_date': new_departure.isoformat(),
        })
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertTrue(self.booking.departure.clean)
        self.assertTrue(self.booking.arrival.meet_greet)

    def test_update_booking_on_an_owner_booking_does_set_clean_and_meet_greet(self):
        Departure.objects.create(booking=self.booking, clean=True)
        response = self.client.post(self.url, {
            'action': 'update_booking', 'is_owner': 'on', 'meet_greet': 'on', 'clean': 'off',
        })
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertTrue(self.booking.arrival.meet_greet)
        self.assertFalse(self.booking.departure.clean)

    def test_update_booking_moving_departure_date_does_not_false_positive_on_the_stale_turnover_date(self):
        # Confirmed live 2026-08-27: changing just the departure date raised "That date is outside
        # this task's valid window." - booking.save()'s post_save signal auto-advances the
        # (non-manually-scheduled) turnover task's date to match the new departure_date before
        # _update_cleaning_tasks() runs, so the *old* date still sitting in the posted
        # turnover_date field (nobody touched it - the booking's own dates changed instead) looked
        # like a deliberate edit back to a now-invalid date. See _update_cleaning_tasks's docstring.
        Departure.objects.create(booking=self.booking, clean=True)
        stale_turnover_date = self.booking.cleaning_tasks.get(task_type='turnover').date
        new_departure = self.end + timedelta(days=3)
        response = self.client.post(self.url, {
            'action': 'update_booking', 'departure_date': new_departure.isoformat(),
            'turnover_date': stale_turnover_date.isoformat(),
        }, follow=True)
        messages = [str(m) for m in response.context['messages']]
        self.assertFalse(any("outside this task's valid window" in m for m in messages))
        task = self.booking.cleaning_tasks.get(task_type='turnover')
        self.assertEqual(task.date, new_departure)
        self.assertFalse(task.manually_scheduled)

    def test_add_deduction(self):
        response = self.client.post(self.url, {
            'action': 'add_deduction', 'description': 'Broken glass', 'amount': '25.00',
        })
        self.assertRedirects(response, self.url)
        deduction = Deduction.objects.get(booking=self.booking)
        self.assertEqual(deduction.description, 'Broken glass')
        self.assertEqual(deduction.amount, Decimal('25.00'))

    def test_add_deduction_requires_description_and_amount(self):
        self.client.post(self.url, {'action': 'add_deduction', 'description': '', 'amount': '25.00'})
        self.assertFalse(Deduction.objects.filter(booking=self.booking).exists())

    def test_add_owner_payment(self):
        response = self.client.post(self.url, {
            'action': 'add_owner_payment', 'amount': '400.00', 'note': 'August payout',
        })
        self.assertRedirects(response, self.url)
        payment = OwnerPayment.objects.get(booking=self.booking)
        self.assertEqual(payment.amount, Decimal('400.00'))
        self.assertEqual(payment.note, 'August payout')

    def test_add_owner_payment_requires_a_valid_amount(self):
        response = self.client.post(self.url, {'action': 'add_owner_payment', 'amount': ''})
        self.assertRedirects(response, self.url)
        self.assertFalse(OwnerPayment.objects.filter(booking=self.booking).exists())

    def test_delete_owner_payment(self):
        payment = OwnerPayment.objects.create(booking=self.booking, amount=Decimal('50.00'), note='to remove')
        response = self.client.post(self.url, {'action': 'delete_owner_payment', 'payment_id': payment.pk})
        self.assertRedirects(response, self.url)
        self.assertFalse(OwnerPayment.objects.filter(pk=payment.pk).exists())

    def test_delete_owner_payment_is_scoped_to_this_booking(self):
        other_booking = Booking.objects.create(
            property=self.other_property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        other_payment = OwnerPayment.objects.create(booking=other_booking, amount=Decimal('10.00'))
        response = self.client.post(self.url, {'action': 'delete_owner_payment', 'payment_id': other_payment.pk})
        self.assertRedirects(response, self.url)
        self.assertTrue(OwnerPayment.objects.filter(pk=other_payment.pk).exists())

    def test_add_task_note(self):
        response = self.client.post(self.url, {'action': 'add_task_note', 'description': 'Called guest re: extras'})
        self.assertRedirects(response, self.url)
        entry = TaskHistoryEntry.objects.get(booking=self.booking)
        self.assertEqual(entry.description, 'Note added')
        self.assertEqual(entry.detail, 'Called guest re: extras')
        self.assertEqual(entry.created_by, self.staff_user)

    def test_unknown_action_is_a_noop(self):
        response = self.client.post(self.url, {'action': 'not_a_real_action'})
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.arrival_date, self.start)

    def test_cancel_booking_sets_status_and_logs_task_history(self):
        response = self.client.post(self.url, {'action': 'cancel_booking'})
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.enquiry_status, 'Cancelled by staff')
        entry = TaskHistoryEntry.objects.get(booking=self.booking)
        self.assertEqual(entry.description, 'Status changed')
        self.assertIn("'Cancelled by staff'", entry.detail)
        self.assertEqual(entry.created_by, self.staff_user)

    def test_cancel_booking_frees_the_calendar(self):
        self.client.post(self.url, {'action': 'cancel_booking'})
        self.assertFalse(
            Booking.objects.overlapping(self.property, self.start, self.end).filter(pk=self.booking.pk).exists()
        )

    def test_cancel_booking_on_an_already_closed_booking_is_a_noop(self):
        self.booking.enquiry_status = 'Cancelled by guest'
        self.booking.save(update_fields=['enquiry_status'])
        self.client.post(self.url, {'action': 'cancel_booking'})
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.enquiry_status, 'Cancelled by guest')  # untouched, not overwritten
        self.assertFalse(TaskHistoryEntry.objects.filter(booking=self.booking).exists())

    def test_context_can_cancel_flag(self):
        response = self.client.get(self.url)
        self.assertTrue(response.context['can_cancel'])

        self.booking.enquiry_status = 'Cancelled by platform'
        self.booking.save(update_fields=['enquiry_status'])
        response = self.client.get(self.url)
        self.assertFalse(response.context['can_cancel'])

    def test_uncancel_booking_revives_a_staff_cancellation(self):
        self.booking.enquiry_status = 'Cancelled by staff'
        self.booking.save(update_fields=['enquiry_status'])
        response = self.client.post(self.url, {'action': 'uncancel_booking'})
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.enquiry_status, 'Booking confirmed')
        entry = TaskHistoryEntry.objects.get(booking=self.booking)
        self.assertEqual(entry.description, 'Status changed')
        self.assertIn("'Cancelled by staff' to 'Booking confirmed'", entry.detail)

    def test_uncancel_booking_revives_a_guest_cancellation(self):
        self.booking.enquiry_status = 'Cancelled by guest'
        self.booking.save(update_fields=['enquiry_status'])
        self.client.post(self.url, {'action': 'uncancel_booking'})
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.enquiry_status, 'Booking confirmed')

    def test_uncancel_booking_refuses_a_platform_cancellation(self):
        self.booking.enquiry_status = 'Cancelled by platform'
        self.booking.save(update_fields=['enquiry_status'])
        self.client.post(self.url, {'action': 'uncancel_booking'})
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.enquiry_status, 'Cancelled by platform')  # untouched

    def test_uncancel_booking_refuses_a_still_confirmed_booking(self):
        # Nothing to revive - enquiry_status is already 'Booking confirmed' from setUp.
        self.client.post(self.url, {'action': 'uncancel_booking'})
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.enquiry_status, 'Booking confirmed')
        self.assertFalse(TaskHistoryEntry.objects.filter(booking=self.booking).exists())

    def test_uncancel_booking_blocked_by_a_new_overlapping_booking(self):
        self.booking.enquiry_status = 'Cancelled by staff'
        self.booking.save(update_fields=['enquiry_status'])
        other_guest = Guest.objects.create(last_name='Took The Dates')
        Booking.objects.create(
            property=self.property, guest=other_guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.client.post(self.url, {'action': 'uncancel_booking'})
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.enquiry_status, 'Cancelled by staff')  # untouched

    def test_context_can_uncancel_flag(self):
        response = self.client.get(self.url)
        self.assertFalse(response.context['can_uncancel'])

        self.booking.enquiry_status = 'Cancelled by staff'
        self.booking.save(update_fields=['enquiry_status'])
        response = self.client.get(self.url)
        self.assertTrue(response.context['can_uncancel'])

    def test_dismiss_freshen_action_dismisses_pending_task(self):
        task = CleaningTask.objects.create(
            booking=self.booking, task_type='freshen', date=self.start - timedelta(days=1),
        )
        response = self.client.post(self.url, {'action': 'dismiss_freshen'})
        self.assertRedirects(response, self.url)
        task.refresh_from_db()
        self.assertEqual(task.status, 'dismissed')
        self.assertEqual(task.dismissed_reason, 'manual')
        self.assertEqual(task.dismissed_by, self.staff_user)

    def test_dismiss_freshen_action_with_no_freshen_task_is_a_noop(self):
        response = self.client.post(self.url, {'action': 'dismiss_freshen'})
        self.assertRedirects(response, self.url)
        self.assertFalse(CleaningTask.objects.filter(booking=self.booking, task_type='freshen').exists())

    def test_undo_dismiss_freshen_action_restores_a_dismissed_task(self):
        task = CleaningTask.objects.create(
            booking=self.booking, task_type='freshen', date=self.start - timedelta(days=1),
            status='dismissed', dismissed_by=self.staff_user, dismissed_at=timezone.now(),
            dismissed_reason='manual',
        )
        response = self.client.post(self.url, {'action': 'undo_dismiss_freshen'})
        self.assertRedirects(response, self.url)
        task.refresh_from_db()
        self.assertEqual(task.status, 'pending')
        self.assertIsNone(task.dismissed_by)
        self.assertIsNone(task.dismissed_at)
        self.assertEqual(task.dismissed_reason, '')

    def test_undo_dismiss_freshen_action_on_a_pending_task_is_a_noop(self):
        task = CleaningTask.objects.create(
            booking=self.booking, task_type='freshen', date=self.start - timedelta(days=1),
        )
        self.client.post(self.url, {'action': 'undo_dismiss_freshen'})
        task.refresh_from_db()
        self.assertEqual(task.status, 'pending')


class StaffBookingDetailArrivalDepartureTests(TestCase):
    """The Arrival/Departure panels + Booking Info's self_check_in/meet_greet/clean checkboxes -
    added 2026-08-25 (see StaffBookingDetailView._update_booking()'s docstring)."""

    def setUp(self):
        self.staff_user = User.objects.create_user(username='staffer2', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='staffer2', password='pw')
        self.property = Property.objects.create(title='Staff AD Property', short_title='STAFFAD')
        self.guest = Guest.objects.create(first_name='Rui', last_name='Nunes', email='staff-ad@example.com')
        self.start = date.today() + timedelta(days=100)
        self.end = self.start + timedelta(days=7)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.url = reverse('staff:booking_detail', kwargs={'reference': self.booking.reference})

    def test_get_context_includes_travel_method_choices(self):
        response = self.client.get(self.url)
        self.assertIn(('flight_faro', 'Flight to Faro'), response.context['arrival_travel_methods'])
        self.assertIn(('flight_faro', 'Flight from Faro'), response.context['departure_travel_methods'])

    def test_update_booking_creates_arrival_and_departure_from_scratch(self):
        self.assertFalse(hasattr(self.booking, 'arrival'))
        self.assertFalse(hasattr(self.booking, 'departure'))
        response = self.client.post(self.url, {
            'action': 'update_booking', 'is_owner': 'on',
            'arrival_method': 'flight_lisbon', 'arrival_flight_number': 'TP1234',
            'arrival_time': '14:00', 'arrival_details': 'Renting a car', 'arrival_hiring_car': 'on',
            'departure_method': 'driving', 'departure_travelling_from': 'Lisbon',
            'departure_time': '09:00',
            'self_check_in': 'on', 'meet_greet': 'on', 'clean': 'on',
        })
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.arrival.method, 'flight_lisbon')
        self.assertEqual(self.booking.arrival.flight_number, 'TP1234')
        self.assertEqual(self.booking.arrival.time, time(14, 0))
        self.assertTrue(self.booking.arrival.hiring_car)
        self.assertTrue(self.booking.arrival.self_check_in)
        self.assertTrue(self.booking.arrival.meet_greet)
        self.assertEqual(self.booking.departure.method, 'driving')
        self.assertEqual(self.booking.departure.travelling_from, 'Lisbon')
        self.assertTrue(self.booking.departure.clean)

    def test_arrival_departure_change_logs_an_update_history_entry(self):
        response = self.client.post(self.url, {
            'action': 'update_booking', 'arrival_method': 'flight_faro', 'arrival_flight_number': 'TP1234',
        })
        self.assertRedirects(response, self.url)
        entry = TaskHistoryEntry.objects.get(booking=self.booking)
        self.assertEqual(entry.description, "Arrival/Departure updated")
        self.assertEqual(entry.created_by, self.staff_user)
        # 2026-08-27, per Thomas: this entry used to have no detail at all, reading as unexplained
        # "mixed" noise next to Booking dates updated's clear before/after - see _field_changes.
        self.assertIn("Flight number", entry.detail)
        self.assertIn("to 'TP1234'", entry.detail)

    def test_update_history_row_shows_stub_with_hover_tooltip(self):
        TaskHistoryEntry.objects.create(
            booking=self.booking, description="Status changed",
            detail="From 'Booking confirmed' to 'Cancelled by staff'", created_by=self.staff_user,
        )
        response = self.client.get(self.url)
        self.assertContains(response, "staff-info-icon")
        self.assertContains(response, "staff-tooltip")
        self.assertContains(response, "Booking confirmed")
        self.assertContains(response, "Cancelled by staff")
        self.assertContains(response, "staffer2")
        self.assertContains(response, "staff-list-date")  # the row itself still shows the date

    def test_unchanged_resubmit_does_not_log_a_duplicate_entry(self):
        Arrival.objects.create(
            booking=self.booking, method='flight_faro', flight_number='TP1234',
            self_check_in=False, meet_greet=False,
        )
        Departure.objects.create(booking=self.booking, method='flight_faro')
        response = self.client.post(self.url, {
            'action': 'update_booking', 'arrival_method': 'flight_faro', 'arrival_flight_number': 'TP1234',
            'departure_method': 'flight_faro',
        })
        self.assertRedirects(response, self.url)
        self.assertEqual(TaskHistoryEntry.objects.filter(booking=self.booking).count(), 0)

    def test_self_check_in_defaults_to_false_on_first_save(self):
        # Real bug: this used to default to True on a fresh Arrival row - self-check-in is a real
        # ops decision, not something to assume by default.
        response = self.client.post(self.url, {'action': 'update_booking'})
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertFalse(self.booking.arrival.self_check_in)

    def test_update_booking_unchecked_boxes_are_false_not_untouched(self):
        # self_check_in isn't gated behind the Owner-booking row, so it always reflects the
        # checkbox's real state, including "omitted means unchecked". meet_greet/clean live inside
        # that row instead, disabled (and so omitted from POST entirely) whenever is_owner isn't
        # also set in this submission - see test_update_booking_on_a_non_owner_booking_never_
        # touches_clean_or_meet_greet for why they must NOT be reset to False in that case.
        Arrival.objects.create(booking=self.booking, self_check_in=True, meet_greet=True)
        Departure.objects.create(booking=self.booking, clean=True)
        response = self.client.post(self.url, {'action': 'update_booking', 'is_owner': 'on'})
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertFalse(self.booking.arrival.self_check_in)
        self.assertFalse(self.booking.arrival.meet_greet)
        self.assertFalse(self.booking.departure.clean)

    def test_update_booking_rejects_invalid_arrival_flight_number_and_saves_nothing(self):
        response = self.client.post(self.url, {
            'action': 'update_booking', 'arrival_method': 'flight_faro',
            'arrival_flight_number': 'not-a-flight-number', 'first_name': 'Changed',
        })
        self.assertRedirects(response, self.url)
        self.assertFalse(hasattr(self.booking, 'arrival'))
        self.guest.refresh_from_db()
        self.assertNotEqual(self.guest.first_name, 'Changed')

    def test_update_booking_never_resets_manual_date_free_departure(self):
        # Sanity check the manual_date removal - Departure saves cleanly without it.
        response = self.client.post(self.url, {
            'action': 'update_booking', 'departure_method': 'bus', 'departure_time': '10:30',
        })
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.departure.method, 'bus')
        self.assertEqual(self.booking.departure.time, time(10, 30))

    def test_meet_greet_row_present_in_markup_regardless_of_owner_status(self):
        # Visibility is a client-side JS toggle (see arrival_departure.js) - the row is always
        # server-rendered so it works with JS disabled and so the checked state survives a
        # validation-error re-render; this only confirms the server side of that contract.
        response = self.client.get(self.url)
        self.assertContains(response, 'data-owner-row')
        self.assertContains(response, 'Meet &amp; greet')

    def test_cleaning_panel_shows_end_of_stay_clean_fields_once_scheduled(self):
        # 2026-08-27, per Thomas: separate from the "Clean on departure" checkbox itself, which is
        # back where it always lived, alongside Meet & Greet (see the test below) - this is the
        # panel that actually shows the resulting CleaningTask's date/staff/team once one exists.
        Departure.objects.create(booking=self.booking, clean=True)
        response = self.client.get(self.url)
        self.assertContains(response, 'End-of-stay clean')

    def test_cleaning_panel_shows_a_placeholder_with_no_end_of_stay_clean_yet(self):
        # This booking's setUp never creates a Departure row, so there's no CleaningTask for the
        # panel to show - it should say so plainly rather than rendering an empty section.
        response = self.client.get(self.url)
        self.assertContains(response, 'staff-panel-title">Cleaning<')
        self.assertContains(response, 'No end-of-stay clean scheduled yet.')

    def test_clean_on_departure_checkbox_lives_with_meet_and_greet(self):
        # 2026-08-27, per Thomas: restored to its original spot after briefly living in the
        # Cleaning panel - it's the one remaining way to turn off the new default-True end-of-stay
        # clean for a booking that shouldn't get one (e.g. an owner cleaning it themselves).
        response = self.client.get(self.url)
        self.assertContains(response, 'data-owner-row')
        self.assertContains(response, 'Meet &amp; greet')
        self.assertContains(response, 'Clean on departure')

    def test_booking_info_has_no_mid_stay_clean_checkbox(self):
        # 2026-08-27, per Thomas: this was a duplicate on/off control for a guest-selected Extra
        # (see BookingFormMixin._save_extras/_parse_mid_stay_clean) that could silently stomp the
        # guest's real choice with whatever this page happened to last render - see the test below.
        response = self.client.get(self.url)
        self.assertNotContains(response, 'staff-mid-stay-clean')
        self.assertNotContains(response, 'name="mid_stay_clean"')

    def test_update_booking_never_touches_mid_stay_clean(self):
        Extra.objects.create(
            booking=self.booking, mid_stay_clean=True, mid_stay_clean_date=self.start + timedelta(days=2),
        )
        # A stray/replayed mid_stay_clean(_date) field in the POST body must be ignored - it's no
        # longer this form's field to write, precisely so a stale page load can't clobber a newer
        # guest-side change with an unrelated save (e.g. editing the guest's phone number).
        response = self.client.post(self.url, {
            'action': 'update_booking', 'mid_stay_clean': 'off', 'mid_stay_clean_date': '',
        })
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertTrue(self.booking.extras.mid_stay_clean)
        self.assertEqual(self.booking.extras.mid_stay_clean_date, self.start + timedelta(days=2))


class CleaningTaskSyncTests(TestCase):
    """staff/signals.py + staff/utils.py::sync_cleaning_tasks_for_booking() - CleaningTask stays
    in sync with Departure.clean/Extra.mid_stay_clean via post_save signals, not a call embedded
    in one view, so it has to work regardless of which code path saves those models."""

    def setUp(self):
        self.property = Property.objects.create(title='Cleaning Sync Property', short_title='CLEANSYNC')
        self.guest = Guest.objects.create(first_name='Ana', last_name='Silva', email='cleaning-sync@example.com')
        self.start = date.today() + timedelta(days=30)
        self.end = self.start + timedelta(days=10)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_departure_clean_true_creates_turnover_task(self):
        Departure.objects.create(booking=self.booking, clean=True)
        task = CleaningTask.objects.get(booking=self.booking, task_type='turnover')
        self.assertEqual(task.date, self.end)
        self.assertEqual(task.status, 'pending')

    def test_departure_clean_false_creates_no_task(self):
        Departure.objects.create(booking=self.booking, clean=False)
        self.assertFalse(CleaningTask.objects.filter(booking=self.booking, task_type='turnover').exists())

    def test_unchecking_clean_removes_pending_task(self):
        departure = Departure.objects.create(booking=self.booking, clean=True)
        self.assertTrue(CleaningTask.objects.filter(booking=self.booking, task_type='turnover').exists())
        departure.clean = False
        departure.save()
        self.assertFalse(CleaningTask.objects.filter(booking=self.booking, task_type='turnover').exists())

    def test_unchecking_clean_does_not_remove_a_done_task(self):
        departure = Departure.objects.create(booking=self.booking, clean=True)
        task = CleaningTask.objects.get(booking=self.booking, task_type='turnover')
        task.status = 'done'
        task.save()
        departure.clean = False
        departure.save()
        task.refresh_from_db()
        self.assertEqual(task.status, 'done')

    def test_mid_stay_clean_with_date_creates_task(self):
        target = self.start + timedelta(days=3)
        Extra.objects.create(booking=self.booking, mid_stay_clean=True, mid_stay_clean_date=target)
        task = CleaningTask.objects.get(booking=self.booking, task_type='mid_stay')
        self.assertEqual(task.date, target)

    def test_mid_stay_clean_without_date_creates_no_task(self):
        Extra.objects.create(booking=self.booking, mid_stay_clean=True, mid_stay_clean_date=None)
        self.assertFalse(CleaningTask.objects.filter(booking=self.booking, task_type='mid_stay').exists())

    def test_departure_date_change_updates_existing_turnover_task_date(self):
        Departure.objects.create(booking=self.booking, clean=True)
        new_end = self.end + timedelta(days=5)
        self.booking.departure_date = new_end
        self.booking.save()
        task = CleaningTask.objects.get(booking=self.booking, task_type='turnover')
        self.assertEqual(task.date, new_end)

    def test_repeated_saves_do_not_duplicate_tasks(self):
        departure = Departure.objects.create(booking=self.booking, clean=True)
        departure.save()
        departure.save()
        self.assertEqual(CleaningTask.objects.filter(booking=self.booking, task_type='turnover').count(), 1)

    def test_cancelling_a_booking_removes_its_pending_turnover_and_mid_stay_tasks(self):
        # Regression 2026-08-27: cancelling a booking only ever flipped enquiry_status - nothing
        # cleaned up its CleaningTask rows, so a cancelled booking's clean kept showing on the
        # calendar looking exactly like a real one.
        Departure.objects.create(booking=self.booking, clean=True)
        Extra.objects.create(
            booking=self.booking, mid_stay_clean=True, mid_stay_clean_date=self.start + timedelta(days=3),
        )
        self.assertEqual(CleaningTask.objects.filter(booking=self.booking).count(), 2)

        self.booking.enquiry_status = 'Cancelled by staff'
        self.booking.save(update_fields=['enquiry_status'])

        self.assertFalse(CleaningTask.objects.filter(booking=self.booking).exists())

    def test_cancelling_a_booking_does_not_remove_a_done_task(self):
        Departure.objects.create(booking=self.booking, clean=True)
        task = CleaningTask.objects.get(booking=self.booking, task_type='turnover')
        task.status = 'done'
        task.save()

        self.booking.enquiry_status = 'Cancelled by staff'
        self.booking.save(update_fields=['enquiry_status'])

        task.refresh_from_db()
        self.assertEqual(task.status, 'done')

    def test_uncancelling_a_booking_recreates_its_turnover_task(self):
        Departure.objects.create(booking=self.booking, clean=True)
        self.booking.enquiry_status = 'Cancelled by staff'
        self.booking.save(update_fields=['enquiry_status'])
        self.assertFalse(CleaningTask.objects.filter(booking=self.booking).exists())

        self.booking.enquiry_status = 'Booking confirmed'
        self.booking.save(update_fields=['enquiry_status'])

        task = CleaningTask.objects.get(booking=self.booking, task_type='turnover')
        self.assertEqual(task.date, self.end)
        self.assertEqual(task.status, 'pending')

    def test_cleans_on_calendar_false_creates_no_task(self):
        company = make_management_company(name='No Calendar Co', cleans_on_calendar=False)
        self.property.cleaning_company = company
        self.property.save()
        Departure.objects.create(booking=self.booking, clean=True)
        self.assertFalse(CleaningTask.objects.filter(booking=self.booking).exists())

    def test_turning_cleans_on_calendar_off_removes_pending_tasks_on_next_sync(self):
        company = make_management_company(name='No Calendar Co 2', cleans_on_calendar=True)
        self.property.cleaning_company = company
        self.property.save()
        Departure.objects.create(booking=self.booking, clean=True)
        self.assertTrue(CleaningTask.objects.filter(booking=self.booking).exists())

        company.cleans_on_calendar = False
        company.save()
        self.booking.save()  # re-trigger the signal-driven sync, same as any other unrelated save
        self.assertFalse(CleaningTask.objects.filter(booking=self.booking, status='pending').exists())

    def test_cleans_on_calendar_false_does_not_remove_a_done_task(self):
        company = make_management_company(name='No Calendar Co 3', cleans_on_calendar=True)
        self.property.cleaning_company = company
        self.property.save()
        Departure.objects.create(booking=self.booking, clean=True)
        task = CleaningTask.objects.get(booking=self.booking, task_type='turnover')
        task.status = 'done'
        task.save()

        company.cleans_on_calendar = False
        company.save()
        self.booking.save()
        task.refresh_from_db()
        self.assertEqual(task.status, 'done')

    def test_no_cleaning_company_is_unaffected_by_cleans_on_calendar(self):
        # Property.cleaning_company is None here (never set) - the flag only applies when a
        # company is actually tracked.
        Departure.objects.create(booking=self.booking, clean=True)
        self.assertTrue(CleaningTask.objects.filter(booking=self.booking, task_type='turnover').exists())


class FreshenTaskSyncTests(TestCase):
    """staff/utils.py::sync_freshen_tasks_for_property() - the auto-insert/auto-dismiss cascade
    for the 'freshen' task type, driven from the same signals as sync_cleaning_tasks_for_booking()
    above (staff/signals.py) so it stays correct across booking create/cancel/uncancel without a
    dedicated call site."""

    def setUp(self):
        self.company = ManagementCompany.objects.create(name='Freshen Co', freshen_after_days=10)
        self.property = Property.objects.create(
            title='Freshen Property', short_title='FRESHPROP', cleaning_company=self.company,
        )
        self.guest = Guest.objects.create(first_name='Ana', last_name='Silva', email='freshen-sync@example.com')
        self.baseline = date.today() + timedelta(days=30)

    def _make_booking(self, arrival, departure):
        return Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=arrival, departure_date=departure,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def _seed_last_clean(self):
        # A departed booking whose turnover clean gives the property a "last clean" baseline
        # (self.baseline) for the tests below to measure a gap against.
        earlier = self._make_booking(self.baseline - timedelta(days=10), self.baseline)
        Departure.objects.create(booking=earlier, clean=True)
        return earlier

    def test_no_prior_clean_creates_no_freshen_task(self):
        # Nothing to measure a gap against yet - see sync_freshen_tasks_for_property()'s docstring.
        later = self._make_booking(self.baseline, self.baseline + timedelta(days=7))
        self.assertFalse(CleaningTask.objects.filter(booking=later, task_type='freshen').exists())

    def test_cleans_on_calendar_false_creates_no_freshen_task(self):
        self.company.cleans_on_calendar = False
        self.company.save()
        # Manually seed a 'last clean' baseline (bypassing the turnover gate, which
        # cleans_on_calendar also suppresses) so this test isolates the freshen sweep's own gate.
        earlier = self._make_booking(self.baseline - timedelta(days=10), self.baseline)
        CleaningTask.objects.create(booking=earlier, task_type='turnover', date=self.baseline)
        later = self._make_booking(self.baseline + timedelta(days=10), self.baseline + timedelta(days=17))
        self.assertFalse(CleaningTask.objects.filter(booking=later, task_type='freshen').exists())

    def test_gap_meeting_threshold_creates_pending_freshen_task(self):
        self._seed_last_clean()
        later = self._make_booking(self.baseline + timedelta(days=10), self.baseline + timedelta(days=17))
        task = CleaningTask.objects.get(booking=later, task_type='freshen')
        self.assertEqual(task.status, 'pending')
        self.assertEqual(task.date, later.arrival_date - timedelta(days=1))

    def test_gap_below_threshold_creates_no_freshen_task(self):
        self._seed_last_clean()
        later = self._make_booking(self.baseline + timedelta(days=9), self.baseline + timedelta(days=16))
        self.assertFalse(CleaningTask.objects.filter(booking=later, task_type='freshen').exists())

    def test_closing_booking_auto_dismisses_existing_freshen_task(self):
        self._seed_last_clean()
        far = self._make_booking(self.baseline + timedelta(days=10), self.baseline + timedelta(days=17))
        far_task = CleaningTask.objects.get(booking=far, task_type='freshen')
        self.assertEqual(far_task.status, 'pending')

        closing = self._make_booking(self.baseline + timedelta(days=1), self.baseline + timedelta(days=4))
        Departure.objects.create(booking=closing, clean=True)

        far_task.refresh_from_db()
        self.assertEqual(far_task.status, 'dismissed')
        self.assertEqual(far_task.dismissed_reason, 'gap_closed')
        self.assertIsNone(far_task.dismissed_by)

    def test_cancelling_the_closing_booking_reinstates_the_freshen_task(self):
        self._seed_last_clean()
        far = self._make_booking(self.baseline + timedelta(days=10), self.baseline + timedelta(days=17))
        far_task = CleaningTask.objects.get(booking=far, task_type='freshen')
        closing = self._make_booking(self.baseline + timedelta(days=1), self.baseline + timedelta(days=4))
        Departure.objects.create(booking=closing, clean=True)
        far_task.refresh_from_db()
        self.assertEqual(far_task.status, 'dismissed')

        closing.enquiry_status = 'Cancelled by staff'
        closing.save(update_fields=['enquiry_status'])

        far_task.refresh_from_db()
        self.assertEqual(far_task.status, 'pending')
        self.assertEqual(far_task.dismissed_reason, '')
        self.assertIsNone(far_task.dismissed_at)

    def test_manual_dismissal_is_never_reinstated_by_the_sweep(self):
        self._seed_last_clean()
        far = self._make_booking(self.baseline + timedelta(days=10), self.baseline + timedelta(days=17))
        far_task = CleaningTask.objects.get(booking=far, task_type='freshen')
        staffer = User.objects.create_user(username='freshendismisser', password='pw', is_staff=True)
        far_task.status = 'dismissed'
        far_task.dismissed_by = staffer
        far_task.dismissed_at = timezone.now()
        far_task.dismissed_reason = 'manual'
        far_task.save()

        # Re-trigger the sweep - the gap still qualifies, but a manual dismissal must not be
        # silently reinstated.
        far.save()

        far_task.refresh_from_db()
        self.assertEqual(far_task.status, 'dismissed')
        self.assertEqual(far_task.dismissed_reason, 'manual')

    def test_no_cleaning_company_never_creates_a_freshen_task(self):
        self.property.cleaning_company = None
        self.property.save()
        self._seed_last_clean()
        later = self._make_booking(self.baseline + timedelta(days=10), self.baseline + timedelta(days=17))
        self.assertFalse(CleaningTask.objects.filter(booking=later, task_type='freshen').exists())

    def test_cancelling_the_arriving_booking_removes_its_own_pending_freshen_task(self):
        self._seed_last_clean()
        far = self._make_booking(self.baseline + timedelta(days=10), self.baseline + timedelta(days=17))
        self.assertTrue(CleaningTask.objects.filter(booking=far, task_type='freshen', status='pending').exists())

        far.enquiry_status = 'Cancelled by staff'
        far.save(update_fields=['enquiry_status'])

        self.assertFalse(CleaningTask.objects.filter(booking=far, task_type='freshen').exists())

    def test_a_same_day_turnover_counts_as_covering_the_gap(self):
        # Regression: a real production booking's turnover clean had been manually dragged onto
        # the very date the next booking arrives - a normal same-day turnover, per
        # cleaning_task_valid_range's own docstring, not an uncovered gap. The gap query used to
        # be date__lt=arrival_date, which excluded that same-day clean entirely and walked back to
        # a much older clean instead, wrongly inserting a freshen task for an arrival that was
        # already covered.

        # A much older clean the gap query must NOT fall back to once the same-day one below is
        # correctly counted.
        far_earlier = self._make_booking(self.baseline - timedelta(days=40), self.baseline - timedelta(days=35))
        Departure.objects.create(booking=far_earlier, clean=True)

        earlier = self._make_booking(self.baseline - timedelta(days=20), self.baseline - timedelta(days=10))
        Departure.objects.create(booking=earlier, clean=True)
        # Drag onto where the next booking (created below) will arrive - no next confirmed arrival
        # exists yet at this point, so the valid-range ceiling is unbounded and this drag is legal
        # on its own terms, exactly like the real dummy-test-data drag that surfaced this bug.
        turnover = CleaningTask.objects.get(booking=earlier, task_type='turnover')
        apply_manual_task_date(turnover, self.baseline)

        arriving = self._make_booking(self.baseline, self.baseline + timedelta(days=7))
        self.assertFalse(CleaningTask.objects.filter(booking=arriving, task_type='freshen').exists())

    def test_a_freshen_tasks_own_date_is_never_used_as_its_own_last_clean(self):
        # Regression: the gap query didn't exclude the booking's own already-created freshen task,
        # so re-running the sweep (e.g. an unrelated save on the same booking) would see that task
        # itself (dated arrival-minus-1, always inside the window) as "the last clean" and
        # immediately auto-dismiss itself as gap_closed.
        self._seed_last_clean()
        far = self._make_booking(self.baseline + timedelta(days=10), self.baseline + timedelta(days=17))
        task = CleaningTask.objects.get(booking=far, task_type='freshen')
        self.assertEqual(task.status, 'pending')

        far.save()  # re-triggers the sweep with no other state changed

        task.refresh_from_db()
        self.assertEqual(task.status, 'pending')

    def test_dragging_a_turnover_date_retriggers_the_freshen_sweep(self):
        # Regression 2026-08-27: apply_manual_task_date() only ever saved the CleaningTask itself,
        # never Booking/Departure/Extra, so a calendar drag never re-triggered the signals that
        # normally keep Freshen state in sync - confirmed live when dragging a turnover clean
        # later closed a gap for a booking three stays down, and it only took effect because the
        # reconcile management command happened to be run manually afterwards.
        self._seed_last_clean()
        middle = self._make_booking(self.baseline + timedelta(days=5), self.baseline + timedelta(days=8))
        Departure.objects.create(booking=middle, clean=True)
        far = self._make_booking(self.baseline + timedelta(days=20), self.baseline + timedelta(days=27))
        far_task = CleaningTask.objects.get(booking=far, task_type='freshen')
        self.assertEqual(far_task.status, 'pending')

        middle_turnover = CleaningTask.objects.get(booking=middle, task_type='turnover')
        error = apply_manual_task_date(middle_turnover, self.baseline + timedelta(days=15))
        self.assertIsNone(error)

        far_task.refresh_from_db()
        self.assertEqual(far_task.status, 'dismissed')
        self.assertEqual(far_task.dismissed_reason, 'gap_closed')

    def test_dragging_a_freshen_tasks_own_date_does_not_crash(self):
        # Regression: apply_manual_task_date()'s computed_date logic assumed every task was either
        # turnover or mid_stay, so dragging a freshen task fell into the mid_stay branch and tried
        # task.booking.extras.mid_stay_clean_date - a freshen task's booking never has an Extra
        # row, so this would 500.
        self._seed_last_clean()
        far = self._make_booking(self.baseline + timedelta(days=10), self.baseline + timedelta(days=17))
        far_task = CleaningTask.objects.get(booking=far, task_type='freshen')

        error = apply_manual_task_date(far_task, far_task.date + timedelta(days=1))

        self.assertIsNone(error)
        far_task.refresh_from_db()
        self.assertTrue(far_task.manually_scheduled)


class StaffCleaningRotaViewTests(TestCase):
    """StaffCleaningRotaView - superuser sees every task for the date with an assign control; a
    non-superuser with the role sees only their own assigned tasks."""

    def setUp(self):
        self.role = StaffRole.objects.create(name='Cleaner', can_view_cleaning_rota=True, is_cleaning_staff=True)
        self.cleaner = User.objects.create_user(username='cleaner1', password='pw', is_staff=True)
        StaffProfile.objects.create(user=self.cleaner, role=self.role)
        self.other_cleaner = User.objects.create_user(username='cleaner2', password='pw', is_staff=True)
        StaffProfile.objects.create(user=self.other_cleaner, role=self.role)
        self.superuser = User.objects.create_user(
            username='rotasuperuser', password='pw', is_staff=True, is_superuser=True,
        )

        self.property = Property.objects.create(title='Rota Property', short_title='ROTAPROP')
        self.guest = Guest.objects.create(first_name='Joao', last_name='Costa', email='rota@example.com')
        self.today = date.today()
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest,
            arrival_date=self.today - timedelta(days=3), departure_date=self.today,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Departure.objects.create(booking=self.booking, clean=True)
        self.task = CleaningTask.objects.get(booking=self.booking, task_type='turnover')
        self.url = reverse('staff:cleaning_rota')

    def test_role_less_user_gets_403(self):
        User.objects.create_user(username='norole', password='pw', is_staff=True)
        self.client.login(username='norole', password='pw')
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_superuser_sees_task_with_unassigned_status(self):
        self.client.login(username='rotasuperuser', password='pw')
        response = self.client.get(self.url)
        self.assertContains(response, 'Rota Property')
        self.assertContains(response, 'Unassigned')

    def test_assigned_names_shown_read_only(self):
        """Assignment itself now happens on the cleaning calendar (StaffCleaningTaskSaveView) -
        this page only displays who's assigned, for superusers and cleaners alike."""
        self.task.assigned_to.set([self.cleaner])
        self.client.login(username='rotasuperuser', password='pw')
        response = self.client.get(self.url)
        self.assertContains(response, f'Assigned to {self.cleaner.username}')
        self.assertNotContains(response, 'staff-cleaning-assign-form')

    def test_posting_assign_action_is_a_no_op(self):
        self.client.login(username='rotasuperuser', password='pw')
        self.client.post(self.url, {
            'action': 'assign', 'task_id': self.task.pk, 'assigned_to': self.cleaner.pk,
            'date': self.today.isoformat(),
        })
        self.task.refresh_from_db()
        self.assertFalse(self.task.assigned_to.exists())

    def test_non_superuser_sees_only_own_assigned_tasks(self):
        self.task.assigned_to.set([self.other_cleaner])
        self.client.login(username='cleaner1', password='pw')
        response = self.client.get(self.url)
        self.assertNotContains(response, 'Rota Property')

        self.client.login(username='cleaner2', password='pw')
        response = self.client.get(self.url)
        self.assertContains(response, 'Rota Property')

    def test_turnover_shows_next_bookings_extras_not_own(self):
        """A turnover card must show what the ARRIVING guest ordered (relevant to prepping the
        property), not what the guest who just left ordered (irrelevant once they're gone) -
        2026-08-26 fix after this showed the departing booking's own extras, confusingly including
        an unrelated mid-stay-clean note from the wrong stay."""
        Extra.objects.create(
            booking=self.booking, welcome_pack=True,
            welcome_pack_food='standard', welcome_pack_drinks='standard', welcome_pack_charge=10,
        )
        next_guest = Guest.objects.create(first_name='Bo', last_name='Costa', email='rota-next@example.com')
        next_booking = Booking.objects.create(
            property=self.property, guest=next_guest, arrival_date=self.today + timedelta(days=2),
            departure_date=self.today + timedelta(days=9), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Extra.objects.create(booking=next_booking, cot=True, cot_high_chair_charge=15)

        self.client.login(username='rotasuperuser', password='pw')
        response = self.client.get(self.url)
        self.assertNotContains(response, 'Welcome Pack')
        self.assertContains(response, 'Cot')

    def test_turnover_with_no_next_booking_says_so(self):
        self.client.login(username='rotasuperuser', password='pw')
        response = self.client.get(self.url)
        self.assertContains(response, 'Next arrival not yet booked.')

    def test_assignee_can_mark_done(self):
        self.task.assigned_to.set([self.cleaner])
        self.client.login(username='cleaner1', password='pw')
        response = self.client.post(self.url, {
            'action': 'mark_done', 'task_id': self.task.pk, 'date': self.today.isoformat(),
        })
        self.assertRedirects(response, f"{self.url}?date={self.today.isoformat()}")
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'done')
        self.assertEqual(self.task.completed_by, self.cleaner)
        self.assertIsNotNone(self.task.completed_at)

    def test_non_assignee_cannot_mark_done(self):
        self.task.assigned_to.set([self.other_cleaner])
        self.client.login(username='cleaner1', password='pw')
        response = self.client.post(self.url, {
            'action': 'mark_done', 'task_id': self.task.pk, 'date': self.today.isoformat(),
        })
        self.assertEqual(response.status_code, 403)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'pending')


class StaffBookingLookupViewTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='lookup_staffer', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='lookup_staffer', password='pw')
        self.property = Property.objects.create(title='Lookup Property', short_title='LOOKUPPROP')
        self.guest = Guest.objects.create(first_name='Look', last_name='Up', email='staff-lookup@example.com')
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest,
            arrival_date=date.today() + timedelta(days=60), departure_date=date.today() + timedelta(days=67),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_valid_reference_redirects_to_detail(self):
        response = self.client.post(reverse('staff:booking_lookup'), {'reference': self.booking.reference})
        self.assertRedirects(response, reverse('staff:booking_detail', kwargs={'reference': self.booking.reference}))

    def test_unknown_reference_shows_error(self):
        response = self.client.post(reverse('staff:booking_lookup'), {'reference': 'NOPE-NOPE'})
        self.assertEqual(response.status_code, 200)
        messages = list(response.context['messages'])
        self.assertTrue(any('No booking found' in str(m) for m in messages))


class BookingStageTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Stage Test Property', short_title='STAGETEST')
        self.guest = Guest.objects.create(last_name='Stage Guest')

    def _booking(self, start, end, status):
        return Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=start, departure_date=end,
            is_owner=False, enquiry_status=status, enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_provisional(self):
        booking = self._booking(date.today() + timedelta(days=10), date.today() + timedelta(days=17), 'Awaiting payment')
        self.assertEqual(booking_stage(booking), 'Provisional Booking')

    def test_confirmed(self):
        booking = self._booking(date.today() + timedelta(days=10), date.today() + timedelta(days=17), 'Booking confirmed')
        self.assertEqual(booking_stage(booking), 'Confirmed Booking')

    def test_holiday_started(self):
        booking = self._booking(date.today() - timedelta(days=1), date.today() + timedelta(days=5), 'Booking confirmed')
        self.assertEqual(booking_stage(booking), 'Holiday started')

    def test_holiday_ended(self):
        booking = self._booking(date.today() - timedelta(days=10), date.today() - timedelta(days=3), 'Booking confirmed')
        self.assertEqual(booking_stage(booking), 'Holiday ended')

    def test_closed(self):
        booking = self._booking(date.today() + timedelta(days=10), date.today() + timedelta(days=17), 'Cancelled by guest')
        self.assertEqual(booking_stage(booking), 'Closed')


class StatusBucketTests(TestCase):
    def test_provisional_and_confirmed_and_holiday_started_are_valid(self):
        for stage in ('Provisional Booking', 'Confirmed Booking', 'Holiday started'):
            with self.subTest(stage=stage):
                self.assertEqual(status_bucket(stage), 'Valid')

    def test_holiday_ended_is_ended(self):
        self.assertEqual(status_bucket('Holiday ended'), 'Ended')

    def test_closed_is_invalid(self):
        self.assertEqual(status_bucket('Closed'), 'Invalid')


class NextStepHintTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(title='Next Step Property', short_title='NEXTSTEP')
        self.guest = Guest.objects.create(last_name='Next Step Guest')

    def _booking(self, start, end, status):
        return Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=start, departure_date=end,
            is_owner=False, enquiry_status=status, enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_awaiting_payment(self):
        booking = self._booking(date.today() + timedelta(days=10), date.today() + timedelta(days=17), 'Awaiting payment')
        self.assertIn('deposit', next_step_hint(booking, None, None))

    def test_balance_due(self):
        booking = self._booking(date.today() + timedelta(days=10), date.today() + timedelta(days=17), 'Booking confirmed')
        charge = Charge.objects.create(booking=booking, balance_due_date=date.today() + timedelta(days=5))
        balance_payment = BalancePayment.objects.create(booking=booking, provider='revolut')
        self.assertIn('Balance due', next_step_hint(booking, charge, balance_payment))

    def test_on_site(self):
        booking = self._booking(date.today() - timedelta(days=1), date.today() + timedelta(days=5), 'Booking confirmed')
        self.assertIn('on-site', next_step_hint(booking, None, None))

    def test_closed(self):
        booking = self._booking(date.today() + timedelta(days=10), date.today() + timedelta(days=17), 'Cancelled by guest')
        self.assertIn('closed', next_step_hint(booking, None, None))


class StaffHomeViewTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='home_staffer', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='home_staffer', password='pw')

        self.property_a = Property.objects.create(title='Home Property A', short_title='HOMEPROPA')
        self.property_b = Property.objects.create(title='Home Property B', short_title='HOMEPROPB')
        self.guest = Guest.objects.create(first_name='Home', last_name='Guest', email='staff-home@example.com')

        self.booking_a = Booking.objects.create(
            property=self.property_a, guest=self.guest,
            arrival_date=date.today() + timedelta(days=30), departure_date=date.today() + timedelta(days=37),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.booking_b = Booking.objects.create(
            property=self.property_b, guest=self.guest,
            arrival_date=date.today() + timedelta(days=10), departure_date=date.today() + timedelta(days=17),
            is_owner=False, enquiry_status='Awaiting payment', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.cancelled_booking = Booking.objects.create(
            property=self.property_a, guest=self.guest,
            arrival_date=date.today() + timedelta(days=60), departure_date=date.today() + timedelta(days=67),
            is_owner=False, enquiry_status='Cancelled by guest', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.ended_booking = Booking.objects.create(
            property=self.property_a, guest=self.guest,
            arrival_date=date.today() - timedelta(days=20), departure_date=date.today() - timedelta(days=13),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.url = reverse('staff:home')

    def test_default_shows_all_properties_and_only_valid_reservations(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['status_filter'], 'Valid')
        self.assertEqual(len(response.context['calendars']), 2)
        booking_ids = {row['booking'].pk for row in response.context['rows']}
        self.assertEqual(booking_ids, {self.booking_a.pk, self.booking_b.pk})  # ended + cancelled excluded

    def test_property_filter_narrows_calendar_and_reservations(self):
        response = self.client.get(self.url, {'property': self.property_a.pk})
        self.assertEqual(len(response.context['calendars']), 1)
        self.assertEqual(response.context['calendars'][0]['property'], self.property_a)
        booking_ids = {row['booking'].pk for row in response.context['rows']}
        self.assertEqual(booking_ids, {self.booking_a.pk})

    def test_single_property_view_shows_more_months_than_all_properties_view(self):
        all_response = self.client.get(self.url)
        one_response = self.client.get(self.url, {'property': self.property_a.pk})
        self.assertLess(len(all_response.context['calendars'][0]['months']), len(one_response.context['calendars'][0]['months']))

    def test_valid_status_filter_excludes_ended_and_cancelled(self):
        response = self.client.get(self.url, {'status': 'Valid'})
        booking_ids = {row['booking'].pk for row in response.context['rows']}
        self.assertEqual(booking_ids, {self.booking_a.pk, self.booking_b.pk})

    def test_ended_status_filter_shows_only_past_departed_valid_bookings(self):
        response = self.client.get(self.url, {'status': 'Ended'})
        booking_ids = {row['booking'].pk for row in response.context['rows']}
        self.assertEqual(booking_ids, {self.ended_booking.pk})

    def test_invalid_status_filter_shows_cancelled_bookings_with_real_status(self):
        response = self.client.get(self.url, {'status': 'Invalid'})
        rows = response.context['rows']
        booking_ids = {row['booking'].pk for row in rows}
        self.assertEqual(booking_ids, {self.cancelled_booking.pk})
        self.assertEqual(rows[0]['status_label'], 'Cancelled by guest')  # not the generic "Closed" bucket label

    def test_all_status_filter_shows_everything(self):
        response = self.client.get(self.url, {'status': 'All'})
        booking_ids = {row['booking'].pk for row in response.context['rows']}
        self.assertEqual(
            booking_ids,
            {self.booking_a.pk, self.booking_b.pk, self.cancelled_booking.pk, self.ended_booking.pk},
        )

    def test_reservation_link_resolves_to_correct_booking(self):
        response = self.client.get(self.url, {'property': self.property_a.pk})
        self.assertContains(response, reverse('staff:booking_detail', kwargs={'reference': self.booking_a.reference}))

    def test_prop_column_hidden_for_a_single_selected_property(self):
        response = self.client.get(self.url, {'property': self.property_a.pk})
        self.assertNotContains(response, '<th>Prop</th>')
        self.assertContains(response, 'staff-home-layout-single')

    def test_prop_column_shown_for_all_properties(self):
        response = self.client.get(self.url)
        self.assertContains(response, '<th>Prop</th>')
        self.assertNotContains(response, 'staff-home-layout-single')

    def test_malformed_property_param_falls_back_to_all_properties(self):
        response = self.client.get(self.url, {'property': 'not-a-real-id'})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['selected_property'])
        self.assertEqual(len(response.context['calendars']), 2)

    def test_includes_inline_booking_lookup_form(self):
        response = self.client.get(self.url)
        self.assertContains(response, f'action="{reverse("staff:booking_lookup")}"')
        self.assertContains(response, 'name="reference"')


class StaffGuestListViewTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='guests_staffer', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='guests_staffer', password='pw')

        self.adams = Guest.objects.create(
            first_name='Carly', last_name='Adams', email='carly.adams@example.com', phone='+351911222333',
        )
        self.ackroyd = Guest.objects.create(first_name='Nora', last_name='Ackroyd', email='nora.ackroyd@example.com')
        self.baxter = Guest.objects.create(first_name='Bob', last_name='Baxter', email='bob.baxter@example.com')
        self.url = reverse('staff:guest_list')

    def test_default_shows_only_first_letter(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context['selected_letter'], 'A')
        surnames = {guest.last_name for guest in response.context['guests']}
        self.assertEqual(surnames, {'Adams', 'Ackroyd'})

    def test_letter_filter_narrows_to_surname(self):
        response = self.client.get(self.url, {'letter': 'B'})
        surnames = {guest.last_name for guest in response.context['guests']}
        self.assertEqual(surnames, {'Baxter'})

    def test_all_letter_shows_every_guest(self):
        response = self.client.get(self.url, {'letter': 'ALL'})
        surnames = {guest.last_name for guest in response.context['guests']}
        self.assertEqual(surnames, {'Adams', 'Ackroyd', 'Baxter'})

    def test_search_by_name_overrides_letter_filter(self):
        response = self.client.get(self.url, {'q': 'baxter'})
        surnames = {guest.last_name for guest in response.context['guests']}
        self.assertEqual(surnames, {'Baxter'})
        self.assertEqual(response.context['selected_letter'], '')

    def test_search_by_email(self):
        response = self.client.get(self.url, {'q': 'carly.adams'})
        surnames = {guest.last_name for guest in response.context['guests']}
        self.assertEqual(surnames, {'Adams'})

    def test_search_by_phone(self):
        response = self.client.get(self.url, {'q': '911222333'})
        surnames = {guest.last_name for guest in response.context['guests']}
        self.assertEqual(surnames, {'Adams'})

    def test_guest_links_to_detail_page(self):
        response = self.client.get(self.url, {'letter': 'A'})
        self.assertContains(response, reverse('staff:guest_detail', kwargs={'pk': self.adams.pk}))


class StaffGuestDetailViewTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='guest_detail_staffer', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='guest_detail_staffer', password='pw')

        self.property = Property.objects.create(title='Guest Detail Property', short_title='GUESTDETAILPROP')
        self.guest = Guest.objects.create(
            first_name='Carly', last_name='Adams', email='carly.adams@example.com', phone='+351911222333',
        )
        self.other_guest = Guest.objects.create(first_name='Someone', last_name='Else', email='else@example.com')

        self.confirmed_booking = Booking.objects.create(
            property=self.property, guest=self.guest,
            arrival_date=date.today() + timedelta(days=30), departure_date=date.today() + timedelta(days=37),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.cancelled_booking = Booking.objects.create(
            property=self.property, guest=self.guest,
            arrival_date=date.today() + timedelta(days=60), departure_date=date.today() + timedelta(days=67),
            is_owner=False, enquiry_status='Cancelled by guest', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.other_guest_booking = Booking.objects.create(
            property=self.property, guest=self.other_guest,
            arrival_date=date.today() + timedelta(days=15), departure_date=date.today() + timedelta(days=20),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=1, children=0, babies=0, last_updated=timezone.now(),
        )
        self.url = reverse('staff:guest_detail', kwargs={'pk': self.guest.pk})

    def test_unknown_guest_404s(self):
        response = self.client.get(reverse('staff:guest_detail', kwargs={'pk': 999999}))
        self.assertEqual(response.status_code, 404)

    def test_default_shows_all_of_this_guests_bookings(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context['status_filter'], 'All')
        booking_ids = {row['booking'].pk for row in response.context['rows']}
        self.assertEqual(booking_ids, {self.confirmed_booking.pk, self.cancelled_booking.pk})  # includes cancelled, excludes other guest

    def test_valid_status_filter_excludes_cancelled(self):
        response = self.client.get(self.url, {'status': 'Valid'})
        booking_ids = {row['booking'].pk for row in response.context['rows']}
        self.assertEqual(booking_ids, {self.confirmed_booking.pk})

    def test_all_status_filter_includes_cancelled_but_not_other_guest(self):
        response = self.client.get(self.url, {'status': 'All'})
        booking_ids = {row['booking'].pk for row in response.context['rows']}
        self.assertEqual(booking_ids, {self.confirmed_booking.pk, self.cancelled_booking.pk})

    def test_reservation_link_resolves_to_correct_booking(self):
        response = self.client.get(self.url)
        self.assertContains(response, reverse('staff:booking_detail', kwargs={'reference': self.confirmed_booking.reference}))

    def test_update_guest_info_saves_fields(self):
        response = self.client.post(self.url, {
            'first_name': 'Carly', 'last_name': 'Adams-Smith', 'email': 'new@example.com',
            'phone': '+351900000000', 'preferred_language': 'PT',
        })
        self.assertRedirects(response, self.url)
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.last_name, 'Adams-Smith')
        self.assertEqual(self.guest.email, 'new@example.com')
        self.assertEqual(self.guest.preferred_language, 'PT')

    def test_update_guest_info_never_blanks_required_last_name(self):
        self.client.post(self.url, {'last_name': '   '})
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.last_name, 'Adams')


class StaffPropertyListViewTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='property_list_staffer', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='property_list_staffer', password='pw')

        self.location_a = make_location(title='Location A')
        self.location_b = make_location(title='Location B')
        self.property_a = Property.objects.create(
            title='Property A List', short_title='PROPALIST', location=self.location_a,
        )
        self.property_b = Property.objects.create(
            title='Property B List', short_title='PROPBLIST', location=self.location_b,
        )
        self.url = reverse('staff:property_list')

    def test_default_shows_all_properties(self):
        response = self.client.get(self.url)
        titles = {p.title for p in response.context['properties']}
        self.assertEqual(titles, {'Property A List', 'Property B List'})

    def test_location_filter_narrows_list(self):
        response = self.client.get(self.url, {'location': self.location_a.pk})
        titles = {p.title for p in response.context['properties']}
        self.assertEqual(titles, {'Property A List'})

    def test_malformed_location_param_falls_back_to_all(self):
        response = self.client.get(self.url, {'location': 'not-a-real-id'})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['selected_location'])

    def test_add_new_property_link_present(self):
        response = self.client.get(self.url)
        self.assertContains(response, reverse('staff:property_create'))


class StaffPropertyCreateViewTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='property_create_staffer', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='property_create_staffer', password='pw')
        self.url = reverse('staff:property_create')

    def test_get_renders_form(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_post_creates_property_and_redirects_to_detail(self):
        owner, location = make_owner(), make_location()
        response = self.client.post(self.url, {
            'title': 'Brand New Property', 'short_title': 'BRANDNEW', 'al_number': '12345',
            'owner': owner.pk, 'location': location.pk,
            'standard_cleaning_fee': '75.00',
        })
        property = Property.objects.get(short_title='BRANDNEW')
        self.assertRedirects(response, reverse('staff:property_detail', kwargs={'pk': property.pk}))
        self.assertEqual(property.title, 'Brand New Property')
        self.assertEqual(property.al_number, 12345)
        ownership = PropertyOwnership.objects.get(property=property)
        self.assertEqual(ownership.owner, owner)
        self.assertIsNone(ownership.start_date)
        self.assertIsNone(ownership.end_date)

    def test_post_saves_booking_and_cleaning_company(self):
        owner, location = make_owner(), make_location()
        booking_co = make_management_company(name='Booking Co')
        cleaning_co = make_management_company(name='Cleaning Co')
        response = self.client.post(self.url, {
            'title': 'Company-Scoped Property', 'short_title': 'COMPANYSCOPED',
            'owner': owner.pk, 'location': location.pk,
            'booking_company': booking_co.pk, 'cleaning_company': cleaning_co.pk,
        })
        property = Property.objects.get(short_title='COMPANYSCOPED')
        self.assertRedirects(response, reverse('staff:property_detail', kwargs={'pk': property.pk}))
        self.assertEqual(property.booking_company_id, booking_co.pk)
        self.assertEqual(property.cleaning_company_id, cleaning_co.pk)

    def test_post_missing_owner_location_shows_error_and_does_not_create(self):
        """owner/location are DB-nullable but not blank=True, so Django's own admin (and this
        form, via full_clean()) has always required both up front - not new behaviour, just now
        exercised through this view too."""
        response = self.client.post(self.url, {'title': 'Incomplete Property', 'short_title': 'INCOMPLETE'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Property.objects.filter(short_title='INCOMPLETE').exists())

    def test_post_duplicate_title_shows_error_and_does_not_create(self):
        owner, location = make_owner(), make_location()
        Property.objects.create(
            title='Existing Title', short_title='EXISTINGONE', owner=owner, location=location,
        )
        response = self.client.post(self.url, {
            'title': 'Existing Title', 'short_title': 'EXISTINGTWO',
            'owner': owner.pk, 'location': location.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Property.objects.filter(short_title='EXISTINGTWO').exists())


class StaffQuickAddViewTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='quick_add_staffer', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='quick_add_staffer', password='pw')

    def test_management_company_quick_add_creates_and_returns_json(self):
        # Keyed as 'booking_company' (the <select> field name it populates), not
        # 'management_company' (the model name) - see StaffQuickAddView's dispatch dict comment.
        url = reverse('staff:quick_add', kwargs={'model': 'booking_company'})
        response = self.client.post(url, {'name': 'Quick Add Co'})
        self.assertEqual(response.status_code, 200)
        company = ManagementCompany.objects.get(name='Quick Add Co')
        self.assertEqual(response.json(), {'id': company.pk, 'label': 'Quick Add Co'})

    def test_management_company_quick_add_duplicate_name_returns_error(self):
        make_management_company(name='Existing Co')
        url = reverse('staff:quick_add', kwargs={'model': 'booking_company'})
        response = self.client.post(url, {'name': 'Existing Co'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())

    def test_unknown_model_returns_404(self):
        url = reverse('staff:quick_add', kwargs={'model': 'not_a_real_model'})
        response = self.client.post(url, {})
        self.assertEqual(response.status_code, 404)


class StaffPropertyDetailViewTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='property_detail_staffer', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='property_detail_staffer', password='pw')

        self.owner = make_owner()
        self.accountant = make_accountant()
        self.management_company = make_management_company()
        self.location = make_location()
        # Property.owner is blank=False (required by full_clean(), not just DB-nullable - see
        # StaffPropertyCreateViewTests), so every property here starts with a real owner + matching
        # ownership-history row, mirroring exactly what StaffPropertyCreateView itself does on
        # create - not just Property.objects.create(owner=...) alone, which would leave the two out
        # of sync for these ownership-focused tests specifically.
        self.property = Property.objects.create(title='Detail Property', short_title='DETAILPROP', owner=self.owner)
        PropertyOwnership.record_initial_ownership(self.property, self.owner)
        self.url = reverse('staff:property_detail', kwargs={'pk': self.property.pk})

    def test_unknown_property_404s(self):
        response = self.client.get(reverse('staff:property_detail', kwargs={'pk': 999999}))
        self.assertEqual(response.status_code, 404)

    def test_get_lazily_creates_spec_amenity_and_sef_records(self):
        self.assertFalse(PropertySpec.objects.filter(property=self.property).exists())
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(PropertySpec.objects.filter(property=self.property).exists())
        self.assertTrue(Amenity.objects.filter(property=self.property).exists())
        self.assertTrue(SEFDetail.objects.filter(property=self.property).exists())

    def test_context_includes_absolute_ical_export_url(self):
        response = self.client.get(self.url)
        self.assertIn(self.property.ical_export_token, response.context['export_url'])
        self.assertTrue(response.context['export_url'].startswith('http'))

    def test_update_property_info_saves_fields(self):
        response = self.client.post(self.url, {
            'action': 'update_property_info', 'title': 'Detail Property', 'short_title': 'DETAILPROP',
            'door_number': '12B',
            'location': self.location.pk, 'accountant': self.accountant.pk, 'al_number': '9999',
            'booking_company': self.management_company.pk,
            'standard_cleaning_fee': '90.00',
        })
        self.assertRedirects(response, f'{self.url}?panel=main')
        self.property.refresh_from_db()
        self.assertEqual(self.property.door_number, '12B')
        self.assertEqual(self.property.al_number, 9999)
        self.assertEqual(self.property.booking_company_id, self.management_company.pk)
        self.assertIsNone(self.property.cleaning_company_id)
        self.assertEqual(self.property.standard_cleaning_fee, Decimal('90.00'))

    def test_update_property_info_saves_platform_listing_ids(self):
        airbnb = Platform.objects.get_or_create(name='Airbnb')[0]
        self.client.post(self.url, {
            'action': 'update_property_info', 'title': 'Detail Property', 'short_title': 'DETAILPROP',
            'location': self.location.pk,
            f'platform_listing_{airbnb.pk}': 'ABNB-123',
        })
        self.assertEqual(
            PropertyPlatformID.objects.get(property=self.property, platform=airbnb).listing_id,
            'ABNB-123',
        )

    def test_update_property_info_blank_listing_id_deletes_existing_row(self):
        airbnb = Platform.objects.get_or_create(name='Airbnb')[0]
        PropertyPlatformID.objects.create(property=self.property, platform=airbnb, listing_id='ABNB-123')
        self.client.post(self.url, {
            'action': 'update_property_info', 'title': 'Detail Property', 'short_title': 'DETAILPROP',
            'location': self.location.pk,
            f'platform_listing_{airbnb.pk}': '',
        })
        self.assertFalse(PropertyPlatformID.objects.filter(property=self.property, platform=airbnb).exists())

    def test_update_property_info_no_longer_accepts_an_owner_field(self):
        # Regression guard: Property.owner must only ever change via PropertyOwnership.
        # record_handover() (the Ownership tab / record_handover action below) - posting an
        # 'owner' field to this action should have no effect, even though the raw field name
        # would otherwise map straight onto Property.owner_id.
        other_owner = make_owner(name='Other Owner', email='other-owner@example.com')
        self.client.post(self.url, {
            'action': 'update_property_info', 'title': 'Detail Property', 'short_title': 'DETAILPROP',
            'owner': other_owner.pk,
        })
        self.property.refresh_from_db()
        self.assertEqual(self.property.owner_id, self.owner.pk)

    def test_update_property_info_rejects_duplicate_title(self):
        Property.objects.create(title='Taken Title', short_title='TAKENSHORT')
        self.client.post(self.url, {
            'action': 'update_property_info', 'title': 'Taken Title', 'short_title': 'DETAILPROP',
        })
        self.property.refresh_from_db()
        self.assertEqual(self.property.title, 'Detail Property')

    def test_update_specification_saves_fields(self):
        self.client.post(self.url, {
            'action': 'update_specification', 'bedrooms': '3', 'bathrooms': '2', 'max_guests': '6',
            'is_sea_view': 'on', 'pets_allowed': 'on', 'children_allowed': 'on',
            'description': 'A lovely place.',
        })
        specs = PropertySpec.objects.get(property=self.property)
        self.assertEqual(specs.bedrooms, 3)
        self.assertEqual(specs.max_guests, 6)
        self.assertTrue(specs.is_sea_view)
        self.assertFalse(specs.is_pool_view)
        self.assertEqual(specs.description, 'A lovely place.')

    def test_update_amenities_saves_fields(self):
        self.client.post(self.url, {
            'action': 'update_amenities', 'wifi': 'on', 'pool': 'on', 'double_beds': '2',
            'bed_sizes': '180 x 200', 'vacuum_cleaner': 'on', 'coffee_machine': 'on',
            'hand_towels_per_guest': '2', 'beach_towels_per_guest': '0',
        })
        amenities = Amenity.objects.get(property=self.property)
        self.assertTrue(amenities.wifi)
        self.assertTrue(amenities.pool)
        self.assertFalse(amenities.hot_tub)
        self.assertEqual(amenities.double_beds, 2)
        self.assertEqual(amenities.bed_sizes, '180 x 200')
        self.assertTrue(amenities.vacuum_cleaner)
        self.assertTrue(amenities.coffee_machine)
        self.assertFalse(amenities.mop_and_bucket)  # not posted, so this update turns it off
        self.assertEqual(amenities.hand_towels_per_guest, 2)
        self.assertEqual(amenities.bath_towels_per_guest, 1)  # not posted - default carried over
        self.assertEqual(amenities.beach_towels_per_guest, 0)

    def test_update_sef_saves_fields(self):
        self.client.post(self.url, {
            'action': 'update_sef', 'unidade_hoteleira': 'UH123', 'estabelecimento': 'EST456',
            'chave_de_autenticacao': 'KEY789',
        })
        sef = SEFDetail.objects.get(property=self.property)
        self.assertEqual(sef.unidade_hoteleira, 'UH123')
        self.assertEqual(sef.chave_de_autenticacao, 'KEY789')

    def test_add_price_creates_price_line(self):
        self.client.post(self.url, {
            'action': 'add_price', 'start_date': '2027-06-01', 'end_date': '2027-08-31',
            'rate': '150.00', 'weekly_discount_percent': '10',
        })
        price = Price.objects.get(property=self.property, start_date=date(2027, 6, 1))
        self.assertEqual(price.rate, Decimal('150.00'))

    def test_add_price_rejects_overlap(self):
        Price.objects.create(
            property=self.property, start_date=date(2027, 6, 1), end_date=date(2027, 8, 31),
        )
        self.client.post(self.url, {
            'action': 'add_price', 'start_date': '2027-07-01', 'end_date': '2027-07-15',
            'rate': '100.00',
        })
        self.assertFalse(Price.objects.filter(property=self.property, start_date=date(2027, 7, 1)).exists())

    def test_update_price_saves_fields(self):
        price = Price.objects.create(
            property=self.property, start_date=date(2027, 1, 1), end_date=date(2027, 1, 31),
            rate=Decimal('100.00'),
        )
        response = self.client.post(self.url, {
            'action': 'update_price', 'price_id': price.pk,
            'start_date': '2027-01-01', 'end_date': '2027-02-15', 'rate': '125.50',
            'weekly_discount_percent': '15',
        })
        self.assertRedirects(response, f'{self.url}?panel=rates')
        price.refresh_from_db()
        self.assertEqual(price.end_date, date(2027, 2, 15))
        self.assertEqual(price.rate, Decimal('125.50'))
        self.assertEqual(price.weekly_discount_percent, Decimal('15'))

    def test_update_price_rejects_overlap_with_another_line(self):
        Price.objects.create(
            property=self.property, start_date=date(2027, 5, 1), end_date=date(2027, 5, 31),
        )
        price = Price.objects.create(
            property=self.property, start_date=date(2027, 1, 1), end_date=date(2027, 1, 31),
        )
        self.client.post(self.url, {
            'action': 'update_price', 'price_id': price.pk,
            'start_date': '2027-05-15', 'end_date': '2027-06-15', 'rate': '100.00',
        })
        price.refresh_from_db()
        self.assertEqual(price.start_date, date(2027, 1, 1))  # unchanged - rejected by overlap check

    def test_update_price_editing_its_own_unchanged_dates_does_not_self_conflict(self):
        price = Price.objects.create(
            property=self.property, start_date=date(2027, 1, 1), end_date=date(2027, 1, 31),
            rate=Decimal('100.00'),
        )
        self.client.post(self.url, {
            'action': 'update_price', 'price_id': price.pk,
            'start_date': '2027-01-01', 'end_date': '2027-01-31', 'rate': '110.00',
        })
        price.refresh_from_db()
        self.assertEqual(price.rate, Decimal('110.00'))

    def test_delete_price(self):
        price = Price.objects.create(
            property=self.property, start_date=date(2027, 1, 1), end_date=date(2027, 1, 31),
        )
        self.client.post(self.url, {'action': 'delete_price', 'price_id': price.pk})
        self.assertFalse(Price.objects.filter(pk=price.pk).exists())

    def test_add_ical_link(self):
        airbnb = Platform.objects.get_or_create(name='Airbnb')[0]
        self.client.post(self.url, {
            'action': 'add_ical_link', 'platform': airbnb.pk, 'ical_url': 'https://airbnb.com/feed.ics',
        })
        link = iCalLink.objects.get(property=self.property)
        self.assertEqual(link.platform_id, airbnb.pk)
        self.assertEqual(link.ical_url, 'https://airbnb.com/feed.ics')

    def test_add_ical_link_requires_url(self):
        airbnb = Platform.objects.get_or_create(name='Airbnb')[0]
        self.client.post(self.url, {'action': 'add_ical_link', 'platform': airbnb.pk, 'ical_url': ''})
        self.assertFalse(iCalLink.objects.filter(property=self.property).exists())

    def test_add_ical_link_as_an_owner_link(self):
        airbnb = Platform.objects.get_or_create(name='Airbnb')[0]
        self.client.post(self.url, {
            'action': 'add_ical_link', 'platform': airbnb.pk, 'ical_url': 'https://airbnb.com/feed.ics',
            'is_owner_link': 'on',
        })
        link = iCalLink.objects.get(property=self.property)
        self.assertTrue(link.is_owner_link)

    def test_update_ical_link_toggles_is_owner_link(self):
        link = iCalLink.objects.create(property=self.property, ical_url='https://old.example.com/feed.ics')
        self.client.post(self.url, {
            'action': 'update_ical_link', 'link_id': link.pk, 'ical_url': link.ical_url,
            'is_owner_link': 'on',
        })
        link.refresh_from_db()
        self.assertTrue(link.is_owner_link)

        self.client.post(self.url, {
            'action': 'update_ical_link', 'link_id': link.pk, 'ical_url': link.ical_url,
        })
        link.refresh_from_db()
        self.assertFalse(link.is_owner_link)

    def test_update_ical_link_saves_new_url(self):
        booking_com = Platform.objects.get_or_create(name='Booking.com')[0]
        link = iCalLink.objects.create(
            property=self.property, platform=booking_com, ical_url='https://old.example.com/feed.ics',
        )
        response = self.client.post(self.url, {
            'action': 'update_ical_link', 'link_id': link.pk, 'ical_url': 'https://new.example.com/feed.ics',
        })
        self.assertRedirects(response, f'{self.url}?panel=ical')
        link.refresh_from_db()
        self.assertEqual(link.ical_url, 'https://new.example.com/feed.ics')
        self.assertEqual(link.platform_id, booking_com.pk)  # untouched by this action

    def test_update_ical_link_requires_url(self):
        link = iCalLink.objects.create(property=self.property, ical_url='https://old.example.com/feed.ics')
        self.client.post(self.url, {'action': 'update_ical_link', 'link_id': link.pk, 'ical_url': ''})
        link.refresh_from_db()
        self.assertEqual(link.ical_url, 'https://old.example.com/feed.ics')

    def test_update_ical_link_for_a_different_property_is_a_noop(self):
        other_property = Property.objects.create(title='Other', short_title='OTHERUPDPROP')
        link = iCalLink.objects.create(property=other_property, ical_url='https://old.example.com/feed.ics')
        self.client.post(self.url, {
            'action': 'update_ical_link', 'link_id': link.pk, 'ical_url': 'https://new.example.com/feed.ics',
        })
        link.refresh_from_db()
        self.assertEqual(link.ical_url, 'https://old.example.com/feed.ics')

    def test_delete_ical_link(self):
        link = iCalLink.objects.create(property=self.property, ical_url='https://example.com/feed.ics')
        self.client.post(self.url, {'action': 'delete_ical_link', 'link_id': link.pk})
        self.assertFalse(iCalLink.objects.filter(pk=link.pk).exists())

    def test_add_image_and_delete_image(self):
        upload = SimpleUploadedFile('test.jpg', b'fake-image-bytes', content_type='image/jpeg')
        self.client.post(self.url, {'action': 'add_image', 'image': upload, 'caption': 'Living room'})
        image = PropertyImage.objects.get(property=self.property)
        self.assertEqual(image.caption, 'Living room')

        self.client.post(self.url, {'action': 'delete_image', 'image_id': image.pk})
        self.assertFalse(PropertyImage.objects.filter(pk=image.pk).exists())
        image.image.delete(save=False)

    def test_unknown_action_is_a_noop(self):
        response = self.client.post(self.url, {'action': 'not_a_real_action'})
        self.assertRedirects(response, f'{self.url}?panel=main')

    def test_default_panel_is_info(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context['active_panel'], 'main')

    def test_panel_query_param_selects_active_panel(self):
        response = self.client.get(self.url, {'panel': 'amenities'})
        self.assertEqual(response.context['active_panel'], 'amenities')

    def test_unrecognised_panel_query_param_falls_back_to_info(self):
        response = self.client.get(self.url, {'panel': 'not-a-real-panel'})
        self.assertEqual(response.context['active_panel'], 'main')

    def test_adding_a_price_redirects_to_the_rates_panel(self):
        response = self.client.post(self.url, {
            'action': 'add_price', 'start_date': '2027-09-01', 'end_date': '2027-10-31',
            'rate': '90.00',
        })
        self.assertRedirects(response, f'{self.url}?panel=rates')

    def test_save_redirects_back_to_the_panel_it_came_from(self):
        response = self.client.post(self.url, {
            'action': 'update_amenities', 'wifi': 'on',
        })
        self.assertRedirects(response, f'{self.url}?panel=amenities')

    def test_ownership_tab_shows_history_newest_first(self):
        # setUp already gave self.property its initial (null-start, open-ended) ownership row for
        # self.owner - this only adds the handover on top of it.
        newer_owner = make_owner(name='Newer Owner', email='newer-owner@example.com')
        PropertyOwnership.record_handover(self.property, newer_owner, date(2027, 1, 1))
        response = self.client.get(self.url, {'panel': 'ownership'})
        history = list(response.context['ownership_history'])
        self.assertEqual(history[0].owner, newer_owner)
        self.assertEqual(history[1].owner, self.owner)

    def test_record_handover_creates_ownership_row_and_updates_property_owner(self):
        new_owner = make_owner(name='Handover Owner', email='handover-owner@example.com')
        response = self.client.post(self.url, {
            'action': 'record_handover', 'new_owner': new_owner.pk, 'effective_date': '2027-03-01',
        })
        self.assertRedirects(response, f'{self.url}?panel=ownership')
        self.property.refresh_from_db()
        self.assertEqual(self.property.owner, new_owner)
        row = PropertyOwnership.objects.get(property=self.property, owner=new_owner)
        self.assertEqual(row.start_date, date(2027, 3, 1))
        self.assertIsNone(row.end_date)

    def test_record_handover_closes_out_previous_owners_row(self):
        new_owner = make_owner(name='Handover New Owner', email='handover-new-owner@example.com')
        self.client.post(self.url, {
            'action': 'record_handover', 'new_owner': new_owner.pk, 'effective_date': '2027-03-01',
        })
        prior = PropertyOwnership.objects.get(property=self.property, owner=self.owner)
        self.assertEqual(prior.end_date, date(2027, 2, 28))

    def test_record_handover_rejects_missing_new_owner_or_date(self):
        response = self.client.post(self.url, {'action': 'record_handover', 'new_owner': '', 'effective_date': ''})
        self.assertRedirects(response, f'{self.url}?panel=ownership')
        # Only the initial ownership row from setUp - nothing new created from the bad POST.
        self.assertEqual(PropertyOwnership.objects.filter(property=self.property).count(), 1)

    def test_record_handover_flashes_validation_error_on_invalid_effective_date(self):
        # A real start_date is needed on the *current* owner's row for the date-order check to
        # bite (setUp's initial row has start_date=None, "since before tracking" - unbounded, so
        # nothing before it can violate it) - one legitimate handover establishes that first.
        interim_owner = make_owner(name='Interim Owner', email='interim-owner@example.com')
        PropertyOwnership.record_handover(self.property, interim_owner, date(2027, 3, 1))
        another_owner = make_owner(name='Another Owner', email='another-owner@example.com')
        response = self.client.post(self.url, {
            'action': 'record_handover', 'new_owner': another_owner.pk, 'effective_date': '2027-01-01',
        }, follow=True)
        messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('start date' in m for m in messages))
        self.assertFalse(PropertyOwnership.objects.filter(property=self.property, owner=another_owner).exists())

    def test_record_handover_redirects_to_ownership_panel(self):
        new_owner = make_owner(name='Redirect Owner', email='redirect-owner@example.com')
        response = self.client.post(self.url, {
            'action': 'record_handover', 'new_owner': new_owner.pk, 'effective_date': '2027-03-01',
        })
        self.assertRedirects(response, f'{self.url}?panel=ownership')


def _ics_feed(events):
    """Same minimal single-event .ics builder as bookings/tests.py's own helper - duplicated
    rather than imported since this file has no existing cross-app test-helper import, and the
    feed shape is trivial enough not to warrant one just for this."""
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Test//EN']
    for uid, start, end in events:
        lines += [
            'BEGIN:VEVENT', f'UID:{uid}',
            f'DTSTART;VALUE=DATE:{start.strftime("%Y%m%d")}',
            f'DTEND;VALUE=DATE:{end.strftime("%Y%m%d")}',
            'SUMMARY:Reserved', 'END:VEVENT',
        ]
    lines.append('END:VCALENDAR')
    return '\r\n'.join(lines)


class StaffIcalSyncViewTests(TestCase):
    """Covers the view wiring around sync_ical_link() (fetch, error handling, template choice) -
    see bookings/tests.py::SyncIcalLinkTests for the actual sync-logic coverage."""
    def setUp(self):
        User.objects.create_user(username='ical_sync_staffer', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='ical_sync_staffer', password='pw')
        self.property = Property.objects.create(title='Sync View Property', short_title='SYNCVIEWPROP')
        self.platform = Platform.objects.get_or_create(name='Airbnb')[0]
        self.link = iCalLink.objects.create(
            property=self.property, platform=self.platform, ical_url='https://example.com/feed.ics',
        )
        self.url = reverse('staff:ical_sync', kwargs={'pk': self.property.pk, 'link_id': self.link.pk})
        self.start = date.today() + timedelta(days=100)
        self.end = self.start + timedelta(days=7)

    def test_unknown_property_404s(self):
        url = reverse('staff:ical_sync', kwargs={'pk': 999999, 'link_id': self.link.pk})
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_link_belonging_to_a_different_property_404s(self):
        other_property = Property.objects.create(title='Other', short_title='OTHERSYNCPROP')
        url = reverse('staff:ical_sync', kwargs={'pk': other_property.pk, 'link_id': self.link.pk})
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_link_with_no_url_shows_an_error_without_fetching(self):
        self.link.ical_url = ''
        self.link.save(update_fields=['ical_url'])
        with patch('staff.views.requests.get') as mocked_get:
            response = self.client.post(self.url)
        mocked_get.assert_not_called()
        self.assertIn('URL', response.context['fetch_error'])

    def test_link_with_no_platform_shows_an_error_without_fetching(self):
        self.link.platform = None
        self.link.save(update_fields=['platform'])
        with patch('staff.views.requests.get') as mocked_get:
            response = self.client.post(self.url)
        mocked_get.assert_not_called()
        self.assertIn('no platform set', response.context['fetch_error'])

    def test_fetch_failure_shows_an_error(self):
        with patch('staff.views.requests.get', side_effect=requests.ConnectionError('boom')):
            response = self.client.post(self.url)
        self.assertIn('Could not connect', response.context['fetch_error'])

    def test_empty_feed_reports_healthy_with_no_events(self):
        mocked_response = Mock(text=_ics_feed([]))
        mocked_response.raise_for_status = Mock()
        with patch('staff.views.requests.get', return_value=mocked_response):
            response = self.client.post(self.url)
        self.assertNotIn('fetch_error', response.context)
        self.assertEqual(response.context['summary']['events'], [])

    def test_feed_with_a_new_event_creates_a_booking_and_reports_it(self):
        mocked_response = Mock(text=_ics_feed([('uid-1', self.start, self.end)]))
        mocked_response.raise_for_status = Mock()
        with patch('staff.views.requests.get', return_value=mocked_response):
            response = self.client.post(self.url)
        summary = response.context['summary']
        self.assertEqual(summary['created'], 1)
        self.assertEqual(summary['events'][0]['result'], 'created')
        booking = Booking.objects.get(ical_uid='uid-1')
        self.assertEqual(summary['events'][0]['booking'], booking)
        self.assertContains(response, booking.reference)

    def test_feed_event_overlapping_an_existing_booking_is_flagged_as_a_conflict(self):
        guest = Guest.objects.create(last_name='Existing Guest')
        Booking.objects.create(
            property=self.property, guest=guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        mocked_response = Mock(text=_ics_feed([('uid-1', self.start, self.end)]))
        mocked_response.raise_for_status = Mock()
        with patch('staff.views.requests.get', return_value=mocked_response):
            response = self.client.post(self.url)
        summary = response.context['summary']
        self.assertEqual(len(summary['conflicts']), 1)
        self.assertEqual(summary['events'][0]['result'], 'conflict')
        self.assertContains(response, 'Overlaps an existing booking')

    def test_updates_link_last_synced_on_success(self):
        self.assertIsNone(self.link.last_synced)
        mocked_response = Mock(text=_ics_feed([]))
        mocked_response.raise_for_status = Mock()
        with patch('staff.views.requests.get', return_value=mocked_response):
            self.client.post(self.url)
        self.link.refresh_from_db()
        self.assertIsNotNone(self.link.last_synced)


class StaffLocationListViewTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='location_list_staffer', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='location_list_staffer', password='pw')
        self.location = make_location(title='Location List Test')
        self.url = reverse('staff:location_list')

    def test_lists_locations_with_property_count(self):
        Property.objects.create(title='Counted Property', short_title='COUNTED', location=self.location)
        response = self.client.get(self.url)
        row = next(l for l in response.context['locations'] if l.pk == self.location.pk)
        self.assertEqual(row.property_count, 1)

    def test_add_new_location_link_present(self):
        response = self.client.get(self.url)
        self.assertContains(response, reverse('staff:location_create'))


class StaffLocationCreateViewTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='location_create_staffer', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='location_create_staffer', password='pw')
        self.url = reverse('staff:location_create')

    def test_get_renders_form(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_post_creates_location_and_redirects_to_detail(self):
        response = self.client.post(self.url, {
            'title': 'Brand New Location', 'street': '5 New Street', 'zip_code': '8000-111',
            'city': 'Faro', 'coordinates': '37.1,-7.8', 'map_link': 'https://maps.example.com/new',
        })
        location = Location.objects.get(title='Brand New Location')
        self.assertRedirects(response, reverse('staff:location_detail', kwargs={'pk': location.pk}))
        self.assertEqual(location.city, 'Faro')

    def test_post_missing_required_field_shows_error_and_does_not_create(self):
        response = self.client.post(self.url, {'title': 'Incomplete Location'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Location.objects.filter(title='Incomplete Location').exists())


class StaffLocationDetailViewTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='location_detail_staffer', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='location_detail_staffer', password='pw')
        self.location = make_location(title='Detail Location')
        self.url = reverse('staff:location_detail', kwargs={'pk': self.location.pk})

    def test_unknown_location_404s(self):
        response = self.client.get(reverse('staff:location_detail', kwargs={'pk': 999999}))
        self.assertEqual(response.status_code, 404)

    def test_get_lazily_creates_spec_and_rules_records(self):
        self.assertFalse(LocationSpec.objects.filter(location=self.location).exists())
        self.assertFalse(LocationRules.objects.filter(location=self.location).exists())
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(LocationSpec.objects.filter(location=self.location).exists())
        rules = LocationRules.objects.get(location=self.location)
        self.assertIsNotNone(rules.quiet_hours_start)

    def test_update_location_info_saves_fields(self):
        response = self.client.post(self.url, {
            'action': 'update_location_info', 'title': 'Detail Location', 'street': '9 Updated Street',
            'zip_code': '8000-222', 'city': 'Faro', 'coordinates': '37.2,-7.7',
            'map_link': 'https://maps.example.com/updated', 'description': 'A lovely spot.',
        })
        self.assertRedirects(response, f'{self.url}?panel=main')
        self.location.refresh_from_db()
        self.assertEqual(self.location.street, '9 Updated Street')
        self.assertEqual(self.location.description, 'A lovely spot.')

    def test_update_location_info_rejects_blank_required_field(self):
        self.client.post(self.url, {
            'action': 'update_location_info', 'title': '', 'street': self.location.street,
            'zip_code': self.location.zip_code, 'city': self.location.city,
            'coordinates': self.location.coordinates, 'map_link': 'not-a-valid-url',
        })
        self.location.refresh_from_db()
        self.assertEqual(self.location.title, 'Detail Location')

    def test_update_specification_saves_fields(self):
        self.client.post(self.url, {'action': 'update_specification', 'sea_views': 'on', 'pool': 'on'})
        specs = LocationSpec.objects.get(location=self.location)
        self.assertTrue(specs.sea_views)
        self.assertTrue(specs.pool)
        self.assertFalse(specs.gym)

    def test_update_rules_saves_fields(self):
        self.client.post(self.url, {
            'action': 'update_rules', 'quiet_hours_start': '23:00', 'quiet_hours_end': '07:00',
            'pool_hours_start': '08:00', 'pool_hours_end': '21:00', 'pool_rules': 'No diving.',
        })
        rules = LocationRules.objects.get(location=self.location)
        self.assertEqual(rules.quiet_hours_start.strftime('%H:%M'), '23:00')
        self.assertEqual(rules.pool_rules, 'No diving.')

    def test_add_image_and_delete_image(self):
        upload = SimpleUploadedFile('test.jpg', b'fake-image-bytes', content_type='image/jpeg')
        self.client.post(self.url, {'action': 'add_image', 'image': upload, 'caption': 'View'})
        image = LocationImage.objects.get(location=self.location)
        self.assertEqual(image.caption, 'View')

        self.client.post(self.url, {'action': 'delete_image', 'image_id': image.pk})
        self.assertFalse(LocationImage.objects.filter(pk=image.pk).exists())
        image.image.delete(save=False)

    def test_default_panel_is_main(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context['active_panel'], 'main')

    def test_save_redirects_back_to_the_panel_it_came_from(self):
        response = self.client.post(self.url, {'action': 'update_rules', 'pool_rules': 'x'})
        self.assertRedirects(response, f'{self.url}?panel=rules')


class StaffSettingsViewTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='settings_staffer', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='settings_staffer', password='pw')
        self.url = reverse('staff:settings')

    def test_delete_owner_with_ownership_history_is_blocked_with_friendly_message(self):
        # PropertyOwnership.owner is on_delete=PROTECT (a permanent record, unlike Property.owner's
        # own SET_NULL) - this guards that the resulting ProtectedError is caught and flashed
        # rather than bubbling up as a 500.
        owner = make_owner()
        property = Property.objects.create(title='Settings Delete Property', short_title='SETTINGSDEL')
        PropertyOwnership.record_initial_ownership(property, owner)
        response = self.client.post(self.url, {
            'action': 'delete_owner', 'owner_id': owner.pk,
        }, follow=True)
        self.assertTrue(Owner.objects.filter(pk=owner.pk).exists())
        messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('ownership history' in m for m in messages))

    def test_add_platform(self):
        self.client.post(self.url, {'action': 'add_platform', 'name': 'Direct'})
        platform = Platform.objects.get(name='Direct')
        self.assertFalse(platform.take_security_deposits)

    def test_add_platform_with_take_security_deposits_checked(self):
        self.client.post(self.url, {
            'action': 'add_platform', 'name': 'Direct', 'take_security_deposits': 'on',
        })
        self.assertTrue(Platform.objects.get(name='Direct').take_security_deposits)

    def test_update_platform(self):
        platform = Platform.objects.create(name='Old Name')
        self.client.post(self.url, {
            'action': 'update_platform', 'platform_id': platform.pk, 'name': 'New Name',
            'take_security_deposits': 'on',
        })
        platform.refresh_from_db()
        self.assertEqual(platform.name, 'New Name')
        self.assertTrue(platform.take_security_deposits)

    def test_delete_platform(self):
        platform = Platform.objects.create(name='Unused Platform')
        self.client.post(self.url, {'action': 'delete_platform', 'platform_id': platform.pk})
        self.assertFalse(Platform.objects.filter(pk=platform.pk).exists())

    def test_delete_platform_used_by_an_ical_link_is_blocked_with_friendly_message(self):
        # A live incident during this feature's own build (deleting a Platform still referenced by
        # a property's PropertyPlatformID silently cascade-deleted real, migrated listing-ID data)
        # is why both iCalLink.platform and PropertyPlatformID.platform are PROTECT, not CASCADE -
        # this and the next test guard that the resulting ProtectedError is caught and flashed
        # rather than silently destroying data or bubbling up as a 500.
        platform = Platform.objects.create(name='In Use Platform')
        property = Property.objects.create(title='Platform Delete Property', short_title='PLATFORMDEL')
        iCalLink.objects.create(property=property, platform=platform, ical_url='https://example.com/feed.ics')
        response = self.client.post(self.url, {
            'action': 'delete_platform', 'platform_id': platform.pk,
        }, follow=True)
        self.assertTrue(Platform.objects.filter(pk=platform.pk).exists())
        messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('iCal link or listing ID' in m for m in messages))

    def test_delete_platform_used_by_a_property_listing_id_is_blocked(self):
        platform = Platform.objects.create(name='In Use Platform')
        property = Property.objects.create(title='Platform Delete Property 2', short_title='PLATFORMDEL2')
        PropertyPlatformID.objects.create(property=property, platform=platform, listing_id='XYZ')
        self.client.post(self.url, {'action': 'delete_platform', 'platform_id': platform.pk})
        self.assertTrue(Platform.objects.filter(pk=platform.pk).exists())
        self.assertTrue(PropertyPlatformID.objects.filter(property=property, platform=platform).exists())

    def test_add_management_company_with_no_contacts_set_succeeds(self):
        # Every contact role is genuinely optional - a company can be added with just a name.
        self.client.post(self.url, {'action': 'add_management_company', 'name': 'New Management Co'})
        company = ManagementCompany.objects.get(name='New Management Co')
        self.assertEqual(company.head_name, '')
        self.assertEqual(company.cleaning_email, '')

    def test_add_management_company_with_only_head_contact_succeeds(self):
        self.client.post(self.url, {
            'action': 'add_management_company', 'name': 'Head Only Co',
            'head_name': 'Jane Doe', 'head_email': 'jane@example.com', 'head_phone': '+351911111111',
        })
        company = ManagementCompany.objects.get(name='Head Only Co')
        self.assertEqual(company.head_name, 'Jane Doe')
        self.assertEqual(company.maintenance_name, '')

    def test_add_management_company_requires_a_name(self):
        # count() starts at 1, not 0: migration 0024 seeds the real "KLT Property Services Lda."
        # row, which exists in the test database too (migrations run there the same as production).
        before = ManagementCompany.objects.count()
        self.client.post(self.url, {'action': 'add_management_company', 'name': ''})
        self.assertEqual(ManagementCompany.objects.count(), before)

    def test_add_management_company_with_no_operational_settings_leaves_them_null(self):
        # All genuinely optional (2026-08-27, per Thomas) - a company that doesn't specify any of
        # these shouldn't get a default value that looks like a real, deliberate answer.
        self.client.post(self.url, {'action': 'add_management_company', 'name': 'No Settings Co'})
        company = ManagementCompany.objects.get(name='No Settings Co')
        self.assertIsNone(company.towels_per_guest)
        self.assertIsNone(company.includes_beach_towels)
        self.assertIsNone(company.linen_provided)
        self.assertIsNone(company.standard_meet_and_greet_fee)
        self.assertIsNone(company.check_in_method)
        self.assertIsNone(company.self_check_in_after)
        self.assertIsNone(company.freshen_after_days)

    def test_add_management_company_with_operational_settings_saves_them(self):
        self.client.post(self.url, {
            'action': 'add_management_company', 'name': 'Full Settings Co',
            'towels_per_guest': '2', 'includes_beach_towels': 'true', 'linen_provided': 'true',
            'standard_meet_and_greet_fee': '25.00', 'check_in_method': 'mixed',
            'self_check_in_after': '16:00', 'freshen_after_days': '14',
        })
        company = ManagementCompany.objects.get(name='Full Settings Co')
        self.assertEqual(company.towels_per_guest, 2)
        self.assertTrue(company.includes_beach_towels)
        self.assertTrue(company.linen_provided)
        self.assertEqual(company.standard_meet_and_greet_fee, Decimal('25.00'))
        self.assertEqual(company.check_in_method, 'mixed')
        self.assertEqual(company.self_check_in_after, time(16, 0))
        self.assertEqual(company.freshen_after_days, 14)

    def test_add_management_company_rejects_an_unrecognised_check_in_method(self):
        # A stray/tampered value falls back to None rather than a 500 or a silently-wrong choice.
        self.client.post(self.url, {
            'action': 'add_management_company', 'name': 'Bad Check-in Co', 'check_in_method': 'not-a-real-choice',
        })
        company = ManagementCompany.objects.get(name='Bad Check-in Co')
        self.assertIsNone(company.check_in_method)

    def test_update_management_company_round_trips_operational_settings(self):
        company = make_management_company()
        self.client.post(self.url, {
            'action': 'update_management_company', 'management_company_id': company.pk,
            'name': company.name, 'towels_per_guest': '3', 'includes_beach_towels': 'false',
            'linen_provided': 'true', 'standard_meet_and_greet_fee': '30.50',
            'check_in_method': 'self_check_in', 'freshen_after_days': '10',
        })
        company.refresh_from_db()
        self.assertEqual(company.towels_per_guest, 3)
        self.assertFalse(company.includes_beach_towels)
        self.assertTrue(company.linen_provided)
        self.assertEqual(company.standard_meet_and_greet_fee, Decimal('30.50'))
        self.assertEqual(company.check_in_method, 'self_check_in')
        self.assertEqual(company.freshen_after_days, 10)
        # Clearing it back out (blank/omitted fields posted) should genuinely clear it to "not
        # specified" (None), same as the maintenance-contact round trip above - not leave stale
        # values stuck, and not silently coerce "not specified" into False for the boolean field.
        self.client.post(self.url, {
            'action': 'update_management_company', 'management_company_id': company.pk, 'name': company.name,
        })
        company.refresh_from_db()
        self.assertIsNone(company.towels_per_guest)
        self.assertIsNone(company.includes_beach_towels)
        self.assertIsNone(company.linen_provided)
        self.assertIsNone(company.standard_meet_and_greet_fee)
        self.assertIsNone(company.check_in_method)
        self.assertIsNone(company.freshen_after_days)

    def test_add_management_company_calendar_visibility_flags_default_false_when_unchecked(self):
        # Unlike the "not specified" operational fields above, these two plain checkboxes always
        # resolve to a real boolean - an omitted key (box left unchecked) means False here, not
        # "leave the model's own True default alone". The settings.html "new company" row ships
        # both boxes pre-checked so an admin who doesn't touch them still gets True in practice.
        self.client.post(self.url, {'action': 'add_management_company', 'name': 'No Flags Co'})
        company = ManagementCompany.objects.get(name='No Flags Co')
        self.assertFalse(company.cleans_on_calendar)
        self.assertFalse(company.checkins_on_calendar)

    def test_add_management_company_calendar_visibility_flags_when_checked(self):
        self.client.post(self.url, {
            'action': 'add_management_company', 'name': 'Flags Co',
            'cleans_on_calendar': 'on', 'checkins_on_calendar': 'on',
        })
        company = ManagementCompany.objects.get(name='Flags Co')
        self.assertTrue(company.cleans_on_calendar)
        self.assertTrue(company.checkins_on_calendar)

    def test_update_management_company_can_turn_calendar_visibility_flags_off(self):
        company = make_management_company(cleans_on_calendar=True, checkins_on_calendar=True)
        self.client.post(self.url, {
            'action': 'update_management_company', 'management_company_id': company.pk, 'name': company.name,
        })
        company.refresh_from_db()
        self.assertFalse(company.cleans_on_calendar)
        self.assertFalse(company.checkins_on_calendar)

    def test_update_management_company_can_turn_bookable_on_website_off(self):
        company = make_management_company(bookable_on_website=True)
        self.client.post(self.url, {
            'action': 'update_management_company', 'management_company_id': company.pk, 'name': company.name,
        })
        company.refresh_from_db()
        self.assertFalse(company.bookable_on_website)

    def test_update_management_company_can_turn_bookable_on_website_on(self):
        company = make_management_company(bookable_on_website=False)
        self.client.post(self.url, {
            'action': 'update_management_company', 'management_company_id': company.pk, 'name': company.name,
            'bookable_on_website': 'on',
        })
        company.refresh_from_db()
        self.assertTrue(company.bookable_on_website)

    def test_add_washing_material_creates_row(self):
        company = make_management_company()
        self.client.post(self.url, {
            'action': 'add_washing_material', 'management_company_id': company.pk,
            'title': 'Dish tabs', 'quantity': '20',
        })
        material = WashingMaterial.objects.get(company=company)
        self.assertEqual(material.title, 'Dish tabs')
        self.assertEqual(material.quantity, 20)

    def test_add_washing_material_requires_a_title(self):
        company = make_management_company()
        self.client.post(self.url, {
            'action': 'add_washing_material', 'management_company_id': company.pk, 'title': '',
        })
        self.assertFalse(WashingMaterial.objects.filter(company=company).exists())

    def test_add_washing_material_defaults_quantity_to_one(self):
        company = make_management_company()
        self.client.post(self.url, {
            'action': 'add_washing_material', 'management_company_id': company.pk, 'title': 'Fabric softener',
        })
        material = WashingMaterial.objects.get(company=company)
        self.assertEqual(material.quantity, 1)

    def test_delete_washing_material_removes_it(self):
        company = make_management_company()
        material = WashingMaterial.objects.create(company=company, title='Soap', quantity=5)
        self.client.post(self.url, {'action': 'delete_washing_material', 'washing_material_id': material.pk})
        self.assertFalse(WashingMaterial.objects.filter(pk=material.pk).exists())

    def test_delete_management_company_also_deletes_its_washing_materials(self):
        company = make_management_company()
        WashingMaterial.objects.create(company=company, title='Soap', quantity=5)
        self.client.post(self.url, {'action': 'delete_management_company', 'management_company_id': company.pk})
        self.assertFalse(WashingMaterial.objects.exists())

    def test_update_management_company_saves_fields(self):
        company = make_management_company(name='Original Co')
        response = self.client.post(self.url, {
            'action': 'update_management_company', 'management_company_id': company.pk,
            'name': 'Renamed Co',
        })
        self.assertRedirects(response, f'{self.url}?panel=people')
        company.refresh_from_db()
        self.assertEqual(company.name, 'Renamed Co')

    def test_update_management_company_round_trips_a_maintenance_contact(self):
        company = make_management_company()
        self.client.post(self.url, {
            'action': 'update_management_company', 'management_company_id': company.pk,
            'name': company.name,
            'maintenance_name': 'Maint Person', 'maintenance_email': 'maint@example.com',
            'maintenance_phone': '+351922222222',
        })
        company.refresh_from_db()
        self.assertEqual(company.maintenance_name, 'Maint Person')
        self.assertEqual(company.maintenance_email, 'maint@example.com')
        self.assertEqual(company.maintenance_phone, '+351922222222')
        # Clearing it back out (blank fields posted) should genuinely clear it, not leave it stuck.
        self.client.post(self.url, {
            'action': 'update_management_company', 'management_company_id': company.pk,
            'name': company.name,
        })
        company.refresh_from_db()
        self.assertEqual(company.maintenance_name, '')

    def test_delete_management_company_missing_row_shows_friendly_message(self):
        response = self.client.post(self.url, {
            'action': 'delete_management_company', 'management_company_id': 999999,
        }, follow=True)
        messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('no longer exists' in m for m in messages))

    def test_delete_management_company_orphans_its_properties(self):
        # ManagementCompany's FKs from Property are SET_NULL, not PROTECT (unlike
        # PropertyOwnership.owner above) - deleting one should cleanly null out any property
        # pointing at it rather than erroring.
        company = make_management_company()
        booked = Property.objects.create(
            title='Booked By Deleted Co', short_title='BOOKEDDEL', booking_company=company,
        )
        cleaned = Property.objects.create(
            title='Cleaned By Deleted Co', short_title='CLEANEDDEL', cleaning_company=company,
        )
        self.client.post(self.url, {'action': 'delete_management_company', 'management_company_id': company.pk})
        self.assertFalse(ManagementCompany.objects.filter(pk=company.pk).exists())
        booked.refresh_from_db()
        cleaned.refresh_from_db()
        self.assertIsNone(booked.booking_company_id)
        self.assertIsNone(cleaned.cleaning_company_id)

    def test_add_booking_condition(self):
        self.client.post(self.url, {
            'action': 'add_booking_condition', 'text': 'No smoking indoors.', 'order': '2',
        })
        condition = BookingCondition.objects.get(text='No smoking indoors.')
        self.assertEqual(condition.order, 2)

    def test_add_booking_condition_requires_text(self):
        self.client.post(self.url, {'action': 'add_booking_condition', 'text': '', 'order': '1'})
        self.assertFalse(BookingCondition.objects.filter(order=1).exists())

    def test_update_booking_condition_saves_fields(self):
        condition = BookingCondition.objects.create(text='Original text', order=1)
        response = self.client.post(self.url, {
            'action': 'update_booking_condition', 'condition_id': condition.pk,
            'text': 'Updated text', 'order': '5',
        })
        self.assertRedirects(response, f'{self.url}?panel=bookings')
        condition.refresh_from_db()
        self.assertEqual(condition.text, 'Updated text')
        self.assertEqual(condition.order, 5)

    def test_delete_booking_condition(self):
        condition = BookingCondition.objects.create(text='Delete me', order=1)
        self.client.post(self.url, {'action': 'delete_booking_condition', 'condition_id': condition.pk})
        self.assertFalse(BookingCondition.objects.filter(pk=condition.pk).exists())

    def test_booking_conditions_context_ordered(self):
        BookingCondition.objects.create(text='Second', order=2)
        BookingCondition.objects.create(text='First', order=1)
        response = self.client.get(self.url)
        texts = [c.text for c in response.context['booking_conditions']]
        self.assertEqual(texts, ['First', 'Second'])

    def test_add_faq(self):
        self.client.post(self.url, {
            'action': 'add_faq', 'question': 'Can I have a late check-out?',
            'answer': 'Yes, ask us.', 'order': '2',
        })
        faq = FAQ.objects.get(question='Can I have a late check-out?')
        self.assertEqual(faq.answer, 'Yes, ask us.')
        self.assertEqual(faq.order, 2)
        self.assertIsNone(faq.location)

    def test_add_faq_requires_question_and_answer(self):
        self.client.post(self.url, {'action': 'add_faq', 'question': '', 'answer': ''})
        self.assertEqual(FAQ.objects.count(), 0)

    def test_add_faq_with_a_location(self):
        location = make_location(title='Parking Location')
        self.client.post(self.url, {
            'action': 'add_faq', 'question': 'Is there parking?', 'answer': 'Yes, private parking.',
            'location': location.pk,
        })
        faq = FAQ.objects.get(question='Is there parking?')
        self.assertEqual(faq.location, location)

    def test_update_faq_saves_fields_and_can_clear_location(self):
        location = make_location(title='FAQ Update Location')
        faq = FAQ.objects.create(question='Original', answer='Original answer', order=1, location=location)
        response = self.client.post(self.url, {
            'action': 'update_faq', 'faq_id': faq.pk, 'question': 'Updated',
            'answer': 'Updated answer', 'order': '5', 'location': '',
        })
        self.assertRedirects(response, f'{self.url}?panel=bookings')
        faq.refresh_from_db()
        self.assertEqual(faq.question, 'Updated')
        self.assertEqual(faq.answer, 'Updated answer')
        self.assertEqual(faq.order, 5)
        self.assertIsNone(faq.location)

    def test_delete_faq(self):
        faq = FAQ.objects.create(question='Delete me', answer='Answer', order=1)
        self.client.post(self.url, {'action': 'delete_faq', 'faq_id': faq.pk})
        self.assertFalse(FAQ.objects.filter(pk=faq.pk).exists())

    def test_faqs_context_ordered(self):
        FAQ.objects.create(question='Second', answer='A', order=2)
        FAQ.objects.create(question='First', answer='A', order=1)
        response = self.client.get(self.url)
        questions = [f.question for f in response.context['faqs']]
        self.assertEqual(questions, ['First', 'Second'])

    def test_update_payment_settings_saves_fields(self):
        response = self.client.post(self.url, {
            'action': 'update_payment_settings',
            'high_season_commission_percent': '16.00', 'low_season_commission_percent': '11.00',
            'high_season_start_month': '5', 'high_season_end_month': '9',
            'klt_commission_share_percent': '100.00', 'vat_rate_percent': '23.00',
            'cleaning_surcharge_one_bedroom': '10.00', 'cleaning_surcharge_multi_bedroom': '15.00',
            'cleaning_high_occupancy_surcharge': '15.00', 'meet_greet_fee': '28.00',
            'extra_bed_fee': '25.00', 'regular_payout_days_after_arrival': '5',
            'charge_vat_on_low_season_direct_commission': 'on',
        })
        self.assertRedirects(response, f'{self.url}?panel=payments')
        settings = PaymentSettings.load()
        self.assertEqual(settings.regular_payout_days_after_arrival, 5)
        self.assertTrue(settings.charge_vat_on_low_season_direct_commission)
        # Omitted checkbox - unchecked, not left at its previous value, matching how the other
        # boolean-checkbox settings on this page (e.g. OWNER_BOOLEAN_FIELDS) already behave.
        self.assertFalse(settings.charge_vat_on_low_season_platform_commission)


class CleaningTaskValidRangeTests(TestCase):
    """staff/utils.py::cleaning_task_valid_range() - both ends inclusive for both task types
    (2026-08-26 fix: a turnover clean can land on the same day the next guest arrives - a normal
    same-day turnover - so the upper bound must not be exclusive)."""

    def setUp(self):
        self.property = Property.objects.create(title='Valid Range Property', short_title='VALIDRANGE')
        self.guest = Guest.objects.create(first_name='Ana', last_name='Silva', email='valid-range@example.com')
        self.start = date.today() + timedelta(days=30)
        self.end = self.start + timedelta(days=10)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Departure.objects.create(booking=self.booking, clean=True)
        self.task = CleaningTask.objects.get(booking=self.booking, task_type='turnover')

    def test_turnover_uncapped_when_no_next_booking(self):
        min_date, max_date = cleaning_task_valid_range(self.task)
        self.assertEqual(min_date, self.end)
        self.assertIsNone(max_date)

    def test_turnover_capped_by_next_confirmed_booking_arrival(self):
        next_arrival = self.end + timedelta(days=2)
        next_guest = Guest.objects.create(first_name='Bo', last_name='Costa', email='next-guest@example.com')
        Booking.objects.create(
            property=self.property, guest=next_guest, arrival_date=next_arrival,
            departure_date=next_arrival + timedelta(days=5), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        min_date, max_date = cleaning_task_valid_range(self.task)
        self.assertEqual(max_date, next_arrival)

    def test_mid_stay_range_is_a_day_either_side_of_the_stays_middle(self):
        # self.booking is a 10-night stay (setUp) - middle day is arrival + 10 // 2 = +5.
        extra = Extra.objects.create(
            booking=self.booking, mid_stay_clean=True, mid_stay_clean_date=self.start + timedelta(days=5),
        )
        mid_task = CleaningTask.objects.get(booking=self.booking, task_type='mid_stay')
        min_date, max_date = cleaning_task_valid_range(mid_task)
        self.assertEqual(min_date, self.start + timedelta(days=4))
        self.assertEqual(max_date, self.start + timedelta(days=6))


class ApplyManualTaskDateTests(TestCase):
    """staff/utils.py::apply_manual_task_date() - the shared validate-and-override helper behind
    a calendar drag, the popup's save button, and the booking detail page's embedded planner."""

    def setUp(self):
        self.property = Property.objects.create(title='Manual Date Property', short_title='MANUALDATE')
        self.guest = Guest.objects.create(first_name='Ana', last_name='Silva', email='manual-date@example.com')
        self.start = date.today() + timedelta(days=30)
        self.end = self.start + timedelta(days=10)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Departure.objects.create(booking=self.booking, clean=True)
        self.task = CleaningTask.objects.get(booking=self.booking, task_type='turnover')
        next_guest = Guest.objects.create(first_name='Bo', last_name='Costa', email='manual-date-next@example.com')
        self.next_arrival = self.end + timedelta(days=2)
        Booking.objects.create(
            property=self.property, guest=next_guest, arrival_date=self.next_arrival,
            departure_date=self.next_arrival + timedelta(days=5), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_date_within_window_is_accepted(self):
        error = apply_manual_task_date(self.task, self.end + timedelta(days=1))
        self.assertIsNone(error)
        self.task.refresh_from_db()
        self.assertEqual(self.task.date, self.end + timedelta(days=1))
        self.assertTrue(self.task.manually_scheduled)
        self.assertEqual(self.task.auto_date, self.end)

    def test_date_exactly_on_next_arrival_day_is_accepted(self):
        """The exact boundary bug reported 2026-08-26: same-day turnover (clean before the next
        guest checks in later that day) must be allowed, not rejected as 'too late'."""
        error = apply_manual_task_date(self.task, self.next_arrival)
        self.assertIsNone(error)
        self.task.refresh_from_db()
        self.assertEqual(self.task.date, self.next_arrival)

    def test_date_after_next_arrival_is_rejected(self):
        error = apply_manual_task_date(self.task, self.next_arrival + timedelta(days=1))
        self.assertIsNotNone(error)
        self.task.refresh_from_db()
        self.assertEqual(self.task.date, self.end)
        self.assertFalse(self.task.manually_scheduled)

    def test_date_before_departure_is_rejected(self):
        error = apply_manual_task_date(self.task, self.end - timedelta(days=1))
        self.assertIsNotNone(error)
        self.task.refresh_from_db()
        self.assertFalse(self.task.manually_scheduled)


class StaffCleaningCalendarEndpointTests(TestCase):
    """The drag-to-reschedule calendar's own page/endpoints - superuser-only, not gated by any
    StaffRole flag like the day-list rota."""

    def setUp(self):
        self.superuser = User.objects.create_user(
            username='calendarsuperuser', password='pw', is_staff=True, is_superuser=True,
        )
        self.role = StaffRole.objects.create(name='Cleaner', is_cleaning_staff=True)
        self.cleaner = User.objects.create_user(username='calendarcleaner', password='pw', is_staff=True)
        StaffProfile.objects.create(user=self.cleaner, role=self.role)

        self.location = Location.objects.create(
            title='Calendar Location', street='Rua Test', zip_code='8200-001', city='Albufeira',
            coordinates='37.0,-8.2', map_link='https://maps.example.com', color='#123456',
        )
        self.property = Property.objects.create(
            title='Calendar Property', short_title='CALPROP', location=self.location,
        )
        self.guest = Guest.objects.create(first_name='Ana', last_name='Silva', email='calendar-task@example.com')
        self.start = date.today() + timedelta(days=30)
        self.end = self.start + timedelta(days=10)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start, departure_date=self.end,
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Departure.objects.create(booking=self.booking, clean=True)
        self.task = CleaningTask.objects.get(booking=self.booking, task_type='turnover')

    def test_calendar_page_requires_superuser(self):
        self.client.login(username='calendarcleaner', password='pw')
        response = self.client.get(reverse('staff:cleaning_calendar'))
        self.assertEqual(response.status_code, 403)

    def test_events_feed_returns_location_color_and_range(self):
        self.client.login(username='calendarsuperuser', password='pw')
        response = self.client.get(reverse('staff:cleaning_calendar_events'), {
            'start': self.start.isoformat() + 'T00:00:00', 'end': (self.end + timedelta(days=30)).isoformat() + 'T00:00:00',
        })
        self.assertEqual(response.status_code, 200)
        events = response.json()
        event = next(e for e in events if e['id'] == self.task.pk)
        self.assertEqual(event['extendedProps']['location_id'], self.location.pk)
        self.assertEqual(event['extendedProps']['location_color'], '#123456')
        self.assertEqual(event['extendedProps']['min_date'], self.end.isoformat())
        self.assertEqual(event['extendedProps']['assigned_to'], [])
        self.assertEqual(event['extendedProps']['team'], 1)
        self.assertEqual(event['title'], 'NEW CALPROP — Turnover')

    def test_events_feed_title_uses_short_title_and_drops_new_once_assigned(self):
        self.task.assigned_to.set([self.cleaner])
        self.client.login(username='calendarsuperuser', password='pw')
        response = self.client.get(reverse('staff:cleaning_calendar_events'), {
            'start': self.start.isoformat() + 'T00:00:00', 'end': (self.end + timedelta(days=30)).isoformat() + 'T00:00:00',
        })
        event = next(e for e in response.json() if e['id'] == self.task.pk)
        self.assertEqual(event['title'], 'CALPROP — Turnover')

    def test_move_view_accepts_valid_date_and_sets_override(self):
        self.client.login(username='calendarsuperuser', password='pw')
        new_date = self.end + timedelta(days=1)
        response = self.client.post(
            reverse('staff:cleaning_calendar_move', kwargs={'pk': self.task.pk}), {'date': new_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.date, new_date)
        self.assertTrue(self.task.manually_scheduled)

    def test_move_view_rejects_date_before_departure(self):
        self.client.login(username='calendarsuperuser', password='pw')
        response = self.client.post(
            reverse('staff:cleaning_calendar_move', kwargs={'pk': self.task.pk}),
            {'date': (self.end - timedelta(days=1)).isoformat()},
        )
        self.assertEqual(response.status_code, 400)
        self.task.refresh_from_db()
        self.assertFalse(self.task.manually_scheduled)

    def test_events_feed_excludes_dismissed_tasks(self):
        self.task.status = 'dismissed'
        self.task.save()
        self.client.login(username='calendarsuperuser', password='pw')
        response = self.client.get(reverse('staff:cleaning_calendar_events'), {
            'start': self.start.isoformat() + 'T00:00:00', 'end': (self.end + timedelta(days=30)).isoformat() + 'T00:00:00',
        })
        ids = [e['id'] for e in response.json()]
        self.assertNotIn(self.task.pk, ids)

    def test_dismiss_view_requires_superuser(self):
        freshen_task = CleaningTask.objects.create(
            booking=self.booking, task_type='freshen', date=self.start - timedelta(days=1),
        )
        self.client.login(username='calendarcleaner', password='pw')
        response = self.client.post(reverse('staff:cleaning_task_dismiss', kwargs={'pk': freshen_task.pk}))
        self.assertEqual(response.status_code, 403)

    def test_dismiss_view_dismisses_a_pending_freshen_task(self):
        freshen_task = CleaningTask.objects.create(
            booking=self.booking, task_type='freshen', date=self.start - timedelta(days=1),
        )
        self.client.login(username='calendarsuperuser', password='pw')
        response = self.client.post(reverse('staff:cleaning_task_dismiss', kwargs={'pk': freshen_task.pk}))
        self.assertEqual(response.status_code, 200)
        freshen_task.refresh_from_db()
        self.assertEqual(freshen_task.status, 'dismissed')
        self.assertEqual(freshen_task.dismissed_reason, 'manual')
        self.assertEqual(freshen_task.dismissed_by, self.superuser)

    def test_dismiss_view_rejects_a_non_freshen_task(self):
        self.client.login(username='calendarsuperuser', password='pw')
        response = self.client.post(reverse('staff:cleaning_task_dismiss', kwargs={'pk': self.task.pk}))
        self.assertEqual(response.status_code, 400)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'pending')

    def test_move_view_requires_superuser(self):
        self.client.login(username='calendarcleaner', password='pw')
        response = self.client.post(
            reverse('staff:cleaning_calendar_move', kwargs={'pk': self.task.pk}),
            {'date': (self.end + timedelta(days=1)).isoformat()},
        )
        self.assertEqual(response.status_code, 403)

    def test_detail_view_includes_departure_and_planner_html(self):
        self.client.login(username='calendarsuperuser', password='pw')
        response = self.client.get(reverse('staff:cleaning_task_detail', kwargs={'pk': self.task.pk}))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('Departure', data['departure_html'])
        self.assertIn('calendarcleaner', data['planner_html'])

    def test_detail_view_includes_next_arrival_for_turnover(self):
        next_guest = Guest.objects.create(first_name='Bo', last_name='Costa', email='calendar-next@example.com')
        next_arrival = self.end + timedelta(days=2)
        Booking.objects.create(
            property=self.property, guest=next_guest, arrival_date=next_arrival,
            departure_date=next_arrival + timedelta(days=5), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.client.login(username='calendarsuperuser', password='pw')
        response = self.client.get(reverse('staff:cleaning_task_detail', kwargs={'pk': self.task.pk}))
        data = response.json()
        self.assertIn('Next Arrival', data['arrival_html'])
        self.assertIn('Bo Costa', data['arrival_html'])

    def test_detail_view_shows_arrived_and_departing_for_mid_stay(self):
        Arrival.objects.create(booking=self.booking, meet_greet=True)
        Extra.objects.create(
            booking=self.booking, mid_stay_clean=True, mid_stay_clean_date=self.start + timedelta(days=3),
        )
        mid_task = CleaningTask.objects.get(booking=self.booking, task_type='mid_stay')
        self.client.login(username='calendarsuperuser', password='pw')
        response = self.client.get(reverse('staff:cleaning_task_detail', kwargs={'pk': mid_task.pk}))
        data = response.json()
        self.assertIn('Arrived', data['arrival_html'])
        self.assertIn('Silva', data['arrival_html'])  # this booking's own guest, not a next arrival
        self.assertIn('Departing', data['departure_html'])
        self.assertNotIn('>Departure<', data['departure_html'])

    def test_detail_view_shows_last_clean_and_next_arrival_for_freshen(self):
        earlier = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start - timedelta(days=30),
            departure_date=self.start - timedelta(days=20), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Departure.objects.create(booking=earlier, clean=True)
        freshen_task = CleaningTask.objects.create(
            booking=self.booking, task_type='freshen', date=self.start - timedelta(days=1),
        )
        self.client.login(username='calendarsuperuser', password='pw')
        response = self.client.get(reverse('staff:cleaning_task_detail', kwargs={'pk': freshen_task.pk}))
        data = response.json()
        self.assertIn('Last Clean', data['departure_html'])
        self.assertNotIn('No earlier clean on record', data['departure_html'])
        self.assertIn('Next Arrival', data['arrival_html'])
        self.assertIn('Silva', data['arrival_html'])

    def test_detail_view_last_clean_empty_message_when_no_prior_clean(self):
        freshen_task = CleaningTask.objects.create(
            booking=self.booking, task_type='freshen', date=self.start - timedelta(days=1),
        )
        self.client.login(username='calendarsuperuser', password='pw')
        response = self.client.get(reverse('staff:cleaning_task_detail', kwargs={'pk': freshen_task.pk}))
        data = response.json()
        self.assertIn('No earlier clean on record', data['departure_html'])

    def test_save_view_sets_date_and_assignees(self):
        self.client.login(username='calendarsuperuser', password='pw')
        new_date = self.end + timedelta(days=1)
        response = self.client.post(reverse('staff:cleaning_task_save', kwargs={'pk': self.task.pk}), {
            'date': new_date.isoformat(), 'assigned_to': [self.cleaner.pk],
        })
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.date, new_date)
        self.assertTrue(self.task.manually_scheduled)
        self.assertEqual(list(self.task.assigned_to.all()), [self.cleaner])

    def test_save_view_rejects_invalid_date_but_still_reports_error(self):
        self.client.login(username='calendarsuperuser', password='pw')
        response = self.client.post(reverse('staff:cleaning_task_save', kwargs={'pk': self.task.pk}), {
            'date': (self.end - timedelta(days=1)).isoformat(),
        })
        self.assertEqual(response.status_code, 400)
        self.task.refresh_from_db()
        self.assertFalse(self.task.manually_scheduled)

    def test_save_view_setting_assignees_alone_does_not_flip_manually_scheduled(self):
        self.client.login(username='calendarsuperuser', password='pw')
        response = self.client.post(reverse('staff:cleaning_task_save', kwargs={'pk': self.task.pk}), {
            'date': self.task.date.isoformat(), 'assigned_to': [self.cleaner.pk],
        })
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db()
        self.assertFalse(self.task.manually_scheduled)

    def test_save_view_sets_team(self):
        self.client.login(username='calendarsuperuser', password='pw')
        response = self.client.post(reverse('staff:cleaning_task_save', kwargs={'pk': self.task.pk}), {
            'date': self.task.date.isoformat(), 'assigned_to': [self.cleaner.pk], 'team': '2',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['team'], 2)
        self.task.refresh_from_db()
        self.assertEqual(self.task.team, 2)

    def test_save_view_falls_back_to_team_1_when_missing_or_invalid(self):
        self.client.login(username='calendarsuperuser', password='pw')
        self.task.team = 3
        self.task.save(update_fields=['team'])
        response = self.client.post(reverse('staff:cleaning_task_save', kwargs={'pk': self.task.pk}), {
            'date': self.task.date.isoformat(), 'team': 'not-a-number',
        })
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.team, 1)

    def test_detail_view_planner_html_shows_current_team_selected(self):
        self.task.team = 2
        self.task.save(update_fields=['team'])
        self.client.login(username='calendarsuperuser', password='pw')
        response = self.client.get(reverse('staff:cleaning_task_detail', kwargs={'pk': self.task.pk}))
        data = response.json()
        self.assertIn('<option value="2" selected>Group 2</option>', data['planner_html'])


class ComputeArrivalEtaTests(TestCase):
    """staff/utils.py::compute_arrival_eta() - the per-method buffer math the check-ins calendar's
    displayed time is built from."""

    def setUp(self):
        CheckinSettings.objects.all().delete()
        self.settings = CheckinSettings.load()
        self.settings.faro_buffer_minutes = 90
        self.settings.lisbon_buffer_minutes = 270
        self.settings.transit_buffer_minutes = 30
        self.settings.save()

        self.property = Property.objects.create(title='ETA Property', short_title='ETAPROP')
        self.guest = Guest.objects.create(first_name='Ana', last_name='Silva', email='eta@example.com')
        self.start = date.today() + timedelta(days=30)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start,
            departure_date=self.start + timedelta(days=7), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_faro_flight_adds_faro_buffer(self):
        Arrival.objects.create(booking=self.booking, method=TravelMethod.FLIGHT_FARO, time=time(14, 0), meet_greet=True)
        computed, is_all_day = compute_arrival_eta(self.booking)
        self.assertFalse(is_all_day)
        self.assertEqual(computed, time(15, 30))

    def test_lisbon_flight_adds_lisbon_buffer(self):
        Arrival.objects.create(booking=self.booking, method=TravelMethod.FLIGHT_LISBON, time=time(10, 0), meet_greet=True)
        computed, is_all_day = compute_arrival_eta(self.booking)
        self.assertFalse(is_all_day)
        self.assertEqual(computed, time(14, 30))

    def test_bus_adds_transit_buffer(self):
        Arrival.objects.create(booking=self.booking, method=TravelMethod.BUS, time=time(12, 0), meet_greet=True)
        computed, is_all_day = compute_arrival_eta(self.booking)
        self.assertEqual(computed, time(12, 30))

    def test_train_adds_transit_buffer(self):
        Arrival.objects.create(booking=self.booking, method=TravelMethod.TRAIN, time=time(9, 15), meet_greet=True)
        computed, is_all_day = compute_arrival_eta(self.booking)
        self.assertEqual(computed, time(9, 45))

    def test_driving_uses_given_time_verbatim(self):
        Arrival.objects.create(booking=self.booking, method=TravelMethod.DRIVING, time=time(16, 0), meet_greet=True)
        computed, is_all_day = compute_arrival_eta(self.booking)
        self.assertFalse(is_all_day)
        self.assertEqual(computed, time(16, 0))

    def test_other_method_falls_back_to_standard_checkin_time(self):
        Arrival.objects.create(booking=self.booking, method=TravelMethod.OTHER, time=None, meet_greet=True)
        computed, is_all_day = compute_arrival_eta(self.booking)
        self.assertFalse(is_all_day)
        self.assertEqual(computed, time(14, 0))

    def test_no_time_given_falls_back_to_standard_checkin_time(self):
        Arrival.objects.create(booking=self.booking, method=TravelMethod.FLIGHT_FARO, time=None, meet_greet=True)
        computed, is_all_day = compute_arrival_eta(self.booking)
        self.assertFalse(is_all_day)
        self.assertEqual(computed, time(14, 0))

    def test_no_arrival_row_falls_back_to_standard_checkin_time(self):
        computed, is_all_day = compute_arrival_eta(self.booking)
        self.assertFalse(is_all_day)
        self.assertEqual(computed, time(14, 0))

    def test_falls_back_to_property_company_standard_checkin_time_when_set(self):
        company = ManagementCompany.objects.create(name='ETA Co', standard_checkin_time=time(16, 30))
        self.property.booking_company = company
        self.property.save()
        computed, is_all_day = compute_arrival_eta(self.booking)
        self.assertFalse(is_all_day)
        self.assertEqual(computed, time(16, 30))


class CheckinSyncTests(TestCase):
    """staff/utils.py::sync_checkins_for_booking() - Checkin stays in sync with Arrival/Booking
    via post_save signals (staff/signals.py), same reasoning CleaningTask's own sync gives."""

    def setUp(self):
        CheckinSettings.objects.all().delete()
        self.settings = CheckinSettings.load()
        self.settings.faro_buffer_minutes = 90
        self.settings.key_box_prep_time = time(10, 0)
        self.settings.welcome_visit_time = time(10, 0)
        self.settings.save()

        self.property = Property.objects.create(title='Checkin Sync Property', short_title='CHECKINSYNC')
        self.guest = Guest.objects.create(first_name='Ana', last_name='Silva', email='checkin-sync@example.com')
        self.start = date.today() + timedelta(days=30)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start,
            departure_date=self.start + timedelta(days=7), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_arrival_save_creates_arrival_checkin(self):
        Arrival.objects.create(booking=self.booking, method=TravelMethod.FLIGHT_FARO, time=time(14, 0), meet_greet=True)
        checkin = Checkin.objects.get(booking=self.booking, task_type='arrival')
        self.assertEqual(checkin.date, self.start)
        self.assertEqual(checkin.time, time(15, 30))
        self.assertEqual(checkin.status, 'pending')

    def test_self_check_in_creates_key_box_and_welcome_visit(self):
        Arrival.objects.create(
            booking=self.booking, method=TravelMethod.FLIGHT_FARO, time=time(14, 0),
            self_check_in=True, meet_greet=False,
        )
        key_box = Checkin.objects.get(booking=self.booking, task_type='key_box')
        welcome = Checkin.objects.get(booking=self.booking, task_type='welcome_visit')
        self.assertEqual(key_box.date, self.start)
        self.assertEqual(key_box.time, time(10, 0))
        self.assertEqual(welcome.date, self.start + timedelta(days=1))
        self.assertEqual(welcome.time, time(10, 0))

    def test_non_self_check_in_creates_no_extra_rows(self):
        Arrival.objects.create(
            booking=self.booking, method=TravelMethod.FLIGHT_FARO, time=time(14, 0),
            self_check_in=False, meet_greet=True,
        )
        self.assertFalse(Checkin.objects.filter(booking=self.booking, task_type__in=['key_box', 'welcome_visit']).exists())

    def test_turning_off_self_check_in_removes_pending_extra_rows(self):
        arrival = Arrival.objects.create(
            booking=self.booking, method=TravelMethod.FLIGHT_FARO, time=time(14, 0),
            self_check_in=True, meet_greet=False,
        )
        self.assertTrue(Checkin.objects.filter(booking=self.booking, task_type='key_box').exists())

        arrival.self_check_in = False
        arrival.save()

        self.assertFalse(Checkin.objects.filter(booking=self.booking, task_type__in=['key_box', 'welcome_visit']).exists())

    def test_turning_off_self_check_in_does_not_remove_a_done_key_box_row(self):
        arrival = Arrival.objects.create(
            booking=self.booking, method=TravelMethod.FLIGHT_FARO, time=time(14, 0),
            self_check_in=True, meet_greet=False,
        )
        key_box = Checkin.objects.get(booking=self.booking, task_type='key_box')
        key_box.status = 'done'
        key_box.save()

        arrival.self_check_in = False
        arrival.save()

        key_box.refresh_from_db()
        self.assertEqual(key_box.status, 'done')

    def test_cancelling_a_booking_removes_pending_checkin_rows(self):
        Arrival.objects.create(
            booking=self.booking, method=TravelMethod.FLIGHT_FARO, time=time(14, 0),
            self_check_in=True, meet_greet=False,
        )
        self.assertEqual(Checkin.objects.filter(booking=self.booking).count(), 3)

        self.booking.enquiry_status = 'Cancelled by staff'
        self.booking.save(update_fields=['enquiry_status'])

        self.assertFalse(Checkin.objects.filter(booking=self.booking).exists())

    def test_cancelling_a_booking_does_not_remove_a_done_row(self):
        Arrival.objects.create(booking=self.booking, method=TravelMethod.FLIGHT_FARO, time=time(14, 0), meet_greet=True)
        checkin = Checkin.objects.get(booking=self.booking, task_type='arrival')
        checkin.status = 'done'
        checkin.save()

        self.booking.enquiry_status = 'Cancelled by staff'
        self.booking.save(update_fields=['enquiry_status'])

        checkin.refresh_from_db()
        self.assertEqual(checkin.status, 'done')

    def test_checkins_on_calendar_false_creates_no_checkin_rows(self):
        company = make_management_company(name='No Checkin Calendar Co', checkins_on_calendar=False)
        self.property.booking_company = company
        self.property.save()
        Arrival.objects.create(booking=self.booking, method=TravelMethod.FLIGHT_FARO, time=time(14, 0), meet_greet=True)
        self.assertFalse(Checkin.objects.filter(booking=self.booking).exists())

    def test_turning_checkins_on_calendar_off_removes_pending_rows_on_next_sync(self):
        company = make_management_company(name='No Checkin Calendar Co 2', checkins_on_calendar=True)
        self.property.booking_company = company
        self.property.save()
        Arrival.objects.create(booking=self.booking, method=TravelMethod.FLIGHT_FARO, time=time(14, 0), meet_greet=True)
        self.assertTrue(Checkin.objects.filter(booking=self.booking).exists())

        company.checkins_on_calendar = False
        company.save()
        self.booking.save()  # re-trigger the signal-driven sync, same as any other unrelated save
        self.assertFalse(Checkin.objects.filter(booking=self.booking, status='pending').exists())

    def test_checkins_on_calendar_false_does_not_remove_a_done_row(self):
        company = make_management_company(name='No Checkin Calendar Co 3', checkins_on_calendar=True)
        self.property.booking_company = company
        self.property.save()
        Arrival.objects.create(booking=self.booking, method=TravelMethod.FLIGHT_FARO, time=time(14, 0), meet_greet=True)
        checkin = Checkin.objects.get(booking=self.booking, task_type='arrival')
        checkin.status = 'done'
        checkin.save()

        company.checkins_on_calendar = False
        company.save()
        self.booking.save()
        checkin.refresh_from_db()
        self.assertEqual(checkin.status, 'done')

    def test_no_booking_company_is_unaffected_by_checkins_on_calendar(self):
        # Property.booking_company is None here (never set) - the flag only applies when a
        # company is actually tracked.
        Arrival.objects.create(booking=self.booking, method=TravelMethod.FLIGHT_FARO, time=time(14, 0), meet_greet=True)
        self.assertTrue(Checkin.objects.filter(booking=self.booking, task_type='arrival').exists())

    def test_dragging_a_time_survives_an_unrelated_resync(self):
        Arrival.objects.create(booking=self.booking, method=TravelMethod.FLIGHT_FARO, time=time(14, 0), meet_greet=True)
        checkin = Checkin.objects.get(booking=self.booking, task_type='arrival')
        error = apply_manual_checkin_time(checkin, time(16, 0))
        self.assertIsNone(error)

        # Re-trigger the sync with nothing about the arrival time actually changed.
        self.booking.save()

        checkin.refresh_from_db()
        self.assertEqual(checkin.time, time(16, 0))
        self.assertTrue(checkin.manually_scheduled)

    def test_editing_arrival_time_after_a_drag_wins_over_the_drag(self):
        arrival = Arrival.objects.create(booking=self.booking, method=TravelMethod.FLIGHT_FARO, time=time(14, 0), meet_greet=True)
        checkin = Checkin.objects.get(booking=self.booking, task_type='arrival')
        apply_manual_checkin_time(checkin, time(16, 0))

        arrival.time = time(18, 0)
        arrival.save()

        checkin.refresh_from_db()
        self.assertEqual(checkin.time, time(19, 30))  # 18:00 + 90min faro buffer
        self.assertFalse(checkin.manually_scheduled)

    def test_settings_change_resyncs_non_manual_rows(self):
        Arrival.objects.create(booking=self.booking, method=TravelMethod.FLIGHT_FARO, time=time(14, 0), meet_greet=True)
        self.settings.faro_buffer_minutes = 120
        self.settings.save()

        checkin = Checkin.objects.get(booking=self.booking, task_type='arrival')
        self.assertEqual(checkin.time, time(16, 0))

    def test_settings_change_does_not_touch_a_manually_scheduled_row(self):
        Arrival.objects.create(booking=self.booking, method=TravelMethod.FLIGHT_FARO, time=time(14, 0), meet_greet=True)
        checkin = Checkin.objects.get(booking=self.booking, task_type='arrival')
        apply_manual_checkin_time(checkin, time(20, 0))

        self.settings.faro_buffer_minutes = 120
        self.settings.save()

        checkin.refresh_from_db()
        self.assertEqual(checkin.time, time(20, 0))


class CheckinCalendarEndpointTests(TestCase):
    """The check-ins calendar's own page/endpoints - gated by can_view_checkins_calendar, not
    superuser_required, since this is meant for day-to-day staff use."""

    def setUp(self):
        role = StaffRole.objects.create(name='Checkin Manager', can_view_checkins_calendar=True)
        self.staffer = User.objects.create_user(username='checkinstaffer', password='pw', is_staff=True)
        StaffProfile.objects.create(user=self.staffer, role=role)
        self.no_access_staffer = User.objects.create_user(username='noaccessstaffer', password='pw', is_staff=True)

        CheckinSettings.objects.all().delete()
        settings_obj = CheckinSettings.load()
        settings_obj.faro_buffer_minutes = 90
        settings_obj.save()

        self.location = Location.objects.create(
            title='Checkin Location', street='Rua Test', zip_code='8200-001', city='Albufeira',
            coordinates='37.0,-8.2', map_link='https://maps.example.com', color='#654321',
        )
        self.property = Property.objects.create(
            title='Checkin Endpoint Property', short_title='CHECKINEP', location=self.location,
        )
        self.guest = Guest.objects.create(first_name='Bo', last_name='Costa', email='checkin-endpoint@example.com', phone='+351911111111')
        self.start = date.today() + timedelta(days=30)
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start,
            departure_date=self.start + timedelta(days=7), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Arrival.objects.create(booking=self.booking, method=TravelMethod.FLIGHT_FARO, time=time(14, 0), meet_greet=True)
        self.checkin = Checkin.objects.get(booking=self.booking, task_type='arrival')

    def test_calendar_page_requires_permission(self):
        self.client.login(username='noaccessstaffer', password='pw')
        response = self.client.get(reverse('staff:checkins_calendar'))
        self.assertEqual(response.status_code, 403)

    def test_calendar_page_loads_for_permitted_staffer(self):
        self.client.login(username='checkinstaffer', password='pw')
        response = self.client.get(reverse('staff:checkins_calendar'))
        self.assertEqual(response.status_code, 200)

    def test_events_feed_returns_computed_time_and_location_color(self):
        self.client.login(username='checkinstaffer', password='pw')
        response = self.client.get(reverse('staff:checkins_calendar_events'), {
            'start': self.start.isoformat() + 'T00:00:00', 'end': (self.start + timedelta(days=1)).isoformat() + 'T00:00:00',
        })
        self.assertEqual(response.status_code, 200)
        event = next(e for e in response.json() if e['id'] == self.checkin.pk)
        self.assertEqual(event['start'], f"{self.start.isoformat()}T15:30:00")
        self.assertFalse(event['allDay'])
        self.assertEqual(event['extendedProps']['location_color'], '#654321')
        self.assertIn('CHECKINEP', event['title'])
        self.assertIn('Bo Costa', event['title'])

    def test_events_feed_marks_no_meet_greet(self):
        arrival = self.booking.arrival
        arrival.meet_greet = False
        arrival.save()
        self.client.login(username='checkinstaffer', password='pw')
        response = self.client.get(reverse('staff:checkins_calendar_events'), {
            'start': self.start.isoformat() + 'T00:00:00', 'end': (self.start + timedelta(days=1)).isoformat() + 'T00:00:00',
        })
        event = next(e for e in response.json() if e['id'] == self.checkin.pk)
        self.assertFalse(event['extendedProps']['meet_greet'])

    def test_events_feed_renders_no_eta_at_standard_checkin_time(self):
        arrival = self.booking.arrival
        arrival.method = TravelMethod.OTHER
        arrival.time = None
        arrival.save()
        self.client.login(username='checkinstaffer', password='pw')
        response = self.client.get(reverse('staff:checkins_calendar_events'), {
            'start': self.start.isoformat() + 'T00:00:00', 'end': (self.start + timedelta(days=1)).isoformat() + 'T00:00:00',
        })
        event = next(e for e in response.json() if e['id'] == self.checkin.pk)
        self.assertFalse(event['allDay'])
        self.assertEqual(event['start'], f"{self.start.isoformat()}T14:00:00")
        self.assertIn('14:00', event['title'])

    def test_close_but_distinct_times_are_pushed_a_full_block_apart(self):
        # Regression 2026-08-28: two checkins one minute apart still numerically overlap at the
        # 29-minute event-block duration checkins_calendar.js renders every timed event at, so the
        # old exact-same-minute-only stagger left them overlapping and FullCalendar split them
        # side-by-side instead of stacking them. self.checkin's real computed time is 15:30 (Faro
        # flight + 90min buffer, see setUp); a second checkin one minute later should render a full
        # EVENT_BLOCK_MINUTES (30) after it, not one minute after it.
        other_guest = Guest.objects.create(first_name='Second', last_name='Guest', email='second-checkin@example.com')
        other_booking = Booking.objects.create(
            property=self.property, guest=other_guest, arrival_date=self.start,
            departure_date=self.start + timedelta(days=7), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=1, children=0, babies=0, last_updated=timezone.now(),
        )
        Arrival.objects.create(booking=other_booking, method=TravelMethod.DRIVING, time=time(15, 31), meet_greet=True)
        other_checkin = Checkin.objects.get(booking=other_booking, task_type='arrival')

        self.client.login(username='checkinstaffer', password='pw')
        response = self.client.get(reverse('staff:checkins_calendar_events'), {
            'start': self.start.isoformat() + 'T00:00:00', 'end': (self.start + timedelta(days=1)).isoformat() + 'T00:00:00',
        })
        payload = response.json()
        first_event = next(e for e in payload if e['id'] == self.checkin.pk)
        second_event = next(e for e in payload if e['id'] == other_checkin.pk)
        self.assertEqual(first_event['start'], f"{self.start.isoformat()}T15:30:00")
        self.assertEqual(second_event['start'], f"{self.start.isoformat()}T16:00:00")
        # The stored/real time and the title's own label are untouched by the render nudge.
        other_checkin.refresh_from_db()
        self.assertEqual(other_checkin.time, time(15, 31))
        self.assertIn('15:31', second_event['title'])

    def test_three_close_times_stack_in_sequential_blocks(self):
        other_guest_a = Guest.objects.create(first_name='Second', last_name='Guest', email='third-a@example.com')
        booking_a = Booking.objects.create(
            property=self.property, guest=other_guest_a, arrival_date=self.start,
            departure_date=self.start + timedelta(days=7), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=1, children=0, babies=0, last_updated=timezone.now(),
        )
        Arrival.objects.create(booking=booking_a, method=TravelMethod.DRIVING, time=time(15, 35), meet_greet=True)
        other_guest_b = Guest.objects.create(first_name='Third', last_name='Guest', email='third-b@example.com')
        booking_b = Booking.objects.create(
            property=self.property, guest=other_guest_b, arrival_date=self.start,
            departure_date=self.start + timedelta(days=7), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=1, children=0, babies=0, last_updated=timezone.now(),
        )
        Arrival.objects.create(booking=booking_b, method=TravelMethod.DRIVING, time=time(15, 38), meet_greet=True)
        checkin_a = Checkin.objects.get(booking=booking_a, task_type='arrival')
        checkin_b = Checkin.objects.get(booking=booking_b, task_type='arrival')

        self.client.login(username='checkinstaffer', password='pw')
        response = self.client.get(reverse('staff:checkins_calendar_events'), {
            'start': self.start.isoformat() + 'T00:00:00', 'end': (self.start + timedelta(days=1)).isoformat() + 'T00:00:00',
        })
        payload = response.json()
        starts = {
            self.checkin.pk: f"{self.start.isoformat()}T15:30:00",
            checkin_a.pk: f"{self.start.isoformat()}T16:00:00",
            checkin_b.pk: f"{self.start.isoformat()}T16:30:00",
        }
        for checkin_id, expected_start in starts.items():
            event = next(e for e in payload if e['id'] == checkin_id)
            self.assertEqual(event['start'], expected_start)

    def test_move_view_accepts_a_same_day_time_change(self):
        self.client.login(username='checkinstaffer', password='pw')
        response = self.client.post(
            reverse('staff:checkins_calendar_move', kwargs={'pk': self.checkin.pk}), {'time': '17:00'},
        )
        self.assertEqual(response.status_code, 200)
        self.checkin.refresh_from_db()
        self.assertEqual(self.checkin.time, time(17, 0))
        self.assertTrue(self.checkin.manually_scheduled)

    def test_move_view_rejects_an_invalid_time(self):
        self.client.login(username='checkinstaffer', password='pw')
        response = self.client.post(
            reverse('staff:checkins_calendar_move', kwargs={'pk': self.checkin.pk}), {'time': 'not-a-time'},
        )
        self.assertEqual(response.status_code, 400)

    def test_detail_view_shows_arrival_popup_content(self):
        self.client.login(username='checkinstaffer', password='pw')
        response = self.client.get(reverse('staff:checkin_detail', kwargs={'pk': self.checkin.pk}))
        self.assertEqual(response.status_code, 200)
        html = response.json()['popup_html']
        self.assertIn('Bo Costa', html)
        self.assertIn('+351911111111', html)
        self.assertIn('Flight to Faro', html)

    def test_detail_view_hides_deposit_for_a_returning_guest(self):
        past_booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=self.start - timedelta(days=100),
            departure_date=self.start - timedelta(days=93), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.client.login(username='checkinstaffer', password='pw')
        response = self.client.get(reverse('staff:checkin_detail', kwargs={'pk': self.checkin.pk}))
        html = response.json()['popup_html']
        self.assertIn('Not required for a returning guest', html)

    def test_detail_view_hides_deposit_when_platform_does_not_take_them(self):
        Platform.objects.get_or_create(name='Airbnb', defaults={'take_security_deposits': False})
        self.booking.enquiry_source = 'Airbnb'
        self.booking.save(update_fields=['enquiry_source'])
        self.client.login(username='checkinstaffer', password='pw')
        response = self.client.get(reverse('staff:checkin_detail', kwargs={'pk': self.checkin.pk}))
        html = response.json()['popup_html']
        self.assertIn("doesn't take security deposits", html)
        self.assertNotIn('due', html)

    def test_detail_view_shows_deposit_when_platform_does_take_them(self):
        platform, _ = Platform.objects.get_or_create(name='Airbnb')
        platform.take_security_deposits = True
        platform.save()
        self.booking.enquiry_source = 'Airbnb'
        self.booking.save(update_fields=['enquiry_source'])
        self.client.login(username='checkinstaffer', password='pw')
        response = self.client.get(reverse('staff:checkin_detail', kwargs={'pk': self.checkin.pk}))
        html = response.json()['popup_html']
        self.assertIn('due', html)

    def test_detail_view_shows_deposit_for_an_unrecognised_source(self):
        # enquiry_source 'Website' (a direct booking) never matches a Platform by name, so the
        # platform waiver simply doesn't apply - same as today's behaviour before this feature.
        self.client.login(username='checkinstaffer', password='pw')
        response = self.client.get(reverse('staff:checkin_detail', kwargs={'pk': self.checkin.pk}))
        html = response.json()['popup_html']
        self.assertIn('due', html)

    def test_detail_view_hides_deposit_for_a_guest_outside_uk_eu(self):
        self.guest.country = 'US'
        self.guest.save(update_fields=['country'])
        self.client.login(username='checkinstaffer', password='pw')
        response = self.client.get(reverse('staff:checkin_detail', kwargs={'pk': self.checkin.pk}))
        html = response.json()['popup_html']
        self.assertIn('outside the UK/EU', html)
        self.assertNotIn('due', html)

    def test_detail_view_shows_deposit_for_a_guest_inside_uk_eu(self):
        self.guest.country = 'FR'
        self.guest.save(update_fields=['country'])
        self.client.login(username='checkinstaffer', password='pw')
        response = self.client.get(reverse('staff:checkin_detail', kwargs={'pk': self.checkin.pk}))
        html = response.json()['popup_html']
        self.assertIn('due', html)

    def test_detail_view_shows_deposit_for_a_guest_with_no_country_on_record(self):
        # Unknown isn't the same as confirmed-international - see compute_deposit_waiver's own
        # docstring. self.guest.country defaults to unset in setUp, so this is really just
        # confirming the baseline case stays unwaived.
        self.assertFalse(self.guest.country)
        self.client.login(username='checkinstaffer', password='pw')
        response = self.client.get(reverse('staff:checkin_detail', kwargs={'pk': self.checkin.pk}))
        html = response.json()['popup_html']
        self.assertIn('due', html)

    def test_detail_view_shows_key_box_popup_content(self):
        arrival = self.booking.arrival
        arrival.self_check_in = True
        arrival.save()
        key_box = Checkin.objects.get(booking=self.booking, task_type='key_box')
        self.client.login(username='checkinstaffer', password='pw')
        response = self.client.get(reverse('staff:checkin_detail', kwargs={'pk': key_box.pk}))
        html = response.json()['popup_html']
        self.assertIn('Key box', html)
        self.assertIn('CHECKINEP', html)

    def test_toggle_done_marks_and_unmarks(self):
        self.client.login(username='checkinstaffer', password='pw')
        url = reverse('staff:checkin_toggle_done', kwargs={'pk': self.checkin.pk})
        response = self.client.post(url)
        self.assertEqual(response.json()['status'], 'done')
        self.checkin.refresh_from_db()
        self.assertEqual(self.checkin.status, 'done')
        self.assertEqual(self.checkin.completed_by, self.staffer)

        response = self.client.post(url)
        self.assertEqual(response.json()['status'], 'pending')
        self.checkin.refresh_from_db()
        self.assertEqual(self.checkin.status, 'pending')
        self.assertIsNone(self.checkin.completed_by)

    def test_save_view_sets_extras_and_deposit_flags(self):
        self.client.login(username='checkinstaffer', password='pw')
        response = self.client.post(reverse('staff:checkin_save', kwargs={'pk': self.checkin.pk}), {
            'extras_collected': 'true', 'deposit_collected': 'true',
        })
        self.assertEqual(response.status_code, 200)
        self.checkin.refresh_from_db()
        self.assertTrue(self.checkin.extras_collected)
        self.assertTrue(self.checkin.deposit_collected)

    def test_save_view_rejects_non_arrival_checkin(self):
        arrival = self.booking.arrival
        arrival.self_check_in = True
        arrival.save()
        key_box = Checkin.objects.get(booking=self.booking, task_type='key_box')
        self.client.login(username='checkinstaffer', password='pw')
        response = self.client.post(reverse('staff:checkin_save', kwargs={'pk': key_box.pk}), {
            'extras_collected': 'true',
        })
        self.assertEqual(response.status_code, 400)


class StaffReportsTests(TestCase):
    """staff/reports.py::booking_report_rows and the Reports page it feeds - see
    [[project_klt_web_reporting]] in memory for the phased plan this is step one of."""

    def setUp(self):
        self.owner = make_owner(is_paid_regularly=False)
        self.company = make_management_company(finances_managed_internally=True)
        self.property = Property.objects.create(
            title='Reports Property', short_title='REPPROP', owner=self.owner,
            cleaning_company=self.company, booking_company=self.company, standard_cleaning_fee=Decimal('80.00'),
        )
        PropertySpec.objects.create(property=self.property, bedrooms=2)
        self.guest = Guest.objects.create(first_name='Rep', last_name='Orter', email='reports-guest@example.com')
        self.settings = PaymentSettings.load()
        self.settings.meet_greet_fee = Decimal('28.00')
        self.settings.save()
        self.today = timezone.now().date()

    def _make_booking(self, arrival_offset, departure_offset, is_owner=False, property=None):
        booking = Booking.objects.create(
            property=property or self.property, guest=self.guest,
            arrival_date=self.today + timedelta(days=arrival_offset),
            departure_date=self.today + timedelta(days=departure_offset),
            is_owner=is_owner, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        if not is_owner:
            Charge.objects.create(booking=booking, basic_rental=Decimal('300.00'))
        Departure.objects.create(booking=booking, clean=True)
        Arrival.objects.create(booking=booking, meet_greet=True)
        return booking

    def test_row_includes_every_report_column_for_a_guest_booking(self):
        booking = self._make_booking(5, 9)
        rows = booking_report_rows(self.today, self.today + timedelta(days=10))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['booking'], booking)
        self.assertEqual(row['nights'], 4)
        self.assertIsNotNone(row['rental_to_owner'])
        self.assertEqual(row['basic_rental'], Decimal('300.00'))
        self.assertEqual(row['platform_fee'], Decimal('0'))
        self.assertEqual(row['platform_fee_vat'], Decimal('0'))
        self.assertGreater(row['clean_cost'], Decimal('0'))
        self.assertEqual(row['meet_greet_cost'], Decimal('28.00'))
        self.assertEqual(row['maintenance_cost'], Decimal('0'))
        # net_revenue = rental_to_owner - clean - meet_greet - maintenance (the waterfall) -
        # rental_to_owner itself has NOT already deducted clean/meet-greet the way the old
        # owner_balance figure did, so this must differ from rental_to_owner whenever those are
        # nonzero (they are here - a real cleaning_company is set).
        expected_net = row['rental_to_owner'] - row['clean_cost'] - row['meet_greet_cost'] - row['maintenance_cost']
        self.assertEqual(row['net_revenue'], expected_net)
        self.assertNotEqual(row['net_revenue'], row['rental_to_owner'])
        # commission/klt_net_commission (2026-08-30): rental_to_owner = basic_rental - commission,
        # and klt_net_commission is Post-IVA (commission minus whatever VAT the agency has to
        # remit on it) - never equal to commission unless commission_vat happens to be zero, and
        # never deducted from rental_to_owner itself (see this dict's own KLT-internal framing).
        self.assertIsNotNone(row['commission'])
        self.assertEqual(row['rental_to_owner'], row['basic_rental'] - row['commission'])
        self.assertIsNotNone(row['klt_net_commission'])
        self.assertLessEqual(row['klt_net_commission'], row['commission'])
        # klt_net_revenue (2026-08-30) - KLT's own separate bottom line, never confused with
        # Owner Net Revenue above even though both are called "net revenue": commission-derived
        # earnings TO klt plus the Clean/Meet & Greet/Maintenance fees it charges, not a deduction
        # from anyone.
        expected_klt_net_revenue = row['klt_net_commission'] + row['clean_cost'] + row['meet_greet_cost'] + row['maintenance_cost']
        self.assertEqual(row['klt_net_revenue'], expected_klt_net_revenue)
        self.assertNotEqual(row['klt_net_revenue'], row['net_revenue'])

    def test_owner_stay_still_reports_a_real_clean_cost_with_no_payout(self):
        """compute_owner_payout always reports an owner stay as unavailable, but the clean still
        genuinely happens and must still show up as a real cost - see clean_fee()'s own
        docstring. The rental-derived figures (basic_rental/platform_fee/platform_fee_vat/
        rental_to_owner) go None together, but net_revenue does NOT join them - with only
        deductions and no rental income to report, it comes out as a genuine negative number
        instead (per Thomas 2026-08-30, so an owner stay's real costs aren't hidden behind a
        blank dash)."""
        self._make_booking(5, 9, is_owner=True)
        rows = booking_report_rows(self.today, self.today + timedelta(days=10))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIsNone(row['rental_to_owner'])
        self.assertIsNone(row['basic_rental'])
        self.assertIsNone(row['platform_fee'])
        self.assertIsNone(row['platform_fee_vat'])
        self.assertGreater(row['clean_cost'], Decimal('0'))
        self.assertEqual(row['maintenance_cost'], Decimal('0'))
        expected_net = -(row['clean_cost'] + row['meet_greet_cost'] + row['maintenance_cost'])
        self.assertEqual(row['net_revenue'], expected_net)
        self.assertLess(row['net_revenue'], Decimal('0'))
        # klt_net_revenue is the opposite sign story here on purpose - the same clean/meet & greet
        # costs that make Owner Net Revenue negative are genuine earnings TO klt (it still
        # performs and bills the clean regardless of whether there's a payout), so with
        # klt_net_commission unavailable (None -> treated as 0) this comes out positive instead.
        self.assertIsNone(row['klt_net_commission'])
        expected_klt_net_revenue = row['clean_cost'] + row['meet_greet_cost'] + row['maintenance_cost']
        self.assertEqual(row['klt_net_revenue'], expected_klt_net_revenue)
        self.assertGreater(row['klt_net_revenue'], Decimal('0'))

    def test_maintenance_cost_and_net_revenue_reflect_the_bookings_memo(self):
        self._make_booking(5, 9)
        AdHocService.objects.create(property=self.property, description='AC repair', cost=Decimal('50.00'))
        rows = booking_report_rows(self.today, self.today + timedelta(days=10))
        row = rows[0]
        self.assertEqual(row['maintenance_cost'], Decimal('50.00'))
        expected_net = row['rental_to_owner'] - row['clean_cost'] - row['meet_greet_cost'] - Decimal('50.00')
        self.assertEqual(row['net_revenue'], expected_net)

    def test_property_filter_excludes_other_properties(self):
        other_property = Property.objects.create(
            title='Other Reports Property', short_title='OTHREP', owner=self.owner,
            cleaning_company=self.company, booking_company=self.company,
        )
        PropertySpec.objects.create(property=other_property, bedrooms=1)
        self._make_booking(5, 9)
        self._make_booking(6, 10, property=other_property)
        rows = booking_report_rows(self.today, self.today + timedelta(days=10), properties=[self.property])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['booking'].property, self.property)

    def test_report_totals_sums_and_excludes_unavailable_figures(self):
        self._make_booking(5, 9)
        self._make_booking(6, 10, is_owner=True)
        rows = booking_report_rows(self.today, self.today + timedelta(days=10))
        totals = report_totals(rows)
        self.assertEqual(totals['basic_rental'], Decimal('300.00'))
        self.assertGreater(totals['clean_cost'], Decimal('0'))


class StaffReportsViewTests(TestCase):
    def setUp(self):
        role = StaffRole.objects.create(name='Reports Viewer', can_view_reports=True)
        self.staffer = User.objects.create_user(username='reportsstaffer', password='pw', is_staff=True)
        StaffProfile.objects.create(user=self.staffer, role=role)
        self.no_access_staffer = User.objects.create_user(username='noaccessreports', password='pw', is_staff=True)

        self.owner = make_owner(is_paid_regularly=False)
        self.company = make_management_company(finances_managed_internally=True)
        self.property = Property.objects.create(
            title='Reports View Property', short_title='REPVIEW', owner=self.owner,
            cleaning_company=self.company, booking_company=self.company, standard_cleaning_fee=Decimal('80.00'),
        )
        PropertySpec.objects.create(property=self.property, bedrooms=2)
        guest = Guest.objects.create(first_name='View', last_name='Ing', email='reports-view@example.com')
        self.today = timezone.now().date()
        self.booking = Booking.objects.create(
            property=self.property, guest=guest, arrival_date=self.today + timedelta(days=2),
            departure_date=self.today + timedelta(days=6), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=self.booking, basic_rental=Decimal('300.00'))
        Departure.objects.create(booking=self.booking, clean=True)
        Arrival.objects.create(booking=self.booking, meet_greet=True)

    def test_requires_permission(self):
        self.client.login(username='noaccessreports', password='pw')
        response = self.client.get(reverse('staff:reports'))
        self.assertEqual(response.status_code, 403)

    def test_page_renders_with_all_columns_by_default(self):
        """Explicit start/end, not the view's own current-month default - the fixture booking
        (today + 2 days) can fall in next month whenever this runs in the last couple of days of
        a real month, which silently emptied the table and failed this assertion - caught live
        2026-08-30."""
        self.client.login(username='reportsstaffer', password='pw')
        response = self.client.get(reverse('staff:reports'), {
            'start': self.today.isoformat(), 'end': (self.today + timedelta(days=10)).isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.property.short_title)
        self.assertContains(response, 'Rental to Owner')
        self.assertContains(response, 'Basic rental')
        self.assertContains(response, 'Platform fee')
        self.assertContains(response, 'Clean')
        self.assertContains(response, 'Meet &amp; Greet')
        self.assertContains(response, 'Maintenance')
        self.assertContains(response, 'Owner Net Revenue')
        self.assertContains(response, 'KLT Net Revenue')

    def test_narrowing_columns_selects_only_the_requested_column(self):
        """Column labels always appear once as checkbox text regardless of selection, so this
        checks the table's actual selected_columns context rather than page text presence."""
        self.client.login(username='reportsstaffer', password='pw')
        response = self.client.get(reverse('staff:reports'), {'columns': 'clean_cost'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_columns'], {'clean_cost'})


class MonthlyRevenueRowsTests(TestCase):
    """staff/monthly_reports.py::monthly_revenue_rows() - the Monthly tab's own aggregation,
    mirroring the reference "Monthly Business Report" workbook's Revenue sheet. Fixed 2024/2023
    dates throughout (not today-relative) - see StaffReportsViewTests.
    test_page_renders_with_all_columns_by_default's own docstring for why a relative date bit a
    similar test here once already."""

    def setUp(self):
        self.owner = make_owner(is_paid_regularly=False)
        self.property = Property.objects.create(
            title='Monthly Report Property', short_title='MONTHREV', owner=self.owner,
        )
        self.guest = Guest.objects.create(first_name='Month', last_name='Ly', email='monthly-guest@example.com')

    def _make_booking(self, arrival, enquiry_source='Website', is_owner=False):
        return Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=arrival,
            departure_date=arrival + timedelta(days=4), is_owner=is_owner,
            enquiry_status='Booking confirmed', enquiry_source=enquiry_source,
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_direct_booking_paid_equals_received(self):
        booking = self._make_booking(date(2024, 3, 5), enquiry_source='Website')
        Charge.objects.create(booking=booking, basic_rental=Decimal('300.00'))

        rows = monthly_revenue_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Direct']['paid_by_guest'], Decimal('300.00'))
        self.assertEqual(march['groups']['Direct']['rcvd_by_klt'], Decimal('300.00'))

    def test_platform_booking_splits_paid_and_received(self):
        booking = self._make_booking(date(2024, 3, 10), enquiry_source='Airbnb')
        PlatformPayout.objects.create(
            booking=booking, gross_amount=Decimal('400.00'), platform_commission=Decimal('60.00'),
            payout_amount=Decimal('340.00'),
        )

        rows = monthly_revenue_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Airbnb']['paid_by_guest'], Decimal('400.00'))
        self.assertEqual(march['groups']['Airbnb']['rcvd_by_klt'], Decimal('340.00'))
        self.assertNotEqual(
            march['groups']['Airbnb']['paid_by_guest'], march['groups']['Airbnb']['rcvd_by_klt'],
        )

    def test_owner_stay_excluded_entirely(self):
        booking = self._make_booking(date(2024, 3, 15), enquiry_source='Owner Suite', is_owner=True)
        Charge.objects.create(booking=booking, basic_rental=Decimal('999.00'))

        rows = monthly_revenue_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Total']['paid_by_guest'], Decimal('0'))

    def test_group_percent_share_of_the_months_total(self):
        direct = self._make_booking(date(2024, 3, 5), enquiry_source='Website')
        Charge.objects.create(booking=direct, basic_rental=Decimal('300.00'))
        airbnb = self._make_booking(date(2024, 3, 10), enquiry_source='Airbnb')
        PlatformPayout.objects.create(
            booking=airbnb, gross_amount=Decimal('100.00'), platform_commission=Decimal('15.00'),
            payout_amount=Decimal('85.00'),
        )

        rows = monthly_revenue_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Direct']['paid_by_guest_pct'], Decimal('75.00'))
        self.assertEqual(march['groups']['Airbnb']['paid_by_guest_pct'], Decimal('25.00'))
        self.assertEqual(march['groups']['Total']['paid_by_guest_pct'], Decimal('100.00'))

    def test_year_over_year_delta_is_a_real_change_not_the_prior_years_raw_figure(self):
        last_year_booking = self._make_booking(date(2023, 3, 5), enquiry_source='Website')
        Charge.objects.create(booking=last_year_booking, basic_rental=Decimal('200.00'))
        this_year_booking = self._make_booking(date(2024, 3, 5), enquiry_source='Website')
        Charge.objects.create(booking=this_year_booking, basic_rental=Decimal('350.00'))

        rows = monthly_revenue_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Direct']['paid_by_guest'], Decimal('350.00'))
        self.assertEqual(march['groups']['Direct']['paid_by_guest_delta'], Decimal('150.00'))

    def test_a_month_with_no_bookings_has_zero_figures_and_raises_nothing(self):
        rows = monthly_revenue_rows(2024)
        july = next(r for r in rows if r['month'].month == 7)
        self.assertEqual(july['groups']['Total']['paid_by_guest'], Decimal('0'))
        self.assertEqual(july['groups']['Total']['paid_by_guest_pct'], Decimal('0'))

    def test_a_platform_booking_missing_its_payout_row_is_skipped_not_guessed(self):
        self._make_booking(date(2024, 3, 20), enquiry_source='Vrbo')  # no PlatformPayout created

        rows = monthly_revenue_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Vrbo']['paid_by_guest'], Decimal('0'))


class StaffReportsMonthlyViewTests(TestCase):
    def setUp(self):
        role = StaffRole.objects.create(name='Monthly Reports Viewer', can_view_reports=True)
        self.staffer = User.objects.create_user(username='monthlyreportsstaffer', password='pw', is_staff=True)
        StaffProfile.objects.create(user=self.staffer, role=role)
        self.no_access_staffer = User.objects.create_user(username='noaccessmonthly', password='pw', is_staff=True)

        owner = make_owner(is_paid_regularly=False)
        property = Property.objects.create(title='Monthly View Property', short_title='MONTHVIEW', owner=owner)
        guest = Guest.objects.create(first_name='View', last_name='Monthly', email='monthly-view@example.com')
        booking = Booking.objects.create(
            property=property, guest=guest, arrival_date=date(2024, 6, 10), departure_date=date(2024, 6, 14),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=booking, basic_rental=Decimal('500.00'))

    def test_requires_permission(self):
        self.client.login(username='noaccessmonthly', password='pw')
        response = self.client.get(reverse('staff:reports_monthly'))
        self.assertEqual(response.status_code, 403)

    def test_page_renders_with_the_requested_year(self):
        self.client.login(username='monthlyreportsstaffer', password='pw')
        response = self.client.get(reverse('staff:reports_monthly'), {'year': '2024'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['year'], 2024)
        self.assertContains(response, 'June')
        self.assertContains(response, 'Direct')
        self.assertContains(response, 'Airbnb')
        self.assertContains(response, '&euro;500.00')

    def test_invalid_year_falls_back_to_the_current_year(self):
        self.client.login(username='monthlyreportsstaffer', password='pw')
        response = self.client.get(reverse('staff:reports_monthly'), {'year': 'not-a-year'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['year'], timezone.now().date().year)

    def test_no_groups_param_shows_every_group(self):
        """Same 'a fresh visit shows everything' convention as the Bookings tab's own
        selected_columns - a bookmarked/shared URL with no groups=... behaves the same for
        everyone who opens it."""
        self.client.login(username='monthlyreportsstaffer', password='pw')
        response = self.client.get(reverse('staff:reports_monthly'), {'year': '2024'})
        self.assertEqual(response.context['selected_groups'], {'Direct', 'Airbnb', 'Booking.com', 'Vrbo'})

    def test_groups_param_narrows_the_visible_columns(self):
        """'Airbnb' text alone would still appear via the filter form's own checkbox label
        regardless of selection, so this checks the table header cell specifically."""
        self.client.login(username='monthlyreportsstaffer', password='pw')
        response = self.client.get(reverse('staff:reports_monthly'), {'year': '2024', 'groups': 'Direct'})
        self.assertEqual(response.context['selected_groups'], {'Direct'})
        self.assertNotContains(response, '<th colspan="6">Airbnb</th>')
        self.assertContains(response, '<th colspan="6">Direct</th>')
        # Total is never a toggle option - always shown regardless of the groups filter.
        self.assertContains(response, '<th colspan="6">Total</th>')

    def test_trend_chart_renders_on_the_page(self):
        self.client.login(username='monthlyreportsstaffer', password='pw')
        response = self.client.get(reverse('staff:reports_monthly'), {'year': '2024'})
        self.assertContains(response, 'id="revenue-trend-chart"')
        self.assertContains(response, 'id="revenue-trend-data"')


class MonthlyStaysRowsTests(TestCase):
    """staff/monthly_reports.py::monthly_stays_rows() - same shape as MonthlyRevenueRowsTests
    above (Arrivals/Nights in place of money), fixed 2024/2023 dates for the same
    month-boundary-flakiness reason. One merged Stays sheet with an include_owner toggle rather
    than the reference workbook's separate Guest Stays/All Stays sheets, per Thomas 2026-08-30."""

    def setUp(self):
        self.owner = make_owner(is_paid_regularly=False)
        self.property = Property.objects.create(
            title='Stays Report Property', short_title='STAYSREV', owner=self.owner,
        )
        self.guest = Guest.objects.create(first_name='Stays', last_name='Rev', email='guest-stays-rev@example.com')

    def _make_booking(self, arrival, departure=None, enquiry_source='Website', is_owner=False):
        return Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=arrival,
            departure_date=departure or arrival + timedelta(days=4), is_owner=is_owner,
            enquiry_status='Booking confirmed', enquiry_source=enquiry_source,
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_counts_one_arrival_and_its_nights_for_a_direct_booking(self):
        self._make_booking(date(2024, 3, 5), date(2024, 3, 12), enquiry_source='Website')

        rows = monthly_stays_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Direct']['arrivals'], 1)
        self.assertEqual(march['groups']['Direct']['nights'], 7)
        self.assertEqual(march['groups']['Total']['arrivals'], 1)
        self.assertEqual(march['groups']['Total']['nights'], 7)

    def test_owner_stay_excluded_by_default(self):
        self._make_booking(date(2024, 3, 15), enquiry_source='Owner Suite', is_owner=True)

        rows = monthly_stays_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Total']['arrivals'], 0)
        self.assertNotIn('Owner', march['groups'])

    def test_include_owner_adds_an_owner_column_and_grows_the_total(self):
        self._make_booking(date(2024, 3, 5), enquiry_source='Website')  # a Direct guest stay too
        self._make_booking(date(2024, 3, 15), enquiry_source='Owner Suite', is_owner=True)

        rows = monthly_stays_rows(2024, include_owner=True)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Owner']['arrivals'], 1)
        # Unlike the Direct/Airbnb/etc display toggles (cosmetic only), Owner genuinely changes
        # what Total means - it must include the owner stay here, not just show it as a column.
        self.assertEqual(march['groups']['Total']['arrivals'], 2)

    def test_counted_regardless_of_whether_charge_or_platformpayout_exists_yet(self):
        """Unlike revenue, a stay counts as soon as it's a real booking - no money record needed
        yet, since the stay itself already happened (or will) independently of pricing."""
        self._make_booking(date(2024, 3, 20), enquiry_source='Airbnb')  # no PlatformPayout row

        rows = monthly_stays_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Airbnb']['arrivals'], 1)

    def test_group_percent_share_of_the_months_total(self):
        self._make_booking(date(2024, 3, 5), date(2024, 3, 9), enquiry_source='Website')  # 4 nights
        self._make_booking(date(2024, 3, 10), date(2024, 3, 14), enquiry_source='Airbnb')  # 4 nights

        rows = monthly_stays_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Direct']['arrivals_pct'], Decimal('50.00'))
        self.assertEqual(march['groups']['Airbnb']['arrivals_pct'], Decimal('50.00'))

    def test_year_over_year_delta_is_a_real_change_not_the_prior_years_raw_figure(self):
        self._make_booking(date(2023, 3, 5), enquiry_source='Website')
        self._make_booking(date(2024, 3, 5), enquiry_source='Website')
        self._make_booking(date(2024, 3, 12), enquiry_source='Website')

        rows = monthly_stays_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Direct']['arrivals'], 2)
        self.assertEqual(march['groups']['Direct']['arrivals_delta'], 1)

    def test_a_month_with_no_bookings_has_zero_figures_and_raises_nothing(self):
        rows = monthly_stays_rows(2024)
        july = next(r for r in rows if r['month'].month == 7)
        self.assertEqual(july['groups']['Total']['arrivals'], 0)
        self.assertEqual(july['groups']['Total']['arrivals_pct'], Decimal('0'))


class StaffReportsStaysViewTests(TestCase):
    def setUp(self):
        role = StaffRole.objects.create(name='Stays Viewer', can_view_reports=True)
        self.staffer = User.objects.create_user(username='staysstaffer', password='pw', is_staff=True)
        StaffProfile.objects.create(user=self.staffer, role=role)
        self.no_access_staffer = User.objects.create_user(username='noaccessstays', password='pw', is_staff=True)

        owner = make_owner(is_paid_regularly=False)
        property = Property.objects.create(title='Stays View Property', short_title='STAYSVIEW', owner=owner)
        guest = Guest.objects.create(first_name='View', last_name='Stays', email='guest-stays-view@example.com')
        Booking.objects.create(
            property=property, guest=guest, arrival_date=date(2024, 6, 10), departure_date=date(2024, 6, 14),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_requires_permission(self):
        self.client.login(username='noaccessstays', password='pw')
        response = self.client.get(reverse('staff:reports_stays'))
        self.assertEqual(response.status_code, 403)

    def test_page_renders_with_the_requested_year(self):
        self.client.login(username='staysstaffer', password='pw')
        response = self.client.get(reverse('staff:reports_stays'), {'year': '2024'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['year'], 2024)
        self.assertContains(response, 'June')
        self.assertContains(response, 'Arrivals')
        self.assertContains(response, 'Nights')

    def test_groups_param_narrows_the_visible_columns(self):
        self.client.login(username='staysstaffer', password='pw')
        response = self.client.get(reverse('staff:reports_stays'), {'year': '2024', 'groups': 'Direct'})
        self.assertEqual(response.context['selected_groups'], {'Direct'})
        self.assertNotContains(response, '<th colspan="6">Airbnb</th>')
        self.assertContains(response, '<th colspan="6">Direct</th>')

    def test_include_owner_defaults_off(self):
        self.client.login(username='staysstaffer', password='pw')
        response = self.client.get(reverse('staff:reports_stays'), {'year': '2024'})
        self.assertFalse(response.context['include_owner'])
        self.assertNotContains(response, '<th colspan="6">Owner</th>')

    def test_include_owner_param_shows_the_owner_column(self):
        self.client.login(username='staysstaffer', password='pw')
        response = self.client.get(reverse('staff:reports_stays'), {'year': '2024', 'include_owner': 'on'})
        self.assertTrue(response.context['include_owner'])
        self.assertContains(response, '<th colspan="6">Owner</th>')

    def test_trend_chart_renders_on_the_page(self):
        self.client.login(username='staysstaffer', password='pw')
        response = self.client.get(reverse('staff:reports_stays'), {'year': '2024'})
        self.assertContains(response, 'id="stays-trend-chart"')
        self.assertContains(response, 'id="stays-trend-data"')


class RevenueTrendRowsTests(TestCase):
    """staff/monthly_reports.py::revenue_trend_rows() - the "since records began" growth-over-time
    series behind the Revenue tab's chart, deliberately its own real-DB-scoped function (not
    year-scoped like monthly_revenue_rows) - see that function's own docstring."""

    def setUp(self):
        self.owner = make_owner(is_paid_regularly=False)
        self.property = Property.objects.create(title='Trend Property', short_title='TRENDREV', owner=self.owner)
        self.guest = Guest.objects.create(first_name='Trend', last_name='Rev', email='trend-rev@example.com')

    def _make_booking(self, arrival, enquiry_source='Website', is_owner=False):
        return Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=arrival,
            departure_date=arrival + timedelta(days=4), is_owner=is_owner,
            enquiry_status='Booking confirmed', enquiry_source=enquiry_source,
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_empty_when_no_guest_bookings_exist(self):
        self.assertEqual(revenue_trend_rows(), [])

    def test_spans_from_earliest_to_latest_arrival_month_inclusive(self):
        """Every month in between is present too, even ones with no bookings at all (zero-filled)
        - a chart needs a continuous timeline, not gaps wherever a month happened to be empty."""
        early = self._make_booking(date(2020, 1, 15))
        Charge.objects.create(booking=early, basic_rental=Decimal('100.00'))
        late = self._make_booking(date(2020, 4, 10))
        Charge.objects.create(booking=late, basic_rental=Decimal('200.00'))

        rows = revenue_trend_rows()
        months = [r['month'] for r in rows]
        self.assertEqual(months, [date(2020, 1, 1), date(2020, 2, 1), date(2020, 3, 1), date(2020, 4, 1)])
        self.assertEqual(rows[0]['rcvd_by_klt'], Decimal('100.00'))
        self.assertEqual(rows[1]['rcvd_by_klt'], Decimal('0'))
        self.assertEqual(rows[3]['rcvd_by_klt'], Decimal('200.00'))

    def test_owner_stay_excluded(self):
        """An owner-only booking contributes no revenue and (unlike stays_trend_rows) doesn't
        even extend the date range - revenue_trend_rows()'s bounds are guest-only by design,
        since an owner stay never generates revenue - so a real guest booking is needed here too
        for there to be any row at all to assert against."""
        guest_booking = self._make_booking(date(2020, 6, 1))
        Charge.objects.create(booking=guest_booking, basic_rental=Decimal('150.00'))
        self._make_booking(date(2020, 6, 10), enquiry_source='Owner Suite', is_owner=True)

        rows = revenue_trend_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['rcvd_by_klt'], Decimal('150.00'))


class StaysTrendRowsTests(TestCase):
    def setUp(self):
        self.owner = make_owner(is_paid_regularly=False)
        self.property = Property.objects.create(title='Stays Trend Property', short_title='TRENDSTAY', owner=self.owner)
        self.guest = Guest.objects.create(first_name='Trend', last_name='Stay', email='trend-stay@example.com')

    def _make_booking(self, arrival, enquiry_source='Website', is_owner=False):
        return Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=arrival,
            departure_date=arrival + timedelta(days=4), is_owner=is_owner,
            enquiry_status='Booking confirmed', enquiry_source=enquiry_source,
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_empty_when_no_bookings_exist(self):
        self.assertEqual(stays_trend_rows(), [])

    def test_bounds_include_owner_only_months_even_when_excluded_from_the_figures(self):
        """The date range itself is taken across every booking (see the function's own
        docstring), but a month whose only booking is an owner stay still shows zero when
        include_owner=False - correct, not a bug."""
        self._make_booking(date(2020, 2, 1), enquiry_source='Owner Suite', is_owner=True)

        rows = stays_trend_rows(include_owner=False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['arrivals'], 0)

    def test_include_owner_true_counts_the_owner_stay(self):
        self._make_booking(date(2020, 2, 1), enquiry_source='Owner Suite', is_owner=True)

        rows = stays_trend_rows(include_owner=True)
        self.assertEqual(rows[0]['arrivals'], 1)


class MonthlyBookingsRowsTests(TestCase):
    """staff/monthly_reports.py::monthly_bookings_rows() - Bookings vs Enquiries, mirroring the
    reference workbook's Bookings sheet. Displayed as the "Enquiries" tab (see
    StaffReportsEnquiriesView's own docstring for why it isn't called "Bookings" in the nav)."""

    def setUp(self):
        self.owner = make_owner(is_paid_regularly=False)
        self.property = Property.objects.create(
            title='Enquiries Property', short_title='ENQPROP', owner=self.owner,
        )
        self.guest = Guest.objects.create(first_name='Enq', last_name='Uiry', email='enquiries-test@example.com')

    def _make_booking(self, arrival, enquiry_source='Website', enquiry_status='Booking confirmed', is_owner=False):
        return Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=arrival,
            departure_date=arrival + timedelta(days=4), is_owner=is_owner,
            enquiry_status=enquiry_status, enquiry_source=enquiry_source,
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_confirmed_booking_counts_as_both_a_booking_and_an_enquiry(self):
        self._make_booking(date(2024, 3, 5), enquiry_status='Booking confirmed')

        rows = monthly_bookings_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Direct']['bookings'], 1)
        self.assertEqual(march['groups']['Direct']['enquiries'], 1)

    def test_unconverted_enquiry_counts_only_as_an_enquiry(self):
        """A cancelled/expired/failed direct reservation attempt still created a real Booking
        row - it must count toward Enquiries but never toward Bookings."""
        self._make_booking(date(2024, 3, 10), enquiry_status='Hold expired')

        rows = monthly_bookings_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Direct']['bookings'], 0)
        self.assertEqual(march['groups']['Direct']['enquiries'], 1)

    def test_platform_booking_cancelled_on_the_platform_counts_only_as_an_enquiry(self):
        """Per Thomas 2026-08-30: a platform reservation that was later cancelled on the platform
        itself (sync_ical_link() sets 'Cancelled by platform' when a previously-imported UID
        disappears from that platform's feed) still has its row here, same as a direct
        reservation attempt that never converted - it must count toward Airbnb's Enquiries but
        never its Bookings, not be silently invisible."""
        self._make_booking(date(2024, 3, 12), enquiry_source='Airbnb', enquiry_status='Cancelled by platform')

        rows = monthly_bookings_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Airbnb']['bookings'], 0)
        self.assertEqual(march['groups']['Airbnb']['enquiries'], 1)

    def test_owner_stay_excluded_entirely(self):
        self._make_booking(date(2024, 3, 15), enquiry_source='Owner Suite', is_owner=True)

        rows = monthly_bookings_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Total']['enquiries'], 0)

    def test_year_over_year_delta_is_a_real_change_not_the_prior_years_raw_figure(self):
        self._make_booking(date(2023, 3, 5))
        self._make_booking(date(2024, 3, 5))
        self._make_booking(date(2024, 3, 12))

        rows = monthly_bookings_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Direct']['bookings'], 2)
        self.assertEqual(march['groups']['Direct']['bookings_delta'], 1)

    def test_a_month_with_no_bookings_has_zero_figures_and_raises_nothing(self):
        rows = monthly_bookings_rows(2024)
        july = next(r for r in rows if r['month'].month == 7)
        self.assertEqual(july['groups']['Total']['bookings'], 0)
        self.assertEqual(july['groups']['Total']['bookings_pct'], Decimal('0'))


class BookingsTrendRowsTests(TestCase):
    def setUp(self):
        self.owner = make_owner(is_paid_regularly=False)
        self.property = Property.objects.create(
            title='Enquiries Trend Property', short_title='ENQTREND', owner=self.owner,
        )
        self.guest = Guest.objects.create(first_name='Trend', last_name='Enq', email='trend-enq@example.com')

    def test_empty_when_no_guest_bookings_exist(self):
        self.assertEqual(bookings_trend_rows(), [])

    def test_spans_from_earliest_to_latest_regardless_of_status(self):
        """Unlike revenue_trend_rows' bounds, this must include a never-confirmed enquiry's
        month too - Enquiries is the whole funnel, not just what converted."""
        Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=date(2020, 1, 10),
            departure_date=date(2020, 1, 14), is_owner=False,
            enquiry_status='Hold expired', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        rows = bookings_trend_rows()
        self.assertEqual(rows[0]['month'], date(2020, 1, 1))
        self.assertEqual(rows[0]['enquiries'], 1)
        self.assertEqual(rows[0]['bookings'], 0)


class StaffReportsEnquiriesViewTests(TestCase):
    def setUp(self):
        role = StaffRole.objects.create(name='Enquiries Viewer', can_view_reports=True)
        self.staffer = User.objects.create_user(username='enqstaffer', password='pw', is_staff=True)
        StaffProfile.objects.create(user=self.staffer, role=role)
        self.no_access_staffer = User.objects.create_user(username='noaccessenq', password='pw', is_staff=True)

        owner = make_owner(is_paid_regularly=False)
        property = Property.objects.create(title='Enquiries View Property', short_title='ENQVIEW', owner=owner)
        guest = Guest.objects.create(first_name='View', last_name='Enq', email='enq-view@example.com')
        Booking.objects.create(
            property=property, guest=guest, arrival_date=date(2024, 6, 10), departure_date=date(2024, 6, 14),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_requires_permission(self):
        self.client.login(username='noaccessenq', password='pw')
        response = self.client.get(reverse('staff:reports_enquiries'))
        self.assertEqual(response.status_code, 403)

    def test_page_renders_with_the_requested_year(self):
        self.client.login(username='enqstaffer', password='pw')
        response = self.client.get(reverse('staff:reports_enquiries'), {'year': '2024'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['year'], 2024)
        self.assertContains(response, 'June')
        self.assertContains(response, 'Bookings')
        self.assertContains(response, 'Enquiries')

    def test_groups_param_narrows_the_visible_columns(self):
        self.client.login(username='enqstaffer', password='pw')
        response = self.client.get(reverse('staff:reports_enquiries'), {'year': '2024', 'groups': 'Direct'})
        self.assertEqual(response.context['selected_groups'], {'Direct'})
        self.assertNotContains(response, '<th colspan="6">Airbnb</th>')
        self.assertContains(response, '<th colspan="6">Direct</th>')

    def test_trend_chart_renders_on_the_page(self):
        self.client.login(username='enqstaffer', password='pw')
        response = self.client.get(reverse('staff:reports_enquiries'), {'year': '2024'})
        self.assertContains(response, 'id="enquiries-trend-chart"')
        self.assertContains(response, 'id="enquiries-trend-data"')


class MonthlyExtrasRowsTests(TestCase):
    """staff/monthly_reports.py::monthly_extras_rows() - Tot + Lst Yr only, no % column and no
    Direct/Airbnb/etc breakdown, mirroring the reference workbook's Extras sheet exactly."""

    def setUp(self):
        self.owner = make_owner(is_paid_regularly=False)
        self.property = Property.objects.create(
            title='Extras Report Property', short_title='EXTRPROP', owner=self.owner,
        )
        self.guest = Guest.objects.create(first_name='Ext', last_name='Ras', email='extras-report@example.com')

    def _make_booking(self, arrival, enquiry_status='Booking confirmed', is_owner=False):
        return Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=arrival,
            departure_date=arrival + timedelta(days=4), is_owner=is_owner,
            enquiry_status=enquiry_status, enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_counts_each_requested_extra_once(self):
        booking = self._make_booking(date(2024, 3, 5))
        Extra.objects.create(booking=booking, welcome_pack=True, cot=True, high_chair=True, late_checkout=True)
        AirportTransfer.objects.create(
            booking=booking, direction=AirportTransferDirection.INBOUND, time=time(14, 0),
        )

        rows = monthly_extras_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['metrics']['airport_transfers']['total'], 1)
        self.assertEqual(march['metrics']['welcome_packs']['total'], 1)
        self.assertEqual(march['metrics']['cots']['total'], 1)
        self.assertEqual(march['metrics']['high_chairs']['total'], 1)
        self.assertEqual(march['metrics']['late_checkouts']['total'], 1)
        self.assertEqual(march['metrics']['mid_stay_cleans']['total'], 0)

    def test_owner_stay_extras_are_included_unlike_revenue_and_stays(self):
        """Extras aren't platform-specific, and the reference workbook's own Extras sheet has no
        Direct/Airbnb/etc split to exclude owner stays from - unlike Revenue/Stays/Bookings, an
        owner stay's extras must count here."""
        booking = self._make_booking(date(2024, 3, 10), is_owner=True)
        Extra.objects.create(booking=booking, cot=True)

        rows = monthly_extras_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['metrics']['cots']['total'], 1)

    def test_extra_on_an_unconfirmed_booking_is_not_counted(self):
        booking = self._make_booking(date(2024, 3, 15), enquiry_status='Hold expired')
        Extra.objects.create(booking=booking, welcome_pack=True)

        rows = monthly_extras_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['metrics']['welcome_packs']['total'], 0)

    def test_year_over_year_delta_is_a_real_change(self):
        old = self._make_booking(date(2023, 3, 5))
        Extra.objects.create(booking=old, cot=True)
        new = self._make_booking(date(2024, 3, 5))
        Extra.objects.create(booking=new, cot=True)

        rows = monthly_extras_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['metrics']['cots']['total'], 1)
        self.assertEqual(march['metrics']['cots']['delta'], 0)

    def test_a_month_with_no_bookings_has_zero_figures_and_raises_nothing(self):
        rows = monthly_extras_rows(2024)
        july = next(r for r in rows if r['month'].month == 7)
        self.assertEqual(july['metrics']['welcome_packs']['total'], 0)
        self.assertEqual(july['metrics']['welcome_packs']['delta'], 0)


class ExtrasTrendRowsTests(TestCase):
    def setUp(self):
        self.owner = make_owner(is_paid_regularly=False)
        self.property = Property.objects.create(
            title='Extras Trend Property', short_title='EXTRTREND', owner=self.owner,
        )
        self.guest = Guest.objects.create(first_name='Trend', last_name='Ext', email='trend-ext@example.com')

    def test_empty_when_no_confirmed_bookings_exist(self):
        self.assertEqual(extras_trend_rows(), [])

    def test_spans_from_earliest_to_latest_confirmed_booking(self):
        booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=date(2020, 1, 10),
            departure_date=date(2020, 1, 14), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Extra.objects.create(booking=booking, welcome_pack=True)

        rows = extras_trend_rows()
        self.assertEqual(rows[0]['month'], date(2020, 1, 1))
        self.assertEqual(rows[0]['welcome_packs'], 1)


class StaffReportsExtrasViewTests(TestCase):
    def setUp(self):
        role = StaffRole.objects.create(name='Extras Viewer', can_view_reports=True)
        self.staffer = User.objects.create_user(username='extrasstaffer', password='pw', is_staff=True)
        StaffProfile.objects.create(user=self.staffer, role=role)
        self.no_access_staffer = User.objects.create_user(username='noaccessextras', password='pw', is_staff=True)

    def test_requires_permission(self):
        self.client.login(username='noaccessextras', password='pw')
        response = self.client.get(reverse('staff:reports_extras'))
        self.assertEqual(response.status_code, 403)

    def test_page_renders_with_the_requested_year(self):
        self.client.login(username='extrasstaffer', password='pw')
        response = self.client.get(reverse('staff:reports_extras'), {'year': '2024'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['year'], 2024)
        self.assertContains(response, 'Airport Transfers')
        self.assertContains(response, 'Welcome Packs')
        self.assertContains(response, 'Mid-stay Cleans')

    def test_trend_chart_renders_on_the_page(self):
        self.client.login(username='extrasstaffer', password='pw')
        response = self.client.get(reverse('staff:reports_extras'), {'year': '2024'})
        self.assertContains(response, 'id="extras-trend-chart"')
        self.assertContains(response, 'id="extras-trend-data"')


class MonthlyCommissionsRowsTests(TestCase):
    """staff/monthly_reports.py::monthly_commissions_rows() - KLT's own Pre-IVA/Post-IVA
    commission only, mirroring the reference workbook's Commissions sheet minus its "Maria"
    column (dropped per Thomas 2026-08-30 - a legacy item documented elsewhere)."""

    def setUp(self):
        self.owner = make_owner(is_paid_regularly=False)
        self.property = Property.objects.create(
            title='Commissions Report Property', short_title='COMMPROP', owner=self.owner,
        )
        self.guest = Guest.objects.create(first_name='Comm', last_name='Ission', email='commissions-test@example.com')

    def _make_booking(self, arrival, enquiry_source='Website', is_owner=False):
        booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=arrival,
            departure_date=arrival + timedelta(days=4), is_owner=is_owner,
            enquiry_status='Booking confirmed', enquiry_source=enquiry_source,
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        if not is_owner:
            Charge.objects.create(booking=booking, basic_rental=Decimal('1000.00'))
        return booking

    def test_direct_booking_produces_a_real_commission(self):
        self._make_booking(date(2024, 3, 5))

        rows = monthly_commissions_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertGreater(march['groups']['Direct']['pre_iva'], Decimal('0'))
        self.assertEqual(march['groups']['Total']['pre_iva'], march['groups']['Direct']['pre_iva'])

    def test_post_iva_never_exceeds_pre_iva(self):
        self._make_booking(date(2024, 3, 5))

        rows = monthly_commissions_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertLessEqual(march['groups']['Direct']['post_iva'], march['groups']['Direct']['pre_iva'])

    def test_owner_stay_excluded_entirely(self):
        self._make_booking(date(2024, 3, 15), is_owner=True)

        rows = monthly_commissions_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Total']['pre_iva'], Decimal('0'))

    def test_year_over_year_delta_is_a_real_change(self):
        self._make_booking(date(2023, 3, 5))
        self._make_booking(date(2024, 3, 5))

        rows = monthly_commissions_rows(2024)
        march = next(r for r in rows if r['month'].month == 3)
        # Same rental amount both years -> the commission itself matches -> delta is zero, not
        # None or missing.
        self.assertEqual(march['groups']['Direct']['pre_iva_delta'], Decimal('0'))

    def test_a_month_with_no_bookings_has_zero_figures_and_raises_nothing(self):
        rows = monthly_commissions_rows(2024)
        july = next(r for r in rows if r['month'].month == 7)
        self.assertEqual(july['groups']['Total']['pre_iva'], Decimal('0'))
        self.assertEqual(july['groups']['Total']['pre_iva_pct'], Decimal('0'))


class CommissionsTrendRowsTests(TestCase):
    def setUp(self):
        self.owner = make_owner(is_paid_regularly=False)
        self.property = Property.objects.create(
            title='Commissions Trend Property', short_title='COMMTREND', owner=self.owner,
        )
        self.guest = Guest.objects.create(first_name='Trend', last_name='Comm', email='trend-comm@example.com')

    def test_empty_when_no_guest_bookings_exist(self):
        self.assertEqual(commissions_trend_rows(), [])

    def test_spans_from_earliest_to_latest_and_computes_a_real_commission(self):
        booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=date(2020, 1, 10),
            departure_date=date(2020, 1, 14), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=booking, basic_rental=Decimal('1000.00'))

        rows = commissions_trend_rows()
        self.assertEqual(rows[0]['month'], date(2020, 1, 1))
        self.assertGreater(rows[0]['pre_iva'], Decimal('0'))


class StaffReportsCommissionsViewTests(TestCase):
    def setUp(self):
        role = StaffRole.objects.create(name='Commissions Viewer', can_view_reports=True)
        self.staffer = User.objects.create_user(username='commissionsstaffer', password='pw', is_staff=True)
        StaffProfile.objects.create(user=self.staffer, role=role)
        self.no_access_staffer = User.objects.create_user(username='noaccesscomm', password='pw', is_staff=True)

    def test_requires_permission(self):
        self.client.login(username='noaccesscomm', password='pw')
        response = self.client.get(reverse('staff:reports_commissions'))
        self.assertEqual(response.status_code, 403)

    def test_page_renders_with_the_requested_year(self):
        self.client.login(username='commissionsstaffer', password='pw')
        response = self.client.get(reverse('staff:reports_commissions'), {'year': '2024'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['year'], 2024)
        self.assertContains(response, 'Pre-IVA')
        self.assertContains(response, 'Post-IVA')
        self.assertNotContains(response, 'Maria')

    def test_trend_chart_renders_on_the_page(self):
        self.client.login(username='commissionsstaffer', password='pw')
        response = self.client.get(reverse('staff:reports_commissions'), {'year': '2024'})
        self.assertContains(response, 'id="commissions-trend-chart"')
        self.assertContains(response, 'id="commissions-trend-data"')


class LocationGroupsTests(TestCase):
    """staff/monthly_reports.py::location_groups() - the Management tab's own dynamic group
    list, built fresh from properties.models.Location rather than a fixed tuple."""

    def test_includes_every_location_plus_a_trailing_unassigned(self):
        make_location(title='Alpha Location')
        make_location(title='Beta Location')

        groups = location_groups()
        labels = [label for _key, label in groups]
        self.assertIn('Alpha Location', labels)
        self.assertIn('Beta Location', labels)
        self.assertEqual(labels[-1], 'Unassigned')

    def test_ordered_alphabetically_by_title(self):
        make_location(title='Zeta Location')
        make_location(title='Alpha Location')

        groups = location_groups()
        labels = [label for _key, label in groups if label != 'Unassigned']
        self.assertEqual(labels, sorted(labels))


class MonthlyManagementRowsTests(TestCase):
    """staff/monthly_reports.py::monthly_management_rows() - Cleans/Meet & Greets grouped by
    property Location (not property, per Thomas 2026-08-30), mirroring the reference workbook's
    Management sheet."""

    def setUp(self):
        self.owner = make_owner(is_paid_regularly=False)
        self.location = make_location(title='Management Report Location')
        self.property = Property.objects.create(
            title='Management Report Property', short_title='MGMTPROP', owner=self.owner, location=self.location,
        )
        self.unassigned_property = Property.objects.create(
            title='Unassigned Management Property', short_title='MGMTNOLOC', owner=self.owner,
        )
        self.guest = Guest.objects.create(first_name='Mgmt', last_name='Report', email='management-report@example.com')

    def _make_booking(self, arrival, property=None, clean=True, meet_greet=True, is_owner=False):
        booking = Booking.objects.create(
            property=property or self.property, guest=self.guest, arrival_date=arrival,
            departure_date=arrival + timedelta(days=4), is_owner=is_owner,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Departure.objects.create(booking=booking, clean=clean)
        Arrival.objects.create(booking=booking, meet_greet=meet_greet)
        return booking

    def test_counts_a_clean_and_meet_greet_under_its_own_location(self):
        self._make_booking(date(2024, 3, 5))

        groups = location_groups()
        rows = monthly_management_rows(2024, groups)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups'][str(self.location.pk)]['cleans'], 1)
        self.assertEqual(march['groups'][str(self.location.pk)]['meet_greets'], 1)
        self.assertEqual(march['groups']['Total']['cleans'], 1)

    def test_property_with_no_location_counts_under_unassigned(self):
        self._make_booking(date(2024, 3, 10), property=self.unassigned_property)

        groups = location_groups()
        rows = monthly_management_rows(2024, groups)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['unassigned']['cleans'], 1)

    def test_owner_stay_is_included_unlike_revenue_and_stays(self):
        """A pure operational headcount, not a financial figure, so owner stays count here -
        unlike Revenue/Stays/Bookings, which exclude them entirely."""
        self._make_booking(date(2024, 3, 15), is_owner=True)

        groups = location_groups()
        rows = monthly_management_rows(2024, groups)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Total']['cleans'], 1)

    def test_clean_false_is_not_counted(self):
        self._make_booking(date(2024, 3, 20), clean=False, meet_greet=False)

        groups = location_groups()
        rows = monthly_management_rows(2024, groups)
        march = next(r for r in rows if r['month'].month == 3)
        self.assertEqual(march['groups']['Total']['cleans'], 0)
        self.assertEqual(march['groups']['Total']['meet_greets'], 0)

    def test_a_month_with_no_bookings_has_zero_figures_and_raises_nothing(self):
        groups = location_groups()
        rows = monthly_management_rows(2024, groups)
        july = next(r for r in rows if r['month'].month == 7)
        self.assertEqual(july['groups']['Total']['cleans'], 0)
        self.assertEqual(july['groups']['Total']['cleans_pct'], Decimal('0'))


class ManagementTrendRowsTests(TestCase):
    def setUp(self):
        self.owner = make_owner(is_paid_regularly=False)
        self.property = Property.objects.create(
            title='Management Trend Property', short_title='MGMTTREND', owner=self.owner,
        )
        self.guest = Guest.objects.create(first_name='Trend', last_name='Mgmt', email='trend-mgmt@example.com')

    def test_empty_when_no_confirmed_bookings_exist(self):
        groups = location_groups()
        self.assertEqual(management_trend_rows(groups), [])

    def test_spans_from_earliest_to_latest_confirmed_booking(self):
        booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=date(2020, 1, 10),
            departure_date=date(2020, 1, 14), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Departure.objects.create(booking=booking, clean=True)

        groups = location_groups()
        rows = management_trend_rows(groups)
        self.assertEqual(rows[0]['month'], date(2020, 1, 1))
        self.assertEqual(rows[0]['cleans'], 1)


class StaffReportsManagementViewTests(TestCase):
    def setUp(self):
        role = StaffRole.objects.create(name='Management Viewer', can_view_reports=True)
        self.staffer = User.objects.create_user(username='managementstaffer', password='pw', is_staff=True)
        StaffProfile.objects.create(user=self.staffer, role=role)
        self.no_access_staffer = User.objects.create_user(username='noaccessmgmt', password='pw', is_staff=True)

    def test_requires_permission(self):
        self.client.login(username='noaccessmgmt', password='pw')
        response = self.client.get(reverse('staff:reports_management'))
        self.assertEqual(response.status_code, 403)

    def test_page_renders_with_the_requested_year(self):
        self.client.login(username='managementstaffer', password='pw')
        response = self.client.get(reverse('staff:reports_management'), {'year': '2024'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['year'], 2024)
        self.assertContains(response, 'Unassigned')
        self.assertContains(response, 'Cleans')

    def test_trend_chart_renders_on_the_page(self):
        self.client.login(username='managementstaffer', password='pw')
        response = self.client.get(reverse('staff:reports_management'), {'year': '2024'})
        self.assertContains(response, 'id="management-trend-chart"')
        self.assertContains(response, 'id="management-trend-data"')
