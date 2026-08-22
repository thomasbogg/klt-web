from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bookings.models import BalancePayment, Booking, Charge, Payment
from guests.models import Guest
from properties.models import Property
from staff.models import Deduction, OwnerPayment, TaskHistoryEntry
from staff.utils import booking_stage, next_step_hint, status_bucket

User = get_user_model()


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
        User.objects.create_user(username='areal_staffer', password='pw', is_staff=True)
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


class StaffBookingDetailViewTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(username='staffer', password='pw', is_staff=True)
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
        self.assertEqual(list(response.context['owner_payments']), [])
        self.assertEqual(list(response.context['task_history']), [])
        self.assertIn('items', response.context['extras'])

    def test_update_booking_info_saves_fields(self):
        response = self.client.post(self.url, {
            'action': 'update_booking_info', 'property': self.other_property.pk,
            'arrival_date': (self.start + timedelta(days=1)).isoformat(),
            'departure_date': (self.end + timedelta(days=1)).isoformat(),
            'adults': '3', 'children': '1', 'babies': '0', 'is_owner': 'on',
        })
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.property, self.other_property)
        self.assertEqual(self.booking.arrival_date, self.start + timedelta(days=1))
        self.assertEqual(self.booking.adults, 3)
        self.assertEqual(self.booking.children, 1)
        self.assertTrue(self.booking.is_owner)

    def test_update_booking_info_rejects_overlap(self):
        Booking.objects.create(
            property=self.property, guest=self.guest,
            arrival_date=self.start + timedelta(days=30), departure_date=self.end + timedelta(days=30),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        response = self.client.post(self.url, {
            'action': 'update_booking_info',
            'arrival_date': (self.start + timedelta(days=30)).isoformat(),
            'departure_date': (self.end + timedelta(days=30)).isoformat(),
            'adults': '2', 'children': '0', 'babies': '0',
        })
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.arrival_date, self.start)  # untouched

    def test_update_guest_info_saves_fields(self):
        response = self.client.post(self.url, {
            'action': 'update_guest_info', 'first_name': 'Elena', 'last_name': 'Costa-Silva',
            'email': 'new-email@example.com', 'phone': '999888777', 'nif_number': '123456789',
            'nationality': 'Portuguese',
        })
        self.assertRedirects(response, self.url)
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.last_name, 'Costa-Silva')
        self.assertEqual(self.guest.email, 'new-email@example.com')
        self.assertEqual(self.guest.nif_number, '123456789')

    def test_update_guest_info_never_blanks_required_last_name(self):
        self.client.post(self.url, {'action': 'update_guest_info', 'last_name': ''})
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.last_name, 'Costa')

    def test_update_charges_saves_fields_and_logs_task_history(self):
        response = self.client.post(self.url, {
            'action': 'update_charges', 'currency': 'GBP', 'basic_rental': '750.00',
            'admin': '41.25', 'security': '200.00', 'due_at_booking': '190.00', 'due_at_balance': '600.00',
        })
        self.assertRedirects(response, self.url)
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.basic_rental, Decimal('750.00'))
        self.assertEqual(self.charge.currency, 'GBP')
        self.assertEqual(TaskHistoryEntry.objects.filter(booking=self.booking).count(), 1)

    def test_update_charges_rejects_invalid_amount(self):
        self.client.post(self.url, {
            'action': 'update_charges', 'basic_rental': 'not-a-number', 'currency': 'EUR',
        })
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.basic_rental, Decimal('700.00'))  # untouched

    def test_update_enquiry_logs_status_change(self):
        response = self.client.post(self.url, {
            'action': 'update_enquiry', 'enquiry_source': 'Phone', 'enquiry_status': 'Guests on-site',
            'enquiry_date': date.today().isoformat(),
        })
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.enquiry_status, 'Guests on-site')
        self.assertEqual(self.booking.enquiry_source, 'Phone')
        entry = TaskHistoryEntry.objects.get(booking=self.booking)
        self.assertIn('Booking confirmed', entry.description)
        self.assertIn('Guests on-site', entry.description)

    def test_update_enquiry_no_task_entry_when_status_unchanged(self):
        self.client.post(self.url, {'action': 'update_enquiry', 'enquiry_status': 'Booking confirmed'})
        self.assertEqual(TaskHistoryEntry.objects.filter(booking=self.booking).count(), 0)

    def test_update_payments_saves_statuses(self):
        response = self.client.post(self.url, {
            'action': 'update_payments', 'payment_status': 'paid', 'balance_payment_status': 'in_progress',
        })
        self.assertRedirects(response, self.url)
        self.payment.refresh_from_db()
        self.balance_payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'paid')
        self.assertEqual(self.balance_payment.status, 'in_progress')

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
            'action': 'add_owner_payment', 'amount': '400.00', 'currency': 'EUR', 'note': 'August payout',
        })
        self.assertRedirects(response, self.url)
        payment = OwnerPayment.objects.get(booking=self.booking)
        self.assertEqual(payment.amount, Decimal('400.00'))
        self.assertEqual(payment.note, 'August payout')

    def test_add_task_note(self):
        response = self.client.post(self.url, {'action': 'add_task_note', 'description': 'Called guest re: extras'})
        self.assertRedirects(response, self.url)
        entry = TaskHistoryEntry.objects.get(booking=self.booking)
        self.assertEqual(entry.description, 'Called guest re: extras')

    def test_unknown_action_is_a_noop(self):
        response = self.client.post(self.url, {'action': 'not_a_real_action'})
        self.assertRedirects(response, self.url)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.arrival_date, self.start)


class StaffBookingLookupViewTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='lookup_staffer', password='pw', is_staff=True)
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
        booking = self._booking(date.today() - timedelta(days=10), date.today() - timedelta(days=3), 'Guests have departed')
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
        User.objects.create_user(username='home_staffer', password='pw', is_staff=True)
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
            is_owner=False, enquiry_status='Guests have departed', enquiry_source='Website',
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


class StaffGuestListViewTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='guests_staffer', password='pw', is_staff=True)
        self.client.login(username='guests_staffer', password='pw')

        self.property = Property.objects.create(title='Guest List Property', short_title='GUESTLISTPROP')
        self.adams = Guest.objects.create(
            first_name='Carly', last_name='Adams', email='carly.adams@example.com', phone='+351911222333',
        )
        self.ackroyd = Guest.objects.create(first_name='Nora', last_name='Ackroyd', email='nora.ackroyd@example.com')
        self.baxter = Guest.objects.create(first_name='Bob', last_name='Baxter', email='bob.baxter@example.com')

        self.adams_old_booking = Booking.objects.create(
            property=self.property, guest=self.adams,
            arrival_date=date.today() + timedelta(days=10), departure_date=date.today() + timedelta(days=17),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.adams_latest_booking = Booking.objects.create(
            property=self.property, guest=self.adams,
            arrival_date=date.today() + timedelta(days=40), departure_date=date.today() + timedelta(days=47),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        self.url = reverse('staff:guest_list')

    def test_default_shows_only_first_letter(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context['selected_letter'], 'A')
        surnames = {row['guest'].last_name for row in response.context['rows']}
        self.assertEqual(surnames, {'Adams', 'Ackroyd'})

    def test_letter_filter_narrows_to_surname(self):
        response = self.client.get(self.url, {'letter': 'B'})
        surnames = {row['guest'].last_name for row in response.context['rows']}
        self.assertEqual(surnames, {'Baxter'})

    def test_all_letter_shows_every_guest(self):
        response = self.client.get(self.url, {'letter': 'ALL'})
        surnames = {row['guest'].last_name for row in response.context['rows']}
        self.assertEqual(surnames, {'Adams', 'Ackroyd', 'Baxter'})

    def test_search_by_name_overrides_letter_filter(self):
        response = self.client.get(self.url, {'q': 'baxter'})
        surnames = {row['guest'].last_name for row in response.context['rows']}
        self.assertEqual(surnames, {'Baxter'})
        self.assertEqual(response.context['selected_letter'], '')

    def test_search_by_email(self):
        response = self.client.get(self.url, {'q': 'carly.adams'})
        surnames = {row['guest'].last_name for row in response.context['rows']}
        self.assertEqual(surnames, {'Adams'})

    def test_search_by_phone(self):
        response = self.client.get(self.url, {'q': '911222333'})
        surnames = {row['guest'].last_name for row in response.context['rows']}
        self.assertEqual(surnames, {'Adams'})

    def test_guest_links_to_most_recent_booking(self):
        response = self.client.get(self.url, {'letter': 'A'})
        rows = {row['guest'].pk: row['latest_booking'] for row in response.context['rows']}
        self.assertEqual(rows[self.adams.pk], self.adams_latest_booking)
        self.assertContains(response, reverse('staff:booking_detail', kwargs={'reference': self.adams_latest_booking.reference}))

    def test_guest_with_no_booking_renders_without_error(self):
        response = self.client.get(self.url, {'letter': 'B'})
        self.assertEqual(response.status_code, 200)
        row = next(row for row in response.context['rows'] if row['guest'].pk == self.baxter.pk)
        self.assertIsNone(row['latest_booking'])
