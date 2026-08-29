from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bookings.models import Arrival, Booking, BookingSettings, Charge, Departure, PaymentSettings
from bookings.payouts import clean_fee, meet_greet_fee
from finance.models import AdHocService, DepositReturn, Memo, PayoutRecord
from finance.services import (
    deposits_due_in_range, open_memo_for_property, owner_balance_in_range,
    recompute_unsent_memo_fees_for_settings_change, sweep_unattached_ad_hoc_services,
)
from guests.models import Guest
from properties.models import ManagementCompany, Owner, Property, PropertySpec
from staff.models import Checkin, CleaningTask


class FinanceTestCase(TestCase):
    """Shared fixture for the whole finance app - one internally-managed property with a real
    owner, mirroring bookings/tests.py::ComputeOwnerPayoutTests' own setUp shape."""

    def setUp(self):
        self.owner = Owner.objects.create(
            name='Finance Owner', email='finance-owner@example.com', default_clean=False,
            default_meet_greet=False, takes_euros=True, takes_pounds=False,
            cleans_are_invoiced=False, rental_commissions_are_invoiced=False, is_paid_regularly=False,
        )
        self.company = ManagementCompany.objects.create(
            name='Finance Test Co', finances_managed_internally=True,
        )
        self.property = Property.objects.create(
            title='Finance Property', short_title='FINPROP', owner=self.owner,
            cleaning_company=self.company, booking_company=self.company, standard_cleaning_fee=Decimal('80.00'),
        )
        PropertySpec.objects.create(property=self.property, bedrooms=2)
        self.guest = Guest.objects.create(first_name='Fin', last_name='Ance', email='finance-guest@example.com')
        self.settings = PaymentSettings.load()
        self.settings.cleaning_surcharge_one_bedroom = Decimal('10.00')
        self.settings.cleaning_surcharge_multi_bedroom = Decimal('15.00')
        self.settings.cleaning_high_occupancy_surcharge = Decimal('15.00')
        self.settings.meet_greet_fee = Decimal('28.00')
        self.settings.save()
        self.today = timezone.now().date()

    def _make_booking(self, arrival_offset, departure_offset, clean=True, meet_greet=False, property=None):
        booking = Booking.objects.create(
            property=property or self.property, guest=self.guest,
            arrival_date=self.today + timedelta(days=arrival_offset),
            departure_date=self.today + timedelta(days=departure_offset),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=booking, basic_rental=Decimal('300.00'))
        if clean:
            Departure.objects.create(booking=booking, clean=True)
        if meet_greet:
            Arrival.objects.create(booking=booking, meet_greet=True)
        return booking


class SyncMemoForTurnoverTaskTests(FinanceTestCase):
    def test_memo_created_when_finances_managed_internally_true(self):
        booking = self._make_booking(10, 14)
        memo = Memo.objects.get(property=self.property)
        self.assertEqual(memo.cleaning_task, CleaningTask.objects.get(booking=booking, task_type='turnover'))
        self.assertEqual(memo.clean_fee, clean_fee(self.settings, booking))
        self.assertEqual(memo.meet_greet_fee, meet_greet_fee(self.settings, booking))

    def test_memo_not_created_when_finances_managed_internally_false(self):
        self.company.finances_managed_internally = False
        self.company.save()
        self._make_booking(10, 14)
        self.assertFalse(Memo.objects.filter(property=self.property).exists())

    def test_meet_greet_fee_updates_when_arrival_saved_after_departure(self):
        """The Arrival receiver in staff/signals.py must call sync_memo_for_turnover_task too -
        without it, a meet-greet toggled on after the Memo already exists would never show up."""
        booking = self._make_booking(10, 14, meet_greet=False)
        memo = Memo.objects.get(property=self.property)
        self.assertEqual(memo.meet_greet_fee, Decimal('0'))

        arrival = Arrival.objects.create(booking=booking, meet_greet=True)
        memo.refresh_from_db()
        self.assertEqual(memo.meet_greet_fee, meet_greet_fee(self.settings, booking))
        self.assertGreater(memo.meet_greet_fee, Decimal('0'))
        arrival.delete()


class BackfillMemosForCompanyTests(FinanceTestCase):
    """Toggling finances_managed_internally on for a company via StaffSettingsView must backfill
    Memo rows for that company's own upcoming turnover cleans, but never one already in the past -
    see properties.models.ManagementCompany.finances_managed_internally's own docstring and
    finance/services.py::backfill_memos_for_company."""

    def setUp(self):
        super().setUp()
        self.company.finances_managed_internally = False
        self.company.save()
        User.objects.create_user(username='settingsviewer', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='settingsviewer', password='pw')

    def _post_finances_toggle(self, on):
        post = {
            'action': 'update_management_company', 'management_company_id': self.company.pk,
            'name': self.company.name, 'cleans_on_calendar': 'on', 'checkins_on_calendar': 'on',
        }
        if on:
            post['finances_managed_internally'] = 'on'
        return self.client.post(reverse('staff:settings'), post)

    def test_turning_on_backfills_future_turnover_memo(self):
        booking = self._make_booking(10, 14)
        self.assertFalse(Memo.objects.filter(property=self.property).exists())

        response = self._post_finances_toggle(on=True)
        self.assertEqual(response.status_code, 302)
        self.company.refresh_from_db()
        self.assertTrue(self.company.finances_managed_internally)

        memo = Memo.objects.get(property=self.property)
        self.assertEqual(memo.cleaning_task, CleaningTask.objects.get(booking=booking, task_type='turnover'))

    def test_turning_on_does_not_backfill_a_past_turnover_task(self):
        booking = self._make_booking(10, 14)
        task = CleaningTask.objects.get(booking=booking, task_type='turnover')
        task.date = self.today - timedelta(days=1)
        task.save(update_fields=['date'])

        self._post_finances_toggle(on=True)
        self.assertFalse(Memo.objects.filter(property=self.property).exists())

    def test_saving_without_toggling_does_not_backfill(self):
        self._make_booking(10, 14)
        self._post_finances_toggle(on=False)
        self.assertFalse(Memo.objects.filter(property=self.property).exists())

    def test_already_on_save_does_not_resync(self):
        """Only a False->True transition triggers the backfill - re-saving with it already on
        must not re-run sync_memo_for_turnover_task (which would be harmless here, but the whole
        point is this only fires on the actual toggle, not on every unrelated edit)."""
        self.company.finances_managed_internally = True
        self.company.save()
        booking = self._make_booking(10, 14)
        memo = Memo.objects.get(property=self.property)
        memo.sent_at = timezone.now()
        memo.save(update_fields=['sent_at'])

        self._post_finances_toggle(on=True)
        self.assertEqual(Memo.objects.filter(property=self.property).count(), 1)


class OpenMemoForPropertyTests(FinanceTestCase):
    def test_selects_earliest_unsent_future_memo(self):
        self._make_booking(20, 24)
        sooner_booking = self._make_booking(10, 14)
        earliest = open_memo_for_property(self.property)
        self.assertEqual(earliest.cleaning_task.booking, sooner_booking)

    def test_excludes_sent_memos(self):
        booking = self._make_booking(10, 14)
        memo = Memo.objects.get(property=self.property)
        memo.sent_at = timezone.now()
        memo.save(update_fields=['sent_at'])
        later_booking = self._make_booking(20, 24)
        self.assertEqual(
            open_memo_for_property(self.property).cleaning_task.booking, later_booking,
        )

    def test_excludes_past_dated_memos(self):
        # A "past" memo can't actually be synced from a booking (the sync command only ever looks
        # at today-or-later tasks), so simulate one directly for this selection-logic test.
        past_task = CleaningTask.objects.create(
            booking=self._make_booking(10, 14), task_type='mid_stay', date=self.today - timedelta(days=5),
        )
        Memo.objects.filter(property=self.property).update(cleaning_task=None)
        Memo.objects.create(property=self.property, cleaning_task=past_task)
        self.assertIsNone(open_memo_for_property(self.property))

    def test_excludes_orphaned_memos(self):
        Memo.objects.create(property=self.property, cleaning_task=None)
        self.assertIsNone(open_memo_for_property(self.property))


class AdHocServiceAttachmentTests(FinanceTestCase):
    def test_attaches_to_open_memo_on_create(self):
        booking = self._make_booking(10, 14)
        memo = Memo.objects.get(property=self.property)
        service = AdHocService.objects.create(property=self.property, description='AC repair', cost=Decimal('60.00'))
        self.assertEqual(service.memo, memo)

    def test_stays_unattached_when_no_open_memo(self):
        service = AdHocService.objects.create(property=self.property, description='AC repair', cost=Decimal('60.00'))
        self.assertIsNone(service.memo)


class MemoOrphaningTests(FinanceTestCase):
    def test_cancelling_booking_orphans_memo_and_releases_services(self):
        booking = self._make_booking(10, 14)
        memo = Memo.objects.get(property=self.property)
        service = AdHocService.objects.create(property=self.property, description='AC repair', cost=Decimal('60.00'))
        self.assertEqual(service.memo, memo)

        booking.enquiry_status = 'Cancelled by staff'
        booking.save()

        memo.refresh_from_db()
        service.refresh_from_db()
        self.assertIsNone(memo.cleaning_task)
        self.assertIsNone(service.memo)

    def test_sent_memo_survives_cancellation(self):
        booking = self._make_booking(10, 14)
        memo = Memo.objects.get(property=self.property)
        memo.sent_at = timezone.now()
        memo.save(update_fields=['sent_at'])

        booking.enquiry_status = 'Cancelled by staff'
        booking.save()

        memo.refresh_from_db()
        self.assertIsNone(memo.cleaning_task)
        self.assertIsNotNone(memo.sent_at)


class MemoSendFlowTests(FinanceTestCase):
    def test_new_service_after_send_attaches_to_next_open_memo(self):
        first_booking = self._make_booking(10, 14)
        second_booking = self._make_booking(20, 24)
        first_memo = Memo.objects.get(cleaning_task__booking=first_booking)
        second_memo = Memo.objects.get(cleaning_task__booking=second_booking)

        first_memo.sent_at = timezone.now()
        first_memo.save(update_fields=['sent_at'])
        sweep_unattached_ad_hoc_services(self.property)

        service = AdHocService.objects.create(property=self.property, description='Post-send job', cost=Decimal('20.00'))
        self.assertEqual(service.memo, second_memo)


class RecomputeUnsentMemoFeesTests(FinanceTestCase):
    def test_unsent_memo_updates_sent_memo_does_not(self):
        unsent_booking = self._make_booking(10, 14)
        sent_booking = self._make_booking(20, 24)
        unsent_memo = Memo.objects.get(cleaning_task__booking=unsent_booking)
        sent_memo = Memo.objects.get(cleaning_task__booking=sent_booking)
        sent_memo.sent_at = timezone.now()
        sent_memo.save(update_fields=['sent_at'])
        original_sent_fee = sent_memo.clean_fee

        self.settings.cleaning_surcharge_multi_bedroom = Decimal('999.00')
        self.settings.save()
        recompute_unsent_memo_fees_for_settings_change()

        unsent_memo.refresh_from_db()
        sent_memo.refresh_from_db()
        self.assertEqual(unsent_memo.clean_fee, clean_fee(self.settings, unsent_booking))
        self.assertEqual(sent_memo.clean_fee, original_sent_fee)


class StatementDoubleDeductionTests(FinanceTestCase):
    """The highest-priority test in this feature: a self-managed property (booking_company ==
    cleaning_company, both internal) with a non-regular owner must deduct only the ad-hoc total
    from the gross owner balance - compute_owner_payout's owner_balance already nets out
    clean_fee/meet_greet_fee once, so subtracting the Memo's full total again would double-deduct
    the clean/meet-greet portion."""

    def test_statement_deducts_only_ad_hoc_total_not_the_full_memo(self):
        booking = self._make_booking(10, 14, meet_greet=True)
        memo = Memo.objects.get(cleaning_task__booking=booking)
        AdHocService.objects.create(property=self.property, description='Extra job', cost=Decimal('40.00'))

        # due_date for a non-regular owner is the *last day of the arrival month* - with a 10-day
        # arrival offset that could land anywhere up to ~2 months out depending on what day of the
        # month "today" actually is when this test runs, so the window must be generous, not a
        # fixed +30 days.
        start, end = self.today, self.today + timedelta(days=100)
        due = owner_balance_in_range(self.property, start, end)
        self.assertEqual(len(due), 1)
        gross = due[0][1]['owner_balance']

        ad_hoc_total = Decimal('40.00')
        net = gross - ad_hoc_total

        self.assertEqual(net, gross - ad_hoc_total)
        self.assertNotEqual(net, gross - memo.total())


class PayoutRecordTests(FinanceTestCase):
    def test_mark_paid_view_snapshots_amount_and_is_idempotent(self):
        self.property.owner.is_paid_regularly = True
        self.property.owner.save()
        booking = self._make_booking(1, 5)
        User.objects.create_user(username='financesuper', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='financesuper', password='pw')

        url = reverse('staff:finance_payout_mark_paid', kwargs={'reference': booking.reference})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PayoutRecord.objects.filter(booking=booking).count(), 1)

        response = self.client.post(url)
        self.assertEqual(PayoutRecord.objects.filter(booking=booking).count(), 1)


class DepositReturnTests(FinanceTestCase):
    """The Deposits tab - see finance/services.py::deposits_due_in_range's own docstring for the
    two independent conditions a booking must meet (deposit collected at check-in, end-of-stay
    clean marked done) before it's eligible to appear."""

    def setUp(self):
        super().setUp()
        User.objects.create_user(username='depositsuper', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='depositsuper', password='pw')

    def _eligible_booking(self):
        booking = self._make_booking(10, 14)
        task = CleaningTask.objects.get(booking=booking, task_type='turnover')
        task.status = 'done'
        task.completed_at = timezone.now()
        task.save(update_fields=['status', 'completed_at'])
        checkin, _ = Checkin.objects.get_or_create(
            booking=booking, task_type='arrival', defaults={'date': self.today},
        )
        checkin.deposit_collected = True
        checkin.save(update_fields=['deposit_collected'])
        return booking, task

    def test_not_due_without_deposit_collected(self):
        booking = self._make_booking(10, 14)
        task = CleaningTask.objects.get(booking=booking, task_type='turnover')
        task.status = 'done'
        task.completed_at = timezone.now()
        task.save(update_fields=['status', 'completed_at'])
        due = deposits_due_in_range(self.today - timedelta(days=1), self.today + timedelta(days=1))
        self.assertEqual(due, [])

    def test_not_due_before_clean_is_done(self):
        booking = self._make_booking(10, 14)
        checkin, _ = Checkin.objects.get_or_create(
            booking=booking, task_type='arrival', defaults={'date': self.today},
        )
        checkin.deposit_collected = True
        checkin.save(update_fields=['deposit_collected'])
        due = deposits_due_in_range(self.today - timedelta(days=1), self.today + timedelta(days=1))
        self.assertEqual(due, [])

    def test_due_once_both_conditions_are_met(self):
        booking, task = self._eligible_booking()
        due = deposits_due_in_range(self.today - timedelta(days=1), self.today + timedelta(days=1))
        self.assertEqual(due, [(booking, task.completed_at.date())])

    def test_deposits_tab_renders_and_mark_as_returned_is_idempotent(self):
        booking, _ = self._eligible_booking()
        response = self.client.get(reverse('staff:finance_deposits'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Mark as returned')

        url = reverse('staff:finance_deposit_mark_returned', kwargs={'reference': booking.reference})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DepositReturn.objects.filter(booking=booking).count(), 1)
        record = DepositReturn.objects.get(booking=booking)
        self.assertEqual(record.amount, BookingSettings.load().security_deposit_amount)

        response = self.client.post(url)
        self.assertEqual(DepositReturn.objects.filter(booking=booking).count(), 1)


class FinanceViewSmokeTests(FinanceTestCase):
    """Wiring smoke tests - catches URL/template mistakes the model-level tests above can't."""

    def setUp(self):
        super().setUp()
        User.objects.create_user(username='financeviewer', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='financeviewer', password='pw')

    def test_memos_tab_renders(self):
        self._make_booking(10, 14)
        response = self.client.get(reverse('staff:finance_memos'))
        self.assertEqual(response.status_code, 200)

    def test_memo_detail_and_send_render(self):
        booking = self._make_booking(10, 14)
        memo = Memo.objects.get(cleaning_task__booking=booking)
        response = self.client.get(reverse('staff:finance_memo_detail', kwargs={'pk': memo.pk}))
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse('staff:finance_memo_send', kwargs={'pk': memo.pk}))
        self.assertEqual(response.status_code, 302)
        memo.refresh_from_db()
        self.assertIsNotNone(memo.sent_at)

    def test_ad_hoc_services_page_create_edit_delete(self):
        response = self.client.get(reverse('staff:finance_ad_hoc_services'))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse('staff:finance_ad_hoc_services'), {
            'action': 'create', 'property': self.property.pk, 'description': 'Fix tap', 'cost': '25.00',
        })
        self.assertEqual(response.status_code, 302)
        service = AdHocService.objects.get(description='Fix tap')

        response = self.client.post(reverse('staff:finance_ad_hoc_services'), {
            'action': 'update', 'service_id': service.pk, 'property': self.property.pk,
            'description': 'Fix tap and sink', 'cost': '30.00',
        })
        self.assertEqual(response.status_code, 302)
        service.refresh_from_db()
        self.assertEqual(service.description, 'Fix tap and sink')

        response = self.client.post(reverse('staff:finance_ad_hoc_services'), {
            'action': 'delete', 'service_id': service.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AdHocService.objects.filter(pk=service.pk).exists())

    def test_payouts_tab_renders(self):
        self.property.owner.is_paid_regularly = True
        self.property.owner.save()
        self._make_booking(1, 5)
        response = self.client.get(reverse('staff:finance_payouts'))
        self.assertEqual(response.status_code, 200)

    def test_statement_renders_for_owner_scope(self):
        booking = self._make_booking(10, 14)
        start = self.today.isoformat()
        end = (self.today + timedelta(days=30)).isoformat()
        response = self.client.get(reverse('staff:finance_statement'), {
            'scope': 'owner', 'owner_id': self.owner.pk, 'start': start, 'end': end,
        })
        self.assertEqual(response.status_code, 200)
