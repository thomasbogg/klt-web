from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bookings.models import Arrival, Booking, Charge, Departure, PaymentSettings
from finance.models import OwnerInvoice
from guests.models import Guest
from properties.models import Accountant, ManagementCompany, Owner, Property, PropertySpec

User = get_user_model()


class AccountantSuiteTests(TestCase):
    """The Accountants Suite login gate, Home and Reports - mirrors owners/tests.py::
    OwnerSuiteTests, scoped by Property.accountant instead of Property.owner. An accountant can
    look after properties belonging to several different owners (unlike the Owner Suite, which is
    always scoped to exactly one), so this also checks that a report spans more than one owner."""

    def setUp(self):
        self.accountant = Accountant.objects.create(
            company='Portal Accounting Co', name='Portal Accountant', email='portal-accountant@example.com', phone='+351900000001',
        )
        self.accountant_user = User.objects.create_user(username='portalaccountant', password='pw')
        self.accountant.user = self.accountant_user
        self.accountant.save(update_fields=['user'])

        self.other_accountant = Accountant.objects.create(
            company='Other Accounting Co', name='Other Accountant', email='other-accountant@example.com', phone='+351900000002',
        )

        self.non_accountant_user = User.objects.create_user(username='randomaccuser', password='pw')

        self.owner = Owner.objects.create(
            name='Accountant Client Owner', email='accountant-client-owner@example.com', currency=Owner.Currency.EUR,
            is_paid_regularly=False, cleans_are_invoiced=False,
        )
        # cleans_are_invoiced=True (unlike self.owner above) - lets tests exercise the per-row
        # management-fee gate across two owners under the same accountant in one report.
        self.other_owner = Owner.objects.create(
            name='Accountant Second Owner', email='accountant-second-owner@example.com', currency=Owner.Currency.EUR,
            is_paid_regularly=False, cleans_are_invoiced=True,
        )
        self.company = ManagementCompany.objects.create(name='Accountant Suite Co', finances_managed_internally=True)
        self.property = Property.objects.create(
            title='Accountant Suite Property', short_title='ACCSUITE1', owner=self.owner, accountant=self.accountant,
            cleaning_company=self.company, booking_company=self.company, standard_cleaning_fee=Decimal('80.00'),
        )
        PropertySpec.objects.create(property=self.property, bedrooms=2)
        # A second owner's property assigned to the SAME accountant - confirms the report spans
        # multiple owners, not just one, unlike the Owner Suite's own single-owner scope.
        self.second_property = Property.objects.create(
            title='Accountant Suite Second Property', short_title='ACCSUITE2', owner=self.other_owner, accountant=self.accountant,
        )
        PropertySpec.objects.create(property=self.second_property, bedrooms=2)
        # A property assigned to a DIFFERENT accountant - must never appear for self.accountant.
        self.unrelated_property = Property.objects.create(
            title='Unrelated Accountant Property', short_title='ACCUNREL', owner=self.owner, accountant=self.other_accountant,
        )
        PropertySpec.objects.create(property=self.unrelated_property, bedrooms=2)

        self.guest = Guest.objects.create(first_name='Acc', last_name='Guest', email='accountant-suite-guest@example.com')
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

        self.second_owner_booking = Booking.objects.create(
            property=self.second_property, guest=self.guest, arrival_date=self.today + timedelta(days=4),
            departure_date=self.today + timedelta(days=8), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=self.second_owner_booking, basic_rental=Decimal('200.00'))
        Departure.objects.create(booking=self.second_owner_booking, clean=True)
        Arrival.objects.create(booking=self.second_owner_booking, meet_greet=True)

        self.unrelated_booking = Booking.objects.create(
            property=self.unrelated_property, guest=self.guest, arrival_date=self.today + timedelta(days=3),
            departure_date=self.today + timedelta(days=7), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )

    def test_home_redirects_anonymous_visitor_to_login(self):
        response = self.client.get(reverse('accountants:home'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('accountants:login'), response.url)

    def test_a_user_with_no_accountant_profile_is_rejected_at_login(self):
        response = self.client.post(reverse('accountants:login'), {'username': 'randomaccuser', 'password': 'pw'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        home_response = self.client.get(reverse('accountants:home'))
        self.assertEqual(home_response.status_code, 302)

    def test_a_linked_accountant_can_log_in_and_reach_home(self):
        response = self.client.post(reverse('accountants:login'), {'username': 'portalaccountant', 'password': 'pw'}, follow=True)
        self.assertRedirects(response, reverse('accountants:home'))
        self.assertContains(response, 'Accountant Suite Property')

    def test_logout_via_post_logs_out_and_redirects_to_login(self):
        self.client.login(username='portalaccountant', password='pw')
        self.assertEqual(self.client.get(reverse('accountants:home')).status_code, 200)
        response = self.client.post(reverse('accountants:logout'))
        self.assertRedirects(response, reverse('accountants:login'))
        self.assertEqual(self.client.get(reverse('accountants:home')).status_code, 302)

    def test_logout_via_get_is_not_allowed(self):
        self.client.login(username='portalaccountant', password='pw')
        response = self.client.get(reverse('accountants:logout'))
        self.assertEqual(response.status_code, 405)

    def test_home_lists_properties_across_more_than_one_owner_but_not_another_accountants(self):
        self.client.login(username='portalaccountant', password='pw')
        response = self.client.get(reverse('accountants:home'))
        properties = list(response.context['properties'])
        self.assertIn(self.property, properties)
        self.assertIn(self.second_property, properties)
        self.assertNotIn(self.unrelated_property, properties)

    def test_report_spans_every_owner_this_accountant_looks_after(self):
        self.client.login(username='portalaccountant', password='pw')
        response = self.client.get(reverse('accountants:reports'), {
            'start': self.today.isoformat(), 'end': (self.today + timedelta(days=14)).isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        bookings_seen = {row['booking'] for row in response.context['rows']}
        self.assertEqual(bookings_seen, {self.booking, self.second_owner_booking})

    def test_report_never_shows_another_accountants_bookings(self):
        self.client.login(username='portalaccountant', password='pw')
        response = self.client.get(reverse('accountants:reports'), {
            'start': self.today.isoformat(), 'end': (self.today + timedelta(days=14)).isoformat(),
        })
        bookings_seen = {row['booking'] for row in response.context['rows']}
        self.assertNotIn(self.unrelated_booking, bookings_seen)

    def test_report_shows_commission_but_never_klt_internal_columns(self):
        """Unlike OWNER_SAFE_REPORT_COLUMNS (which owners get), ACCOUNTANT_REPORT_COLUMNS keeps
        Commission visible - 2026-09-15, per Thomas, accountants need it for their own invoice
        totals - but klt_net_commission/klt_net_revenue (KLT's own internal post-VAT take) stay
        hidden, same as for owners. See owners/tests.py::OwnerSuiteTests.
        test_report_never_shows_klt_internal_commission_columns for the owner-side contrast."""
        self.client.login(username='portalaccountant', password='pw')
        response = self.client.get(reverse('accountants:reports'), {
            'start': self.today.isoformat(), 'end': (self.today + timedelta(days=14)).isoformat(),
        })
        self.assertIn('commission', response.context['selected_columns'])
        self.assertContains(response, 'Commission')
        self.assertNotIn('klt_net_commission', response.context['selected_columns'])
        self.assertNotIn('klt_net_revenue', response.context['selected_columns'])
        self.assertNotContains(response, 'KLT Net Commission')
        self.assertNotContains(response, 'KLT Net Revenue')

    def test_total_to_be_receipted_sums_basic_rental_platform_fee_and_vat(self):
        """basic_rental + platform_fee + platform_fee_vat - self.booking is a direct (non-platform)
        Website booking, so platform_fee/platform_fee_vat are both zero and the figure is just its
        €300.00 basic_rental."""
        self.client.login(username='portalaccountant', password='pw')
        response = self.client.get(reverse('accountants:reports'), {
            'start': self.today.isoformat(), 'end': (self.today + timedelta(days=14)).isoformat(),
        })
        rows_by_booking = {row['booking']: row for row in response.context['rows']}
        self.assertEqual(rows_by_booking[self.booking]['total_to_be_receipted'], Decimal('300.00'))

    def test_management_fee_costs_gated_per_row_by_owner_cleans_are_invoiced(self):
        """self.owner has cleans_are_invoiced=False, self.other_owner has it True - both under the
        same accountant in one report, so this checks the gate is per-row, not per-request."""
        self.client.login(username='portalaccountant', password='pw')
        response = self.client.get(reverse('accountants:reports'), {
            'start': self.today.isoformat(), 'end': (self.today + timedelta(days=14)).isoformat(),
        })
        rows_by_booking = {row['booking']: row for row in response.context['rows']}
        not_invoiced_row = rows_by_booking[self.booking]
        self.assertIsNone(not_invoiced_row['clean_cost'])
        self.assertIsNone(not_invoiced_row['meet_greet_cost'])
        self.assertIsNone(not_invoiced_row['maintenance_cost'])

        invoiced_row = rows_by_booking[self.second_owner_booking]
        self.assertIsNotNone(invoiced_row['clean_cost'])
        self.assertIsNotNone(invoiced_row['meet_greet_cost'])

    def test_invoice_total_is_commission_plus_platform_fee_plus_applicable_management_fees(self):
        """2026-09-15, per Thomas: replaces Owner Net Revenue. 'If applicable' means the
        management-fee gate above still applies - self.booking's owner isn't invoiced for those,
        so its invoice_total is commission alone (platform_fee is zero, a direct Website booking);
        self.second_owner_booking's owner is invoiced, so clean/meet-greet count too."""
        self.client.login(username='portalaccountant', password='pw')
        response = self.client.get(reverse('accountants:reports'), {
            'start': self.today.isoformat(), 'end': (self.today + timedelta(days=14)).isoformat(),
        })
        rows_by_booking = {row['booking']: row for row in response.context['rows']}

        not_invoiced_row = rows_by_booking[self.booking]
        self.assertEqual(not_invoiced_row['invoice_total'], not_invoiced_row['commission'])

        invoiced_row = rows_by_booking[self.second_owner_booking]
        expected = (
            invoiced_row['commission'] + invoiced_row['platform_fee']
            + invoiced_row['clean_cost'] + invoiced_row['meet_greet_cost'] + invoiced_row['maintenance_cost']
        )
        self.assertEqual(invoiced_row['invoice_total'], expected)

    def test_invoice_number_shown_when_a_linked_owner_invoice_exists(self):
        invoice = OwnerInvoice.objects.create(
            owner=self.owner, kind=OwnerInvoice.Kind.COMMISSION_PAYOUT,
            commission_amount=Decimal('30.00'), sage_invoice_id='SAGE-123',
        )
        invoice.bookings.add(self.booking)

        self.client.login(username='portalaccountant', password='pw')
        response = self.client.get(reverse('accountants:reports'), {
            'start': self.today.isoformat(), 'end': (self.today + timedelta(days=14)).isoformat(),
        })
        rows_by_booking = {row['booking']: row for row in response.context['rows']}
        self.assertEqual(rows_by_booking[self.booking]['invoice'], invoice)
        self.assertIsNone(rows_by_booking[self.second_owner_booking]['invoice'])
        self.assertContains(response, 'SAGE-123')


class AccountantAcceptInviteViewTests(TestCase):
    """accountants:accept_invite - mirrors owners/tests.py::OwnerAcceptInviteViewTests exactly
    (same PasswordResetConfirmView base, same token mechanics)."""

    def setUp(self):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        self.user = User.objects.create_user(username='invited-accountant@example.com', email='invited-accountant@example.com')
        self.user.set_unusable_password()
        self.user.save()
        self.accountant = Accountant.objects.create(
            company='Invitee Co', name='Invitee Person', email='invited-accountant@example.com', phone='+351900000003', user=self.user,
        )
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        self.url = reverse('accountants:accept_invite', kwargs={'uidb64': uid, 'token': token})

    def test_valid_link_shows_the_set_password_form(self):
        response = self.client.get(self.url, follow=True)
        self.assertContains(response, 'Set Your Password')

    def test_setting_a_password_logs_the_accountant_in_and_redirects_home(self):
        get_response = self.client.get(self.url, follow=True)
        response = self.client.post(get_response.request['PATH_INFO'], {
            'new_password1': 'a-genuinely-strong-pw-98x',
            'new_password2': 'a-genuinely-strong-pw-98x',
        }, follow=True)
        self.user.refresh_from_db()
        self.assertTrue(self.user.has_usable_password())
        self.assertIn('_auth_user_id', self.client.session)
        self.assertRedirects(response, reverse('accountants:home'))
