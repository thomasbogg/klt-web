from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bookings.models import BalancePayment, Booking, Charge, Payment
from guests.models import Guest
from properties.models import (
    Accountant, Amenity, Location, Manager, Owner, Price, Property, PropertyImage, PropertySpec,
    SEFDetail, iCalLink,
)
from staff.models import Deduction, OwnerPayment, TaskHistoryEntry
from staff.utils import booking_stage, next_step_hint, status_bucket

User = get_user_model()


def make_owner(**overrides):
    defaults = dict(
        name='Test Owner', email='owner@example.com', default_clean=False, default_meet_greet=False,
        takes_euros=True, takes_pounds=False, cleans_are_invoiced=False,
        rental_commissions_are_invoiced=False, is_paid_regularly=True,
    )
    defaults.update(overrides)
    return Owner.objects.create(**defaults)


def make_manager(**overrides):
    defaults = dict(
        company='Test Management Co', head_name='Head Person', head_email='head@example.com',
        head_phone='+351900000001', maintenance_name='Maint Person', maintenance_phone='+351900000002',
        maintenance_email='maint@example.com', liaison_name='Liaison Person', liaison_phone='+351900000003',
        liaison_email='liaison@example.com', cleaning_name='Cleaning Person', cleaning_phone='+351900000004',
        cleaning_email='cleaning@example.com',
    )
    defaults.update(overrides)
    return Manager.objects.create(**defaults)


def make_accountant(**overrides):
    defaults = dict(
        company='Test Accounting Co', name='Accountant Person', email='accountant@example.com',
        phone='+351900000005',
    )
    defaults.update(overrides)
    return Accountant.objects.create(**defaults)


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

    def test_includes_inline_booking_lookup_form(self):
        response = self.client.get(self.url)
        self.assertContains(response, f'action="{reverse("staff:booking_lookup")}"')
        self.assertContains(response, 'name="reference"')


class StaffGuestListViewTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='guests_staffer', password='pw', is_staff=True)
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
        User.objects.create_user(username='guest_detail_staffer', password='pw', is_staff=True)
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
            'phone': '+351900000000', 'id_card': 'X123456', 'nif_number': '123456789',
            'nationality': 'British', 'preferred_language': 'PT',
        })
        self.assertRedirects(response, self.url)
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.last_name, 'Adams-Smith')
        self.assertEqual(self.guest.email, 'new@example.com')
        self.assertEqual(self.guest.id_card, 'X123456')
        self.assertEqual(self.guest.preferred_language, 'PT')

    def test_update_guest_info_never_blanks_required_last_name(self):
        self.client.post(self.url, {'last_name': '   '})
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.last_name, 'Adams')


class StaffPropertyListViewTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='property_list_staffer', password='pw', is_staff=True)
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
        User.objects.create_user(username='property_create_staffer', password='pw', is_staff=True)
        self.client.login(username='property_create_staffer', password='pw')
        self.url = reverse('staff:property_create')

    def test_get_renders_form(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_post_creates_property_and_redirects_to_detail(self):
        owner, manager, location = make_owner(), make_manager(), make_location()
        response = self.client.post(self.url, {
            'title': 'Brand New Property', 'short_title': 'BRANDNEW', 'al_number': '12345',
            'owner': owner.pk, 'manager': manager.pk, 'location': location.pk,
            'standard_cleaning_fee': '75.00',
        })
        property = Property.objects.get(short_title='BRANDNEW')
        self.assertRedirects(response, reverse('staff:property_detail', kwargs={'pk': property.pk}))
        self.assertEqual(property.title, 'Brand New Property')
        self.assertEqual(property.al_number, 12345)

    def test_post_missing_owner_manager_location_shows_error_and_does_not_create(self):
        """owner/manager/location are DB-nullable but not blank=True, so Django's own admin (and
        this form, via full_clean()) has always required all three up front - not new behaviour,
        just now exercised through this view too."""
        response = self.client.post(self.url, {'title': 'Incomplete Property', 'short_title': 'INCOMPLETE'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Property.objects.filter(short_title='INCOMPLETE').exists())

    def test_post_duplicate_title_shows_error_and_does_not_create(self):
        owner, manager, location = make_owner(), make_manager(), make_location()
        Property.objects.create(
            title='Existing Title', short_title='EXISTINGONE', owner=owner, manager=manager, location=location,
        )
        response = self.client.post(self.url, {
            'title': 'Existing Title', 'short_title': 'EXISTINGTWO',
            'owner': owner.pk, 'manager': manager.pk, 'location': location.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Property.objects.filter(short_title='EXISTINGTWO').exists())


class StaffPropertyDetailViewTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='property_detail_staffer', password='pw', is_staff=True)
        self.client.login(username='property_detail_staffer', password='pw')

        self.owner = make_owner()
        self.manager = make_manager()
        self.accountant = make_accountant()
        self.location = make_location()
        self.property = Property.objects.create(title='Detail Property', short_title='DETAILPROP')
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

    def test_update_property_info_saves_fields(self):
        response = self.client.post(self.url, {
            'action': 'update_property_info', 'title': 'Detail Property', 'short_title': 'DETAILPROP',
            'door_number': '12B', 'owner': self.owner.pk, 'manager': self.manager.pk,
            'location': self.location.pk, 'accountant': self.accountant.pk, 'al_number': '9999',
            'we_book': 'on', 'booking_com_id': 'BDC123', 'standard_cleaning_fee': '90.00',
        })
        self.assertRedirects(response, f'{self.url}?panel=main')
        self.property.refresh_from_db()
        self.assertEqual(self.property.door_number, '12B')
        self.assertEqual(self.property.owner_id, self.owner.pk)
        self.assertEqual(self.property.al_number, 9999)
        self.assertTrue(self.property.we_book)
        self.assertFalse(self.property.we_clean)
        self.assertEqual(self.property.standard_cleaning_fee, Decimal('90.00'))

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
            'bed_sizes': '180 x 200',
        })
        amenities = Amenity.objects.get(property=self.property)
        self.assertTrue(amenities.wifi)
        self.assertTrue(amenities.pool)
        self.assertFalse(amenities.hot_tub)
        self.assertEqual(amenities.double_beds, 2)
        self.assertEqual(amenities.bed_sizes, '180 x 200')

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
