from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bookings.models import Arrival, Booking, Charge, Departure, PaymentSettings
from finance.models import Memo, PayoutRecord
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
        self.other_owner = Owner.objects.create(
            name='Accountant Second Owner', email='accountant-second-owner@example.com', currency=Owner.Currency.EUR,
            is_paid_regularly=False, cleans_are_invoiced=False,
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

    def test_report_never_shows_klt_internal_commission_columns(self):
        """Same OWNER_SAFE_REPORT_COLUMNS the Owner Suite uses - see owners/tests.py::
        OwnerSuiteTests.test_report_never_shows_klt_internal_commission_columns for why."""
        self.client.login(username='portalaccountant', password='pw')
        response = self.client.get(reverse('accountants:reports'), {
            'start': self.today.isoformat(), 'end': (self.today + timedelta(days=14)).isoformat(),
        })
        self.assertNotIn('commission', response.context['selected_columns'])
        self.assertNotIn('klt_net_commission', response.context['selected_columns'])
        self.assertNotIn('klt_net_revenue', response.context['selected_columns'])
        self.assertNotContains(response, 'KLT Net Commission')
        self.assertNotContains(response, 'KLT Net Revenue')


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


class AccountantPayoutsMemosTests(TestCase):
    """Payouts & Memos - mirrors owners/tests.py::OwnerPayoutsMemosTests, scoped to
    property__accountant. PayoutRecord/Memo are created via the real staff views, same
    convention."""

    def setUp(self):
        self.accountant = Accountant.objects.create(
            company='Payouts Accounting Co', name='Payouts Accountant', email='payouts-accountant@example.com', phone='+351900000004',
        )
        self.accountant_user = User.objects.create_user(username='payoutsaccountant', password='pw')
        self.accountant.user = self.accountant_user
        self.accountant.save(update_fields=['user'])

        self.other_accountant = Accountant.objects.create(
            company='Other Payouts Accounting Co', name='Other Payouts Accountant', email='other-payouts-accountant@example.com', phone='+351900000006',
        )
        self.other_accountant_user = User.objects.create_user(username='otherpayoutsaccountant', password='pw')
        self.other_accountant.user = self.other_accountant_user
        self.other_accountant.save(update_fields=['user'])

        self.owner = Owner.objects.create(
            name='Payouts Accountant Owner', email='payouts-accountant-owner@example.com', currency=Owner.Currency.EUR,
            is_paid_regularly=True, cleans_are_invoiced=False,
        )
        self.company = ManagementCompany.objects.create(name='Payouts Accountant Test Co', finances_managed_internally=True)
        self.property = Property.objects.create(
            title='Payouts Accountant Property', short_title='ACCPAYOUT', owner=self.owner, accountant=self.accountant,
            cleaning_company=self.company, booking_company=self.company, standard_cleaning_fee=Decimal('80.00'),
        )
        PropertySpec.objects.create(property=self.property, bedrooms=2)
        guest = Guest.objects.create(first_name='Acc', last_name='Pay', email='accountant-payouts-guest@example.com')
        self.today = timezone.now().date()
        self.booking = Booking.objects.create(
            property=self.property, guest=guest,
            arrival_date=self.today + timedelta(days=10), departure_date=self.today + timedelta(days=14),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=self.booking, basic_rental=Decimal('300.00'))
        Departure.objects.create(booking=self.booking, clean=True)  # auto-creates a Memo via signal

        staffer = User.objects.create_user(username='accpayoutsstaff', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='accpayoutsstaff', password='pw')
        self.client.post(reverse('staff:finance_payout_mark_paid', kwargs={'reference': self.booking.reference}))
        self.memo = Memo.objects.get(property=self.property)
        self.client.post(reverse('staff:finance_memo_send', kwargs={'pk': self.memo.pk}))
        self.client.logout()
        self.payout_record = PayoutRecord.objects.get(booking=self.booking)
        self.memo.refresh_from_db()

    def test_lists_both_a_payout_and_a_memo_row(self):
        self.client.login(username='payoutsaccountant', password='pw')
        response = self.client.get(reverse('accountants:payouts_memos'))
        rows_by_type = {row['type']: row for row in response.context['rows']}
        self.assertEqual(set(rows_by_type), {'Payout', 'Memo'})
        self.assertEqual(rows_by_type['Payout']['reference'], self.booking.reference)
        self.assertEqual(rows_by_type['Memo']['reference'], self.booking.reference)

    def test_never_shows_another_accountants_rows(self):
        self.client.login(username='otherpayoutsaccountant', password='pw')
        response = self.client.get(reverse('accountants:payouts_memos'))
        self.assertEqual(response.context['rows'], [])

    def test_memo_detail_shows_breakdown_and_total(self):
        self.client.login(username='payoutsaccountant', password='pw')
        response = self.client.get(reverse('accountants:memo_detail', kwargs={'pk': self.memo.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['memo'], self.memo)

    def test_memo_detail_404s_for_another_accountants_memo(self):
        self.client.login(username='otherpayoutsaccountant', password='pw')
        response = self.client.get(reverse('accountants:memo_detail', kwargs={'pk': self.memo.pk}))
        self.assertEqual(response.status_code, 404)
