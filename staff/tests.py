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
    Arrival, BalancePayment, Booking, BookingCondition, BookingSettings, Charge, Departure, Extra,
    FAQ, Payment, PaymentSettings,
)
from guests.models import Guest
from properties.models import (
    Accountant, Amenity, Location, LocationImage, LocationRules, LocationSpec, ManagementCompany,
    Owner, Price, Property, PropertyImage, PropertyOwnership, PropertySpec, SEFDetail,
    iCalLink,
)
from staff.models import CleaningTask, Deduction, OwnerPayment, StaffProfile, StaffRole, TaskHistoryEntry
from staff.utils import apply_manual_task_date, booking_stage, cleaning_task_valid_range, next_step_hint, status_bucket

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
        self.payment.status = 'pending'
        self.payment.save(update_fields=['status'])
        self.client.post(self.url, {'action': 'update_booking', 'basic_rental': '750.00'})
        response = self.client.post(self.url, {'action': 'recalculate_payment_split'})
        self.assertRedirects(response, self.url)
        self.charge.refresh_from_db()
        expected = BookingSettings.load().compute_costs(Decimal('788.50'), arrival_date=self.start)
        self.assertEqual(self.charge.due_at_booking, expected['due_at_booking'])
        self.assertEqual(self.charge.due_at_balance, expected['due_at_balance'])

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
            'action': 'update_booking',
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
        Arrival.objects.create(booking=self.booking, self_check_in=True, meet_greet=True)
        Departure.objects.create(booking=self.booking, clean=True)
        response = self.client.post(self.url, {'action': 'update_booking'})
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

    def test_meet_greet_and_clean_row_present_in_markup_regardless_of_owner_status(self):
        # Visibility is a client-side JS toggle (see arrival_departure.js) - the row is always
        # server-rendered so it works with JS disabled and so the checked state survives a
        # validation-error re-render; this only confirms the server side of that contract.
        response = self.client.get(self.url)
        self.assertContains(response, 'data-owner-row')
        self.assertContains(response, 'Meet &amp; greet')
        self.assertContains(response, 'Clean on departure')

    def test_mid_stay_clean_date_outside_stay_is_rejected(self):
        response = self.client.post(self.url, {
            'action': 'update_booking',
            'mid_stay_clean': 'on', 'mid_stay_clean_date': (self.end + timedelta(days=1)).isoformat(),
        })
        self.assertRedirects(response, self.url)
        self.assertFalse(hasattr(self.booking, 'extras'))

    def test_mid_stay_clean_saved_within_stay(self):
        target = self.start + timedelta(days=2)
        response = self.client.post(self.url, {
            'action': 'update_booking', 'mid_stay_clean': 'on', 'mid_stay_clean_date': target.isoformat(),
        })
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertTrue(self.booking.extras.mid_stay_clean)
        self.assertEqual(self.booking.extras.mid_stay_clean_date, target)


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

    def test_default_shows_only_this_guests_valid_bookings(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context['status_filter'], 'Valid')
        booking_ids = {row['booking'].pk for row in response.context['rows']}
        self.assertEqual(booking_ids, {self.confirmed_booking.pk})  # excludes cancelled + other guest

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
            'booking_company': self.management_company.pk, 'booking_com_id': 'BDC123',
            'standard_cleaning_fee': '90.00',
        })
        self.assertRedirects(response, f'{self.url}?panel=main')
        self.property.refresh_from_db()
        self.assertEqual(self.property.door_number, '12B')
        self.assertEqual(self.property.al_number, 9999)
        self.assertEqual(self.property.booking_company_id, self.management_company.pk)
        self.assertIsNone(self.property.cleaning_company_id)
        self.assertEqual(self.property.standard_cleaning_fee, Decimal('90.00'))

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
        self.client.post(self.url, {
            'action': 'add_ical_link', 'ical_source': 'airbnb', 'ical_url': 'https://airbnb.com/feed.ics',
        })
        link = iCalLink.objects.get(property=self.property)
        self.assertEqual(link.ical_source, 'airbnb')
        self.assertEqual(link.ical_url, 'https://airbnb.com/feed.ics')

    def test_add_ical_link_requires_url(self):
        self.client.post(self.url, {'action': 'add_ical_link', 'ical_source': 'airbnb', 'ical_url': ''})
        self.assertFalse(iCalLink.objects.filter(property=self.property).exists())

    def test_update_ical_link_saves_new_url(self):
        link = iCalLink.objects.create(
            property=self.property, ical_source='booking.com', ical_url='https://old.example.com/feed.ics',
        )
        response = self.client.post(self.url, {
            'action': 'update_ical_link', 'link_id': link.pk, 'ical_url': 'https://new.example.com/feed.ics',
        })
        self.assertRedirects(response, f'{self.url}?panel=ical')
        link.refresh_from_db()
        self.assertEqual(link.ical_url, 'https://new.example.com/feed.ics')
        self.assertEqual(link.ical_source, 'booking.com')  # untouched by this action

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
        self.link = iCalLink.objects.create(
            property=self.property, ical_source='airbnb', ical_url='https://example.com/feed.ics',
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

    def test_link_with_unrecognised_source_shows_an_error_without_fetching(self):
        self.link.ical_source = None
        self.link.save(update_fields=['ical_source'])
        with patch('staff.views.requests.get') as mocked_get:
            response = self.client.post(self.url)
        mocked_get.assert_not_called()
        self.assertIn('recognised source', response.context['fetch_error'])

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

    def test_mid_stay_range_is_the_booking_own_stay(self):
        extra = Extra.objects.create(
            booking=self.booking, mid_stay_clean=True, mid_stay_clean_date=self.start + timedelta(days=3),
        )
        mid_task = CleaningTask.objects.get(booking=self.booking, task_type='mid_stay')
        min_date, max_date = cleaning_task_valid_range(mid_task)
        self.assertEqual(min_date, self.start)
        self.assertEqual(max_date, self.end)


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

    def test_detail_view_omits_arrival_for_mid_stay(self):
        Extra.objects.create(
            booking=self.booking, mid_stay_clean=True, mid_stay_clean_date=self.start + timedelta(days=3),
        )
        mid_task = CleaningTask.objects.get(booking=self.booking, task_type='mid_stay')
        self.client.login(username='calendarsuperuser', password='pw')
        response = self.client.get(reverse('staff:cleaning_task_detail', kwargs={'pk': mid_task.pk}))
        data = response.json()
        self.assertEqual(data['arrival_html'], '')

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
