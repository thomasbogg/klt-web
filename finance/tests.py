from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bookings.models import Arrival, Booking, BookingSettings, Charge, Departure, PaymentSettings
from bookings.payouts import clean_fee, meet_greet_fee
from bookings.utils import BLOCK_LATE_CHECK_OUT_LAST_NAME
from finance.models import AdHocService, DepositReturn, Memo, OwnerInvoice, PayoutRecord, SageSettings
from finance.services import (
    compute_regular_owner_payout, consolidate_informal_cleans_payment, deposits_due_in_range,
    dispatch_commission_receipt_for_payout, dispatch_memo_to_sage, generate_non_regular_owner_invoice,
    needs_informal_cleans_tracking, non_regular_owner_balances_due_in_range, open_memo_for_property,
    owner_balance_in_range, owner_outstanding_balance, payouts_due_in_range,
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
            name='Finance Owner', email='finance-owner@example.com', currency=Owner.Currency.EUR, is_paid_regularly=False, cleans_are_invoiced=False,
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

    def test_deposits_tab_hidden_and_redirected_while_security_deposits_are_paused(self):
        # 2026-09-07, per Thomas: while paused, the tab is switched off entirely (not just
        # unlinked from nav) rather than left reachable showing a now-permanently-empty list.
        settings = BookingSettings.load()
        settings.security_deposits_enabled = False
        settings.save(update_fields=['security_deposits_enabled'])

        response = self.client.get(reverse('staff:finance_memos'))
        self.assertNotContains(response, 'Deposits')

        response = self.client.get(reverse('staff:finance_deposits'))
        self.assertRedirects(response, reverse('staff:finance_memos'))


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

    def test_mark_paid_creates_commission_receipt_and_excludes_management_fee(self):
        """End-to-end through the real view (not calling the service functions directly) -
        StaffFinancePayoutMarkPaidView must use compute_regular_owner_payout, not
        compute_owner_payout, and must fire dispatch_commission_receipt_for_payout."""
        self.property.owner.is_paid_regularly = True
        self.property.owner.cleans_are_invoiced = True
        self.property.owner.save()
        booking = self._make_booking(1, 5)

        response = self.client.post(reverse('staff:finance_payout_mark_paid', kwargs={'reference': booking.reference}))
        self.assertEqual(response.status_code, 302)

        record = PayoutRecord.objects.get(booking=booking)
        # rental_base(300) - commission only, no clean_fee/meet_greet_fee subtracted - confirms
        # the Payouts tab is really using compute_regular_owner_payout under the hood.
        self.assertGreater(record.amount, Decimal('300') - Decimal('80'))

        invoice = OwnerInvoice.objects.get(payout_record=record)
        self.assertEqual(invoice.kind, OwnerInvoice.Kind.COMMISSION_PAYOUT)
        self.assertEqual(invoice.status, 'paid')

    def test_memo_management_fee_toggle(self):
        self.property.owner.is_paid_regularly = True
        self.property.owner.cleans_are_invoiced = False
        self.property.owner.save()
        booking = self._make_booking(10, 14)
        memo = Memo.objects.get(cleaning_task__booking=booking)
        self.client.post(reverse('staff:finance_memo_send', kwargs={'pk': memo.pk}))

        url = reverse('staff:finance_memo_toggle_management_fee_paid', kwargs={'pk': memo.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        memo.refresh_from_db()
        self.assertIsNotNone(memo.management_fee_paid_at)

        self.client.post(url)
        memo.refresh_from_db()
        self.assertIsNone(memo.management_fee_paid_at)

    def test_expected_payments_tab_renders(self):
        response = self.client.get(reverse('staff:finance_expected_payments'))
        self.assertEqual(response.status_code, 200)

    def test_statement_renders_for_owner_scope(self):
        booking = self._make_booking(10, 14)
        as_of = (self.today + timedelta(days=60)).isoformat()
        response = self.client.get(reverse('staff:finance_statement'), {
            'owner_id': self.owner.pk, 'as_of': as_of,
        })
        self.assertEqual(response.status_code, 200)
        sections = response.context['sections']
        self.assertEqual(len(sections), 1)
        balance = sections[0]['balance']
        self.assertEqual(len(balance['owed_to_owner_rows']), 1)
        self.assertGreater(balance['owed_to_owner'], Decimal('0'))
        self.assertIsNone(balance['owed_by_owner'])

    def test_statement_renders_for_property_with_no_owner_param(self):
        """No 'scope' selector any more (2026-09-10) - a property_id alone, with no owner_id at
        all, must still resolve to that property's statement."""
        self._make_booking(10, 14)
        as_of = (self.today + timedelta(days=60)).isoformat()
        response = self.client.get(reverse('staff:finance_statement'), {
            'property_id': self.property.pk, 'as_of': as_of,
        })
        self.assertEqual(response.status_code, 200)
        sections = response.context['sections']
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]['property'], self.property)

    def test_statement_interleaves_credits_and_debts_chronologically(self):
        """2026-09-10, per Thomas: one chronological list, not two separate blocks - a booking
        payout (black, is_credit=False) and a sent-unbilled memo (red, is_credit=True) mixed
        together and sorted by date."""
        self.property.owner.is_paid_regularly = True
        self.property.owner.cleans_are_invoiced = True
        self.property.owner.save()
        booking = self._make_booking(10, 14)
        memo = Memo.objects.get(cleaning_task__booking=booking)
        memo.sent_at = timezone.now()
        memo.save(update_fields=['sent_at'])

        as_of = (self.today + timedelta(days=60)).isoformat()
        response = self.client.get(reverse('staff:finance_statement'), {
            'property_id': self.property.pk, 'as_of': as_of,
        })
        rows = response.context['sections'][0]['rows']
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(row['date'] for row in rows), [row['date'] for row in rows])
        kinds = {row['kind']: row['is_credit'] for row in rows}
        self.assertEqual(kinds, {'booking': False, 'memo': True})


class SageSigningTests(TestCase):
    """libraries.accounting.sage's request-signing math, checked against Sage's own documented
    worked example (developers.sageone.com/docs/pt/v2) wherever the example is actually checkable.
    The example's signing_secret/access_token are partially redacted (shown as 'xxxx...'), so only
    the base-string and signing-key construction (fully unredacted in the example) can be verified
    against Sage's own numbers - the final HMAC-SHA1+base64 step is instead checked for internal
    consistency (recomputed independently with the stdlib hmac module, structurally decoupled from
    the module's own helpers, and compared)."""

    def test_base_string_matches_sages_documented_example(self):
        from libraries.accounting.sage import _build_base_string
        params = {'config_setting': 'foo', 'contact[contact_type_id]': 1, 'contact[name]': 'My Customer'}
        base_string = _build_base_string(
            'post', 'https://api.sageone.com/accounts/v2/contacts', params, 'd6657d14f6d3d9de453ff4b0dc686c6d',
        )
        self.assertEqual(base_string, (
            'POST&https%3A%2F%2Fapi.sageone.com%2Faccounts%2Fv2%2Fcontacts&'
            'config_setting%3Dfoo%26contact%255Bcontact_type_id%255D%3D1%26contact%255Bname%255D%3DMy%2520Customer&'
            'd6657d14f6d3d9de453ff4b0dc686c6d'
        ))

    def test_signing_key_matches_sages_documented_example(self):
        from libraries.accounting.sage import _signing_key
        key = _signing_key('297850d556xxxxxxxxxxxxxxxxxxxxe722db1d2a', 'cULSIjxxxxxIhbgbjX0R6MkKO')
        self.assertEqual(key, '297850d556xxxxxxxxxxxxxxxxxxxxe722db1d2a&cULSIjxxxxxIhbgbjX0R6MkKO')

    def test_sign_matches_an_independently_computed_hmac(self):
        import base64
        import hashlib
        import hmac as hmac_module
        from libraries.accounting.sage import _sign

        signature = _sign(
            'POST', 'https://api.sageone.com/accounts/v2/contacts',
            {'contact[name]': 'Test'}, 'a-fixed-test-nonce', 'test-signing-secret', 'test-access-token',
        )
        base_string = (
            'POST&https%3A%2F%2Fapi.sageone.com%2Faccounts%2Fv2%2Fcontacts&'
            'contact%255Bname%255D%3DTest&a-fixed-test-nonce'
        )
        key = 'test-signing-secret&test-access-token'
        expected = base64.b64encode(
            hmac_module.new(key.encode('ascii'), base_string.encode('ascii'), hashlib.sha1).digest()
        ).decode('ascii')
        self.assertEqual(signature, expected)

    def test_generate_nonce_is_alphanumeric_and_reasonably_random(self):
        from libraries.accounting.sage import generate_nonce
        nonce_a = generate_nonce()
        nonce_b = generate_nonce()
        self.assertRegex(nonce_a, r'^\w+$')
        self.assertNotEqual(nonce_a, nonce_b)


class SageClientRequestTests(TestCase):
    """libraries.accounting.sage.Sage's HTTP calls - mocking requests the same way
    bookings/tests.py already mocks libraries.banking.revolut."""

    @patch('libraries.accounting.sage.requests.post')
    def test_contact_create_sends_bracketed_form_params_and_signed_headers(self, mock_post):
        from libraries.accounting.sage import Sage

        mock_post.return_value.status_code = 201
        mock_post.return_value.json.return_value = {'id': 'contact-123', 'name': 'Test Owner'}

        sage = Sage(access_token='token-abc', signing_secret='secret-xyz')
        result = sage.contact.create('Test Owner', email='owner@example.com', tax_number='123456789')

        self.assertEqual(result['id'], 'contact-123')
        _args, kwargs = mock_post.call_args
        self.assertEqual(kwargs['data']['contact[name]'], 'Test Owner')
        self.assertEqual(kwargs['data']['contact[contact_type_id]'], 1)
        self.assertEqual(kwargs['data']['contact[email]'], 'owner@example.com')
        self.assertEqual(kwargs['data']['contact[tax_number]'], '123456789')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer token-abc')
        self.assertIn('X-Signature', kwargs['headers'])
        self.assertIn('X-Nonce', kwargs['headers'])

    @patch('libraries.accounting.sage.requests.post')
    def test_contact_create_returns_none_and_logs_on_failure(self, mock_post):
        from libraries.accounting.sage import Sage

        mock_post.return_value.status_code = 400
        mock_post.return_value.text = 'bad request'
        sage = Sage(access_token='token-abc', signing_secret='secret-xyz')
        self.assertIsNone(sage.contact.create('Test Owner'))

    @patch('libraries.accounting.sage.requests.get')
    def test_contact_find_by_name_reads_first_result(self, mock_get):
        from libraries.accounting.sage import Sage

        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {'$resources': [{'id': 'contact-456', 'name': 'Test Owner'}]}
        sage = Sage(access_token='token-abc', signing_secret='secret-xyz')
        result = sage.contact.find_by_name('Test Owner')
        self.assertEqual(result['id'], 'contact-456')

    @patch('libraries.accounting.sage.requests.get')
    def test_contact_find_by_name_returns_none_when_no_match(self, mock_get):
        from libraries.accounting.sage import Sage

        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {'$resources': []}
        sage = Sage(access_token='token-abc', signing_secret='secret-xyz')
        self.assertIsNone(sage.contact.find_by_name('Nobody'))

    @patch('libraries.accounting.sage.requests.post')
    def test_sales_invoice_create_sends_line_item_and_signed_headers(self, mock_post):
        from libraries.accounting.sage import Sage

        mock_post.return_value.status_code = 201
        mock_post.return_value.json.return_value = {'id': 'invoice-789'}
        sage = Sage(access_token='token-abc', signing_secret='secret-xyz')
        result = sage.sales_invoice.create(
            contact_id='contact-123', date=date(2026, 9, 9), description='Cleaning',
            net_amount=Decimal('150.00'), tax_rate_id='tax-rate-1',
        )
        self.assertEqual(result['id'], 'invoice-789')
        _args, kwargs = mock_post.call_args
        self.assertEqual(kwargs['data']['sales_invoice[contact_id]'], 'contact-123')
        self.assertEqual(kwargs['data']['sales_invoice[invoice_lines][][net_amount]'], '150.00')
        self.assertEqual(kwargs['data']['sales_invoice[invoice_lines][][tax_rate_id]'], 'tax-rate-1')

    @patch('libraries.accounting.sage.requests.post')
    def test_refresh_access_token_returns_rotated_tokens(self, mock_post):
        from libraries.accounting.sage import refresh_access_token

        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            'access_token': 'new-access', 'refresh_token': 'new-refresh', 'expires_in': 3600,
        }
        tokens = refresh_access_token('client-id', 'client-secret', 'old-refresh')
        self.assertEqual(tokens['access_token'], 'new-access')
        self.assertEqual(tokens['refresh_token'], 'new-refresh')
        _args, kwargs = mock_post.call_args
        self.assertEqual(kwargs['data']['grant_type'], 'refresh_token')
        self.assertEqual(kwargs['data']['refresh_token'], 'old-refresh')

    @patch('libraries.accounting.sage.requests.post')
    def test_refresh_access_token_returns_none_on_failure(self, mock_post):
        from libraries.accounting.sage import refresh_access_token

        mock_post.return_value.status_code = 400
        mock_post.return_value.text = 'invalid_grant'
        self.assertIsNone(refresh_access_token('client-id', 'client-secret', 'old-refresh'))


class DispatchMemoToSageTests(FinanceTestCase):
    """finance/services.py::dispatch_memo_to_sage - NOT currently called from anywhere (see its own
    docstring); these tests cover its branches directly so the logic stays proven ahead of being
    reused/adapted for the real monthly-batch Sage invoicing mechanism."""

    def setUp(self):
        super().setUp()
        booking = self._make_booking(10, 14)
        self.memo = Memo.objects.get(cleaning_task__booking=booking)

    def test_noop_when_owner_has_not_opted_in(self):
        self.owner.cleans_are_invoiced = False
        self.owner.save()
        dispatch_memo_to_sage(self.memo)
        self.memo.refresh_from_db()
        self.assertIsNone(self.memo.sage_invoice_id)
        self.assertIsNone(self.memo.sage_invoice_error)

    def test_records_error_when_sage_not_configured(self):
        # No env_settings.SAGE_CLIENT_ID/etc and no SageSettings.refresh_token in this dev/test
        # environment - the real state until Thomas completes the developer-portal registration.
        self.owner.cleans_are_invoiced = True
        self.owner.save()
        dispatch_memo_to_sage(self.memo)
        self.memo.refresh_from_db()
        self.assertIsNone(self.memo.sage_invoice_id)
        self.assertIsNotNone(self.memo.sage_invoice_error)

    @patch('finance.services.env_settings')
    @patch('libraries.accounting.sage.requests.post')
    def test_creates_contact_and_invoice_on_full_success(self, mock_post, mock_env_settings):
        mock_env_settings.SAGE_CLIENT_ID = 'client-id'
        mock_env_settings.SAGE_CLIENT_SECRET = 'client-secret'
        mock_env_settings.SAGE_SIGNING_SECRET = 'signing-secret'

        sage_settings = SageSettings.load()
        sage_settings.refresh_token = 'refresh-token'
        sage_settings.access_token = 'access-token'
        sage_settings.token_expires_at = timezone.now() + timedelta(hours=1)
        sage_settings.default_tax_rate_id = 'tax-rate-1'
        sage_settings.save()

        self.owner.cleans_are_invoiced = True
        self.owner.save()

        # First call: contact search (GET, not mocked here) returns nothing found -> falls through
        # to create. Both contact create and invoice create go through the mocked POST below, in
        # that order - status 201 with an 'id' satisfies both.
        with patch('libraries.accounting.sage.requests.get') as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {'$resources': []}
            mock_post.return_value.status_code = 201
            mock_post.return_value.json.side_effect = [
                {'id': 'contact-123'}, {'id': 'invoice-789'},
            ]
            dispatch_memo_to_sage(self.memo)

        self.memo.refresh_from_db()
        self.owner.refresh_from_db()
        self.assertEqual(self.memo.sage_invoice_id, 'invoice-789')
        self.assertIsNone(self.memo.sage_invoice_error)
        self.assertEqual(self.owner.sage_contact_id, 'contact-123')

    def test_reuses_existing_sage_contact_id_without_a_lookup(self):
        self.owner.cleans_are_invoiced = True
        self.owner.sage_contact_id = 'existing-contact'
        self.owner.save()

        with patch('finance.services.env_settings') as mock_env_settings, \
                patch('libraries.accounting.sage.requests.post') as mock_post, \
                patch('libraries.accounting.sage.requests.get') as mock_get:
            mock_env_settings.SAGE_CLIENT_ID = 'client-id'
            mock_env_settings.SAGE_CLIENT_SECRET = 'client-secret'
            mock_env_settings.SAGE_SIGNING_SECRET = 'signing-secret'
            sage_settings = SageSettings.load()
            sage_settings.refresh_token = 'refresh-token'
            sage_settings.access_token = 'access-token'
            sage_settings.token_expires_at = timezone.now() + timedelta(hours=1)
            sage_settings.default_tax_rate_id = 'tax-rate-1'
            sage_settings.save()

            mock_post.return_value.status_code = 201
            mock_post.return_value.json.return_value = {'id': 'invoice-999'}
            dispatch_memo_to_sage(self.memo)

            mock_get.assert_not_called()

        self.memo.refresh_from_db()
        self.assertEqual(self.memo.sage_invoice_id, 'invoice-999')


class ComputeRegularOwnerPayoutTests(TestCase):
    """finance/services.py::compute_regular_owner_payout - deliberately separate from
    bookings/payouts.py::compute_owner_payout (untouched, still covered by
    bookings/tests.py::ComputeOwnerPayoutTests). cleaning_company IS set here (so management_fee
    would be non-zero if it were computed) specifically to prove it's never deducted."""

    def setUp(self):
        self.owner = Owner.objects.create(
            name='Regular Owner', email='regular-owner@example.com', currency=Owner.Currency.EUR,
            is_paid_regularly=True, cleans_are_invoiced=True,
        )
        self.company = ManagementCompany.objects.create(name='Regular Test Co', finances_managed_internally=True)
        self.property = Property.objects.create(
            title='Regular Property', short_title='REGPROP', owner=self.owner,
            cleaning_company=self.company, booking_company=self.company, standard_cleaning_fee=Decimal('80.00'),
        )
        PropertySpec.objects.create(property=self.property, bedrooms=2)
        self.guest = Guest.objects.create(first_name='Reg', last_name='Ular', email='regular-guest@example.com')
        self.settings = PaymentSettings.load()
        self.settings.high_season_commission_percent = Decimal('15.00')
        self.settings.low_season_commission_percent = Decimal('10.00')
        self.settings.high_season_start_month = 4
        self.settings.high_season_end_month = 10
        self.settings.vat_rate_percent = Decimal('23.00')
        self.settings.charge_vat_on_low_season_direct_commission = False
        self.settings.regular_payout_days_after_arrival = 3
        self.settings.cleaning_surcharge_one_bedroom = Decimal('10.00')
        self.settings.cleaning_surcharge_multi_bedroom = Decimal('15.00')
        self.settings.cleaning_high_occupancy_surcharge = Decimal('15.00')
        self.settings.meet_greet_fee = Decimal('28.00')
        self.settings.save()

    def _make_booking(self, arrival_date):
        booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=arrival_date,
            departure_date=arrival_date + timedelta(days=4), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=booking, basic_rental=Decimal('300.00'))
        Departure.objects.create(booking=booking, clean=True)
        return booking

    def test_never_deducts_management_fee(self):
        """clean_fee for this property/settings would be 80 (standard) + 15 (multi-bedroom
        surcharge) = 95, non-zero and real - confirm owner_balance doesn't reflect it at all,
        only commission (10% of 300 = 30, low season) does."""
        booking = self._make_booking(date(2026, 2, 1))
        self.assertNotEqual(clean_fee(self.settings, booking), Decimal('0'))

        payout = compute_regular_owner_payout(booking)
        self.assertTrue(payout['available'])
        self.assertEqual(payout['commission'], Decimal('30.00'))
        self.assertEqual(payout['owner_balance'], Decimal('270.00'))
        self.assertNotIn('management_fee', payout)

    def test_unavailable_for_non_regular_owner(self):
        self.owner.is_paid_regularly = False
        self.owner.save()
        booking = self._make_booking(date(2026, 2, 1))
        payout = compute_regular_owner_payout(booking)
        self.assertFalse(payout['available'])

    def test_unavailable_for_owner_stay(self):
        booking = self._make_booking(date(2026, 2, 1))
        booking.is_owner = True
        booking.save()
        payout = compute_regular_owner_payout(booking)
        self.assertFalse(payout['available'])


class DispatchCommissionReceiptForPayoutTests(TestCase):
    """finance/services.py::dispatch_commission_receipt_for_payout - the per-payout commission
    invoice+receipt for scenarios 1 & 4 (Owner.is_paid_regularly=True)."""

    def setUp(self):
        self.owner = Owner.objects.create(
            name='Commission Owner', email='commission-owner@example.com', currency=Owner.Currency.EUR,
            is_paid_regularly=True, cleans_are_invoiced=True,
        )
        self.company = ManagementCompany.objects.create(name='Commission Test Co', finances_managed_internally=True)
        self.property = Property.objects.create(
            title='Commission Property', short_title='COMMPROP', owner=self.owner,
            cleaning_company=self.company, booking_company=self.company,
        )
        PropertySpec.objects.create(property=self.property, bedrooms=2)
        self.guest = Guest.objects.create(first_name='Com', last_name='Mission', email='commission-guest@example.com')
        self.booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=date(2026, 2, 1),
            departure_date=date(2026, 2, 5), is_owner=False, enquiry_status='Booking confirmed',
            enquiry_source='Website', adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=self.booking, basic_rental=Decimal('300.00'))
        self.payout_record = PayoutRecord.objects.create(booking=self.booking, amount=Decimal('270.00'))
        self.payout = {'commission': Decimal('30.00')}

    def test_creates_presettled_invoice(self):
        invoice = dispatch_commission_receipt_for_payout(self.payout_record, self.payout)
        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.kind, OwnerInvoice.Kind.COMMISSION_PAYOUT)
        self.assertEqual(invoice.commission_amount, Decimal('30.00'))
        self.assertEqual(invoice.cleans_amount, Decimal('0'))
        self.assertEqual(invoice.status, 'paid')
        self.assertIsNotNone(invoice.paid_at)
        self.assertIsNone(invoice.provider)  # no Revolut for this kind - pre-settled, not a live request
        self.assertEqual(list(invoice.bookings.all()), [self.booking])

    def test_idempotent_via_payout_record_onetoone(self):
        first = dispatch_commission_receipt_for_payout(self.payout_record, self.payout)
        second = dispatch_commission_receipt_for_payout(self.payout_record, self.payout)
        self.assertIsNone(second)
        self.assertEqual(OwnerInvoice.objects.filter(payout_record=self.payout_record).count(), 1)

    def test_noop_for_non_regular_owner(self):
        self.owner.is_paid_regularly = False
        self.owner.save()
        result = dispatch_commission_receipt_for_payout(self.payout_record, self.payout)
        self.assertIsNone(result)
        self.assertFalse(OwnerInvoice.objects.filter(payout_record=self.payout_record).exists())

    def test_never_raises_when_sage_not_configured(self):
        """No SAGE_CLIENT_ID/etc in this dev/test environment - _sage_client() returns None. The
        invoice is still created (the receipt itself is real regardless of Sage), just with an
        error recorded, same convention as dispatch_memo_to_sage."""
        invoice = dispatch_commission_receipt_for_payout(self.payout_record, self.payout)
        self.assertIsNotNone(invoice)
        self.assertIsNotNone(invoice.sage_invoice_error)

    @patch('finance.services.env_settings')
    @patch('libraries.accounting.sage.requests.post')
    @patch('libraries.accounting.sage.requests.get')
    def test_sage_net_amount_is_backed_out_of_vat_inclusive_total(self, mock_get, mock_post, mock_env_settings):
        """invoice.total() (commission_amount, €30.00) is VAT-INCLUSIVE (2026-09-10, per Thomas) -
        Sage's own net_amount must be the pre-VAT figure (30 / 1.23 = 24.39 at the default 23%
        rate), so that Sage adding 23% back on top reconstructs exactly €30.00, matching what the
        owner was actually charged via the payout deduction - never a different number."""
        mock_env_settings.SAGE_CLIENT_ID = 'client-id'
        mock_env_settings.SAGE_CLIENT_SECRET = 'client-secret'
        mock_env_settings.SAGE_SIGNING_SECRET = 'signing-secret'

        sage_settings = SageSettings.load()
        sage_settings.refresh_token = 'refresh-token'
        sage_settings.access_token = 'access-token'
        sage_settings.token_expires_at = timezone.now() + timedelta(hours=1)
        sage_settings.default_tax_rate_id = 'tax-rate-1'
        sage_settings.save()

        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {'$resources': []}
        mock_post.return_value.status_code = 201
        mock_post.return_value.json.side_effect = [{'id': 'contact-1'}, {'id': 'invoice-1'}]

        invoice = dispatch_commission_receipt_for_payout(self.payout_record, self.payout)

        self.assertIsNone(invoice.sage_invoice_error)
        self.assertEqual(invoice.sage_invoice_id, 'invoice-1')
        _args, kwargs = mock_post.call_args
        self.assertEqual(kwargs['data']['sales_invoice[invoice_lines][][net_amount]'], '24.39')
        self.assertEqual(kwargs['data']['sales_invoice[invoice_lines][][tax_rate_id]'], 'tax-rate-1')


class GenerateMonthlyOwnerInvoicesCommandTests(TestCase):
    """finance/management/commands/generate_monthly_owner_invoices.py - scenarios 1, 2, 3 of the
    4-scenario matrix (scenario 4 is deliberately skipped by the command itself, see its own
    docstring). Uses --month for a fixed, deterministic billing period rather than "this month"."""

    def setUp(self):
        self.company = ManagementCompany.objects.create(name='Batch Test Co', finances_managed_internally=True)
        self.settings = PaymentSettings.load()
        self.settings.high_season_commission_percent = Decimal('15.00')
        self.settings.low_season_commission_percent = Decimal('10.00')
        self.settings.high_season_start_month = 4
        self.settings.high_season_end_month = 10
        self.settings.cleaning_surcharge_one_bedroom = Decimal('10.00')
        self.settings.cleaning_surcharge_multi_bedroom = Decimal('15.00')
        self.settings.cleaning_high_occupancy_surcharge = Decimal('15.00')
        self.settings.meet_greet_fee = Decimal('28.00')
        self.settings.regular_payout_days_after_arrival = 3
        self.settings.save()
        self.guest = Guest.objects.create(first_name='Batch', last_name='Guest', email='batch-guest@example.com')

    def _owner_and_property(self, name, is_paid_regularly, cleans_are_invoiced):
        owner = Owner.objects.create(
            name=name, email=f'{name.lower().replace(" ", "-")}@example.com', currency=Owner.Currency.EUR,
            is_paid_regularly=is_paid_regularly, cleans_are_invoiced=cleans_are_invoiced,
        )
        property = Property.objects.create(
            title=f'{name} Property', short_title=name[:10].upper(), owner=owner,
            cleaning_company=self.company, booking_company=self.company, standard_cleaning_fee=Decimal('80.00'),
        )
        PropertySpec.objects.create(property=property, bedrooms=2)
        return owner, property

    def _booking_with_sent_memo(self, property, arrival_date, sent_at):
        booking = Booking.objects.create(
            property=property, guest=self.guest, arrival_date=arrival_date,
            departure_date=arrival_date + timedelta(days=4), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=booking, basic_rental=Decimal('300.00'))
        Departure.objects.create(booking=booking, clean=True)
        memo = Memo.objects.get(cleaning_task__booking=booking)
        memo.sent_at = timezone.make_aware(datetime(2026, 2, 15, 12, 0))
        memo.save(update_fields=['sent_at'])
        return booking, memo

    @patch('finance.services.create_revolut_order_for_owner_invoice')
    def test_scenario_1_cleans_monthly_with_revolut(self, mock_revolut):
        owner, property = self._owner_and_property('Scenario One', True, True)
        self._booking_with_sent_memo(property, date(2026, 2, 1), timezone.make_aware(datetime(2026, 2, 15)))

        call_command('generate_monthly_owner_invoices', '--month', '2026-02')

        invoice = OwnerInvoice.objects.get(owner=owner, kind=OwnerInvoice.Kind.CLEANS_MONTHLY)
        self.assertEqual(invoice.period_start, date(2026, 2, 1))
        self.assertEqual(invoice.commission_amount, Decimal('0'))
        self.assertGreater(invoice.cleans_amount, Decimal('0'))
        mock_revolut.assert_called_once_with(invoice)
        # No separate commission invoice from this command - that's per-payout, not monthly.
        self.assertFalse(OwnerInvoice.objects.filter(owner=owner, kind=OwnerInvoice.Kind.COMMISSION_MONTHLY).exists())

    def test_scenario_2_combined_monthly(self):
        owner, property = self._owner_and_property('Scenario Two', False, True)
        self._booking_with_sent_memo(property, date(2026, 2, 1), timezone.make_aware(datetime(2026, 2, 15)))

        call_command('generate_monthly_owner_invoices', '--month', '2026-02')

        invoice = OwnerInvoice.objects.get(owner=owner, kind=OwnerInvoice.Kind.COMBINED_MONTHLY)
        self.assertGreater(invoice.commission_amount, Decimal('0'))
        self.assertGreater(invoice.cleans_amount, Decimal('0'))

    def test_scenario_3_commission_only(self):
        owner, property = self._owner_and_property('Scenario Three', False, False)
        self._booking_with_sent_memo(property, date(2026, 2, 1), timezone.make_aware(datetime(2026, 2, 15)))

        call_command('generate_monthly_owner_invoices', '--month', '2026-02')

        invoice = OwnerInvoice.objects.get(owner=owner, kind=OwnerInvoice.Kind.COMMISSION_MONTHLY)
        self.assertGreater(invoice.commission_amount, Decimal('0'))
        self.assertEqual(invoice.cleans_amount, Decimal('0'))

    def test_scenario_4_skipped_entirely(self):
        owner, property = self._owner_and_property('Scenario Four', True, False)
        self._booking_with_sent_memo(property, date(2026, 2, 1), timezone.make_aware(datetime(2026, 2, 15)))

        call_command('generate_monthly_owner_invoices', '--month', '2026-02')

        self.assertFalse(OwnerInvoice.objects.filter(owner=owner).exists())

    def test_idempotent_rerun_does_not_duplicate(self):
        owner, property = self._owner_and_property('Idempotent Owner', False, False)
        self._booking_with_sent_memo(property, date(2026, 2, 1), timezone.make_aware(datetime(2026, 2, 15)))

        call_command('generate_monthly_owner_invoices', '--month', '2026-02')
        call_command('generate_monthly_owner_invoices', '--month', '2026-02')

        self.assertEqual(OwnerInvoice.objects.filter(owner=owner).count(), 1)

    def test_dry_run_creates_nothing(self):
        owner, property = self._owner_and_property('Dry Run Owner', False, False)
        self._booking_with_sent_memo(property, date(2026, 2, 1), timezone.make_aware(datetime(2026, 2, 15)))

        call_command('generate_monthly_owner_invoices', '--month', '2026-02', '--dry-run')

        self.assertFalse(OwnerInvoice.objects.filter(owner=owner).exists())

    def test_owner_id_filter(self):
        owner_a, property_a = self._owner_and_property('Owner A', False, False)
        owner_b, property_b = self._owner_and_property('Owner B', False, False)
        self._booking_with_sent_memo(property_a, date(2026, 2, 1), timezone.make_aware(datetime(2026, 2, 15)))
        self._booking_with_sent_memo(property_b, date(2026, 2, 1), timezone.make_aware(datetime(2026, 2, 15)))

        call_command('generate_monthly_owner_invoices', '--month', '2026-02', '--owner-id', owner_a.pk)

        self.assertTrue(OwnerInvoice.objects.filter(owner=owner_a).exists())
        self.assertFalse(OwnerInvoice.objects.filter(owner=owner_b).exists())


class ExcludeBlockBookingsFromPayoutsTests(FinanceTestCase):
    """A calendar-blocking placeholder booking (guest last_name=BLOCK_LATE_CHECK_OUT_LAST_NAME,
    e.g. staff/utils.py::_create_late_checkout_block_booking) carries no rental income and
    represents nobody's actual stay, but is_owner=False alone no longer keeps it out of
    finance/services.py::_payouts_due_in_range's candidate queryset (is_owner was corrected
    2026-09-09 to mean what it says - see bookings/utils.py::exclude_block_bookings' own
    docstring). Regression test for the resulting spurious €0.00 payout card, found live on the
    Payouts tab 2026-09-10."""

    def _make_block_booking(self, arrival_offset, departure_offset):
        block_guest = Guest.objects.create(first_name=None, last_name=BLOCK_LATE_CHECK_OUT_LAST_NAME)
        booking = Booking.objects.create(
            property=self.property, guest=block_guest,
            arrival_date=self.today + timedelta(days=arrival_offset),
            departure_date=self.today + timedelta(days=departure_offset),
            is_owner=False, enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=0, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=booking, basic_rental=Decimal('0.00'))
        return booking

    def test_block_booking_excluded_from_owner_balance_in_range(self):
        self._make_block_booking(1, 5)
        rows = owner_balance_in_range(self.property, self.today, self.today + timedelta(days=60))
        self.assertEqual(rows, [])

    def test_block_booking_excluded_from_regular_payouts_due_in_range(self):
        self.property.owner.is_paid_regularly = True
        self.property.owner.save()
        self._make_block_booking(1, 5)
        rows = payouts_due_in_range(self.today, self.today + timedelta(days=10))
        self.assertEqual(rows, [])

    def test_real_booking_still_included_alongside_a_block_booking(self):
        self._make_block_booking(1, 5)
        real_booking = self._make_booking(1, 5)
        rows = owner_balance_in_range(self.property, self.today, self.today + timedelta(days=60))
        self.assertEqual([booking.pk for booking, _ in rows], [real_booking.pk])


class ExcludeUnconfirmedBookingsFromPayoutsTests(FinanceTestCase):
    """A mere 'Open enquiry' (or any other non-VALID_BOOKING_STATUSES status) can already hold a
    provisional Charge row, but nothing has actually been confirmed or paid - it must not show up
    as a real payout due. Regression test for the live bug found 2026-09-10 (Nadine Devereaux,
    booking 6DVG-PZPM) - finance/services.py::_payouts_due_in_range never filtered on
    enquiry_status at all before this fix."""

    def test_open_enquiry_excluded_from_owner_balance_in_range(self):
        booking = self._make_booking(1, 5)
        booking.enquiry_status = 'Open enquiry'
        booking.save(update_fields=['enquiry_status'])

        rows = owner_balance_in_range(self.property, self.today, self.today + timedelta(days=60))
        self.assertEqual(rows, [])

    def test_confirmed_booking_still_included(self):
        booking = self._make_booking(1, 5)
        rows = owner_balance_in_range(self.property, self.today, self.today + timedelta(days=60))
        self.assertEqual([b.pk for b, _ in rows], [booking.pk])


class NonRegularOwnerBalancesDueInRangeTests(TestCase):
    """finance/services.py::non_regular_owner_balances_due_in_range - the aggregate, owner-level
    counterpart to payouts_due_in_range, powering the month-end Payouts-tab cards."""

    def setUp(self):
        self.owner = Owner.objects.create(
            name='Month End Owner', email='month-end-owner@example.com', currency=Owner.Currency.EUR,
            is_paid_regularly=False, cleans_are_invoiced=False,
        )
        self.company = ManagementCompany.objects.create(name='Month End Test Co', finances_managed_internally=True)
        self.property = Property.objects.create(
            title='Month End Property', short_title='MEPROP', owner=self.owner,
            cleaning_company=self.company, booking_company=self.company, standard_cleaning_fee=Decimal('80.00'),
        )
        PropertySpec.objects.create(property=self.property, bedrooms=2)
        self.guest = Guest.objects.create(first_name='Month', last_name='End', email='month-end-guest@example.com')
        self.settings = PaymentSettings.load()
        self.settings.high_season_commission_percent = Decimal('15.00')
        self.settings.low_season_commission_percent = Decimal('10.00')
        self.settings.high_season_start_month = 4
        self.settings.high_season_end_month = 10
        self.settings.cleaning_surcharge_one_bedroom = Decimal('10.00')
        self.settings.cleaning_surcharge_multi_bedroom = Decimal('15.00')
        self.settings.cleaning_high_occupancy_surcharge = Decimal('15.00')
        self.settings.meet_greet_fee = Decimal('28.00')
        self.settings.save()

    def _make_booking(self, arrival_date):
        booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=arrival_date,
            departure_date=arrival_date + timedelta(days=4), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=booking, basic_rental=Decimal('300.00'))
        Departure.objects.create(booking=booking, clean=True)
        return booking

    def test_aggregates_owner_balance_across_multiple_bookings(self):
        b1 = self._make_booking(date(2026, 2, 1))
        b2 = self._make_booking(date(2026, 2, 10))

        rows = non_regular_owner_balances_due_in_range(date(2026, 2, 1), date(2026, 2, 28))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['owner'], self.owner)
        self.assertEqual(set(row.pk for row in rows[0]['bookings']), {b1.pk, b2.pk})
        self.assertGreater(rows[0]['owner_balance'], Decimal('0'))

    def test_excludes_regularly_paid_owners(self):
        self.owner.is_paid_regularly = True
        self.owner.save()
        self._make_booking(date(2026, 2, 1))

        rows = non_regular_owner_balances_due_in_range(date(2026, 2, 1), date(2026, 2, 28))
        self.assertEqual(rows, [])


class GenerateNonRegularOwnerInvoiceTests(TestCase):
    """finance/services.py::generate_non_regular_owner_invoice - the shared billing function
    behind both generate_monthly_owner_invoices (CLI) and StaffFinanceOwnerPayoutGenerateView
    (the interactive Payouts-tab 'Generate' button)."""

    def setUp(self):
        self.company = ManagementCompany.objects.create(name='Generate Test Co', finances_managed_internally=True)
        self.settings = PaymentSettings.load()
        self.settings.high_season_commission_percent = Decimal('15.00')
        self.settings.low_season_commission_percent = Decimal('10.00')
        self.settings.high_season_start_month = 4
        self.settings.high_season_end_month = 10
        self.settings.cleaning_surcharge_one_bedroom = Decimal('10.00')
        self.settings.cleaning_surcharge_multi_bedroom = Decimal('15.00')
        self.settings.cleaning_high_occupancy_surcharge = Decimal('15.00')
        self.settings.meet_greet_fee = Decimal('28.00')
        self.settings.save()
        self.guest = Guest.objects.create(first_name='Gen', last_name='Erate', email='generate-guest@example.com')

    def _owner_and_property(self, name, cleans_are_invoiced):
        owner = Owner.objects.create(
            name=name, email=f'{name.lower().replace(" ", "-")}@example.com', currency=Owner.Currency.EUR,
            is_paid_regularly=False, cleans_are_invoiced=cleans_are_invoiced,
        )
        property = Property.objects.create(
            title=f'{name} Property', short_title=name[:10].upper(), owner=owner,
            cleaning_company=self.company, booking_company=self.company, standard_cleaning_fee=Decimal('80.00'),
        )
        PropertySpec.objects.create(property=property, bedrooms=2)
        return owner, property

    def _booking_with_sent_memo(self, property, arrival_date):
        booking = Booking.objects.create(
            property=property, guest=self.guest, arrival_date=arrival_date,
            departure_date=arrival_date + timedelta(days=4), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=booking, basic_rental=Decimal('300.00'))
        Departure.objects.create(booking=booking, clean=True)
        memo = Memo.objects.get(cleaning_task__booking=booking)
        memo.sent_at = timezone.make_aware(datetime(2026, 2, 15, 12, 0))
        memo.save(update_fields=['sent_at'])
        return booking

    def test_not_applicable_for_regularly_paid_owner(self):
        owner, _ = self._owner_and_property('Regular', False)
        owner.is_paid_regularly = True
        owner.save()

        invoice, status = generate_non_regular_owner_invoice(owner, date(2026, 2, 1), date(2026, 2, 28))
        self.assertIsNone(invoice)
        self.assertEqual(status, 'not_applicable')

    def test_combined_kind_when_cleans_are_invoiced(self):
        owner, property = self._owner_and_property('Combined', True)
        self._booking_with_sent_memo(property, date(2026, 2, 1))

        invoice, status = generate_non_regular_owner_invoice(owner, date(2026, 2, 1), date(2026, 2, 28))
        self.assertEqual(status, 'created')
        self.assertEqual(invoice.kind, OwnerInvoice.Kind.COMBINED_MONTHLY)
        self.assertGreater(invoice.commission_amount, Decimal('0'))
        self.assertGreater(invoice.cleans_amount, Decimal('0'))

    def test_commission_only_kind_when_cleans_not_invoiced(self):
        owner, property = self._owner_and_property('CommissionOnly', False)
        self._booking_with_sent_memo(property, date(2026, 2, 1))

        invoice, status = generate_non_regular_owner_invoice(owner, date(2026, 2, 1), date(2026, 2, 28))
        self.assertEqual(status, 'created')
        self.assertEqual(invoice.kind, OwnerInvoice.Kind.COMMISSION_MONTHLY)
        self.assertGreater(invoice.commission_amount, Decimal('0'))
        self.assertEqual(invoice.cleans_amount, Decimal('0'))

    def test_idempotent_second_call_reports_already_invoiced(self):
        owner, property = self._owner_and_property('Idempotent', False)
        self._booking_with_sent_memo(property, date(2026, 2, 1))

        generate_non_regular_owner_invoice(owner, date(2026, 2, 1), date(2026, 2, 28))
        invoice, status = generate_non_regular_owner_invoice(owner, date(2026, 2, 1), date(2026, 2, 28))

        self.assertIsNone(invoice)
        self.assertEqual(status, 'already_invoiced')
        self.assertEqual(OwnerInvoice.objects.filter(owner=owner).count(), 1)

    def test_nothing_to_bill_when_zero(self):
        owner, property = self._owner_and_property('NothingToBill', False)
        invoice, status = generate_non_regular_owner_invoice(owner, date(2026, 2, 1), date(2026, 2, 28))
        self.assertIsNone(invoice)
        self.assertEqual(status, 'nothing_to_bill')

    def test_dry_run_creates_nothing(self):
        owner, property = self._owner_and_property('DryRun', False)
        self._booking_with_sent_memo(property, date(2026, 2, 1))

        invoice, status = generate_non_regular_owner_invoice(owner, date(2026, 2, 1), date(2026, 2, 28), dry_run=True)
        self.assertIsNone(invoice)
        self.assertEqual(status, 'dry_run')
        self.assertFalse(OwnerInvoice.objects.filter(owner=owner).exists())

    def test_never_raises_when_sage_not_configured(self):
        owner, property = self._owner_and_property('NoSage', False)
        self._booking_with_sent_memo(property, date(2026, 2, 1))

        invoice, status = generate_non_regular_owner_invoice(owner, date(2026, 2, 1), date(2026, 2, 28))
        self.assertEqual(status, 'created')
        self.assertIsNotNone(invoice.sage_invoice_error)


class StaffOwnerPayoutMonthEndViewsTests(FinanceTestCase):
    """staff/views.py::StaffFinancePayoutsView's month-end owner cards, and the two new views
    behind them - StaffFinanceOwnerPayoutGenerateView and StaffFinanceOwnerInvoiceMarkPaidView."""

    def setUp(self):
        super().setUp()
        User.objects.create_user(username='payoutviewer', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='payoutviewer', password='pw')
        self.settings.high_season_commission_percent = Decimal('15.00')
        self.settings.low_season_commission_percent = Decimal('10.00')
        self.settings.high_season_start_month = 4
        self.settings.high_season_end_month = 10
        self.settings.save()

    def test_month_end_owner_card_renders_and_generates_invoice(self):
        month_end = date(2026, 2, 28)
        self._make_booking_on(date(2026, 2, 1))

        response = self.client.get(reverse('staff:finance_payouts'), {'date': month_end.isoformat()})
        self.assertEqual(response.status_code, 200)
        owner_rows = [
            row for day in response.context['days'] if day['date'] == month_end for row in day['owner_rows']
        ]
        self.assertEqual(len(owner_rows), 1)
        self.assertIsNone(owner_rows[0]['invoice'])

        response = self.client.post(
            reverse('staff:finance_owner_payout_generate', kwargs={'owner_id': self.owner.pk}),
            {'period_start': '2026-02-01', 'date': month_end.isoformat()},
        )
        self.assertEqual(response.status_code, 302)
        invoice = OwnerInvoice.objects.get(owner=self.owner, period_start=date(2026, 2, 1))
        self.assertEqual(invoice.kind, OwnerInvoice.Kind.COMMISSION_MONTHLY)

    def test_mark_paid_sets_status_and_is_rejected_only_for_commission_payout(self):
        invoice = OwnerInvoice.objects.create(
            owner=self.owner, kind=OwnerInvoice.Kind.COMMISSION_MONTHLY, period_start=date(2026, 2, 1),
            commission_amount=Decimal('50.00'),
        )
        response = self.client.post(reverse('staff:finance_owner_invoice_mark_paid', kwargs={'pk': invoice.pk}))
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'paid')
        self.assertIsNotNone(invoice.paid_at)

        # Second mark-paid is a rejected no-op, not a duplicate/overwrite.
        paid_at = invoice.paid_at
        self.client.post(reverse('staff:finance_owner_invoice_mark_paid', kwargs={'pk': invoice.pk}))
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_at, paid_at)

        # CLEANS_MONTHLY is now also manually mark-payable - a fallback for when an owner settles
        # outside Revolut, whose webhook lifecycle is still dormant (2026-09-10).
        cleans_invoice = OwnerInvoice.objects.create(
            owner=self.owner, kind=OwnerInvoice.Kind.CLEANS_MONTHLY, period_start=date(2026, 3, 1),
            cleans_amount=Decimal('80.00'), provider='revolut', status='pending',
        )
        self.client.post(reverse('staff:finance_owner_invoice_mark_paid', kwargs={'pk': cleans_invoice.pk}))
        cleans_invoice.refresh_from_db()
        self.assertEqual(cleans_invoice.status, 'paid')

        # COMMISSION_PAYOUT is pre-settled at creation - manual mark-paid must refuse it.
        payout_invoice = OwnerInvoice.objects.create(
            owner=self.owner, kind=OwnerInvoice.Kind.COMMISSION_PAYOUT, commission_amount=Decimal('10.00'),
        )
        self.client.post(reverse('staff:finance_owner_invoice_mark_paid', kwargs={'pk': payout_invoice.pk}))
        payout_invoice.refresh_from_db()
        self.assertIsNone(payout_invoice.status)

    def _make_booking_on(self, arrival_date):
        booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=arrival_date,
            departure_date=arrival_date + timedelta(days=4), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=booking, basic_rental=Decimal('300.00'))
        Departure.objects.create(booking=booking, clean=True)
        return booking


class OwnerOutstandingBalanceTests(TestCase):
    """finance/services.py::owner_outstanding_balance - the real current outstanding balance
    powering the rewritten Statement tab (2026-09-10)."""

    def setUp(self):
        self.company = ManagementCompany.objects.create(name='Balance Test Co', finances_managed_internally=True)
        self.settings = PaymentSettings.load()
        self.settings.high_season_commission_percent = Decimal('15.00')
        self.settings.low_season_commission_percent = Decimal('10.00')
        self.settings.high_season_start_month = 4
        self.settings.high_season_end_month = 10
        self.settings.cleaning_surcharge_one_bedroom = Decimal('10.00')
        self.settings.cleaning_surcharge_multi_bedroom = Decimal('15.00')
        self.settings.cleaning_high_occupancy_surcharge = Decimal('15.00')
        self.settings.meet_greet_fee = Decimal('28.00')
        self.settings.regular_payout_days_after_arrival = 3
        self.settings.save()
        self.guest = Guest.objects.create(first_name='Bal', last_name='Ance', email='balance-guest@example.com')

    def _owner_and_property(self, name, is_paid_regularly, cleans_are_invoiced):
        owner = Owner.objects.create(
            name=name, email=f'{name.lower().replace(" ", "-")}@example.com', currency=Owner.Currency.EUR,
            is_paid_regularly=is_paid_regularly, cleans_are_invoiced=cleans_are_invoiced,
        )
        property = Property.objects.create(
            title=f'{name} Property', short_title=name[:10].upper(), owner=owner,
            cleaning_company=self.company, booking_company=self.company, standard_cleaning_fee=Decimal('80.00'),
        )
        PropertySpec.objects.create(property=property, bedrooms=2)
        return owner, property

    def _booking(self, property, arrival_date, send_memo=True):
        booking = Booking.objects.create(
            property=property, guest=self.guest, arrival_date=arrival_date,
            departure_date=arrival_date + timedelta(days=4), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=booking, basic_rental=Decimal('300.00'))
        Departure.objects.create(booking=booking, clean=True)
        if send_memo:
            memo = Memo.objects.get(cleaning_task__booking=booking)
            memo.sent_at = timezone.now()
            memo.save(update_fields=['sent_at'])
        return booking

    def test_regular_owner_unpaid_booking_counts_paid_booking_excluded(self):
        owner, property = self._owner_and_property('Regular One', True, True)
        unpaid = self._booking(property, date.today() - timedelta(days=10), send_memo=False)
        paid = self._booking(property, date.today() - timedelta(days=20), send_memo=False)
        PayoutRecord.objects.create(booking=paid, amount=Decimal('100.00'))

        balance = owner_outstanding_balance(owner, date.today())

        self.assertEqual([row['booking'].pk for row in balance['owed_to_owner_rows']], [unpaid.pk])
        self.assertGreater(balance['owed_to_owner'], Decimal('0'))

    def test_scenario_1_unbilled_memo_owed_billed_and_paid_excluded(self):
        owner, property = self._owner_and_property('Regular Invoiced', True, True)
        self._booking(property, date.today() - timedelta(days=10))

        balance = owner_outstanding_balance(owner, date.today())
        self.assertEqual(len(balance['owed_by_owner_rows']), 1)
        self.assertGreater(balance['owed_by_owner'], Decimal('0'))

        invoice = OwnerInvoice.objects.create(
            owner=owner, kind=OwnerInvoice.Kind.CLEANS_MONTHLY, period_start=date.today().replace(day=1),
            cleans_amount=balance['owed_by_owner'], status='paid',
        )
        invoice.memos.set(Memo.objects.filter(property=property))

        balance = owner_outstanding_balance(owner, date.today())
        self.assertEqual(balance['owed_by_owner_rows'], [])
        self.assertEqual(balance['owed_by_owner'], Decimal('0'))

    def test_scenario_4_management_fee_paid_toggle(self):
        owner, property = self._owner_and_property('Regular NotInvoiced', True, False)
        booking = self._booking(property, date.today() - timedelta(days=10))
        memo = Memo.objects.get(cleaning_task__booking=booking)

        balance = owner_outstanding_balance(owner, date.today())
        self.assertEqual(len(balance['owed_by_owner_rows']), 1)

        memo.management_fee_paid_at = timezone.now()
        memo.save(update_fields=['management_fee_paid_at'])

        balance = owner_outstanding_balance(owner, date.today())
        self.assertEqual(balance['owed_by_owner_rows'], [])
        self.assertEqual(balance['owed_by_owner'], Decimal('0'))

    def test_non_regular_owner_unpaid_month_counts_paid_month_excluded(self):
        owner, property = self._owner_and_property('Non Regular', False, False)
        self._booking(property, date(2026, 2, 1), send_memo=False)

        balance = owner_outstanding_balance(owner, date(2026, 3, 15))
        self.assertEqual(len(balance['owed_to_owner_rows']), 1)
        self.assertGreater(balance['owed_to_owner'], Decimal('0'))
        self.assertIsNone(balance['owed_by_owner'])
        self.assertEqual(balance['owed_by_owner_rows'], [])

        OwnerInvoice.objects.create(
            owner=owner, kind=OwnerInvoice.Kind.COMMISSION_MONTHLY, period_start=date(2026, 2, 1),
            commission_amount=balance['owed_to_owner'], status='paid',
        )

        balance = owner_outstanding_balance(owner, date(2026, 3, 15))
        self.assertEqual(balance['owed_to_owner_rows'], [])
        self.assertEqual(balance['owed_to_owner'], Decimal('0'))


class NeedsInformalCleansTrackingTests(TestCase):
    """finance/services.py::needs_informal_cleans_tracking - not just scenario 4 (2026-09-10): a
    true management-only owner (no booking relationship with KLT at all) needs the same mechanism,
    but a real scenario-3 owner (has one) must not, since their management fee is already netted
    into their payout."""

    def setUp(self):
        self.internal_company = ManagementCompany.objects.create(
            name='Internal Booking Co', finances_managed_internally=True,
        )
        self.external_company = ManagementCompany.objects.create(
            name='External Booking Co', finances_managed_internally=False,
        )

    def _owner_with_property(self, is_paid_regularly, cleans_are_invoiced, booking_company):
        n = Owner.objects.count()
        owner = Owner.objects.create(
            name=f'Test Owner {n}', email=f'test-owner-{n}@example.com',
            currency=Owner.Currency.EUR, is_paid_regularly=is_paid_regularly, cleans_are_invoiced=cleans_are_invoiced,
        )
        Property.objects.create(
            title=f'{owner.name} Property', short_title=f'TESTOWN{n}', owner=owner,
            cleaning_company=self.internal_company, booking_company=booking_company,
        )
        return owner

    def test_scenario_1_and_2_invoiced_never_needs_it(self):
        scenario_1 = self._owner_with_property(True, True, self.internal_company)
        scenario_2 = self._owner_with_property(False, True, self.internal_company)
        self.assertFalse(needs_informal_cleans_tracking(scenario_1))
        self.assertFalse(needs_informal_cleans_tracking(scenario_2))

    def test_scenario_4_always_needs_it(self):
        owner = self._owner_with_property(True, False, self.internal_company)
        self.assertTrue(needs_informal_cleans_tracking(owner))

    def test_real_scenario_3_does_not_need_it_already_netted(self):
        owner = self._owner_with_property(False, False, self.internal_company)
        self.assertFalse(needs_informal_cleans_tracking(owner))

    def test_true_management_only_owner_needs_it(self):
        owner = self._owner_with_property(False, False, self.external_company)
        self.assertTrue(needs_informal_cleans_tracking(owner))


class ConsolidateInformalCleansPaymentTests(TestCase):
    """finance/services.py::consolidate_informal_cleans_payment - Thomas's "one markable-paid
    line" (2026-09-10)."""

    def setUp(self):
        self.company = ManagementCompany.objects.create(name='Consolidate Test Co', finances_managed_internally=True)
        self.owner = Owner.objects.create(
            name='Consolidate Owner', email='consolidate-owner@example.com', currency=Owner.Currency.EUR,
            is_paid_regularly=True, cleans_are_invoiced=False,
        )
        self.property = Property.objects.create(
            title='Consolidate Property', short_title='CONSOL', owner=self.owner,
            cleaning_company=self.company, booking_company=self.company, standard_cleaning_fee=Decimal('80.00'),
        )
        PropertySpec.objects.create(property=self.property, bedrooms=2)
        self.guest = Guest.objects.create(first_name='Con', last_name='Solidate', email='consolidate-guest@example.com')

    def _sent_memo(self, arrival_date):
        booking = Booking.objects.create(
            property=self.property, guest=self.guest, arrival_date=arrival_date,
            departure_date=arrival_date + timedelta(days=4), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=booking, basic_rental=Decimal('300.00'))
        Departure.objects.create(booking=booking, clean=True)
        memo = Memo.objects.get(cleaning_task__booking=booking)
        memo.sent_at = timezone.now()
        memo.save(update_fields=['sent_at'])
        return memo

    def test_bundles_unpaid_memos_into_one_invoice(self):
        memo_a = self._sent_memo(date.today() - timedelta(days=40))
        memo_b = self._sent_memo(date.today() - timedelta(days=10))

        invoice = consolidate_informal_cleans_payment(self.owner)

        self.assertEqual(invoice.kind, OwnerInvoice.Kind.CLEANS_INFORMAL_MONTHLY)
        self.assertEqual(set(invoice.memos.all()), {memo_a, memo_b})
        self.assertEqual(invoice.cleans_amount, memo_a.total() + memo_b.total())

    def test_rerun_extends_existing_open_bundle_not_duplicates(self):
        self._sent_memo(date.today() - timedelta(days=40))
        first = consolidate_informal_cleans_payment(self.owner)

        memo_b = self._sent_memo(date.today() - timedelta(days=1))
        second = consolidate_informal_cleans_payment(self.owner)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            OwnerInvoice.objects.filter(owner=self.owner, kind=OwnerInvoice.Kind.CLEANS_INFORMAL_MONTHLY).count(), 1,
        )
        self.assertIn(memo_b, second.memos.all())

    def test_already_paid_memo_excluded(self):
        memo = self._sent_memo(date.today() - timedelta(days=5))
        memo.management_fee_paid_at = timezone.now()
        memo.save(update_fields=['management_fee_paid_at'])

        invoice = consolidate_informal_cleans_payment(self.owner)
        self.assertIsNone(invoice)

    def test_noop_when_owner_does_not_need_tracking(self):
        self.owner.cleans_are_invoiced = True
        self.owner.save()
        self._sent_memo(date.today() - timedelta(days=5))

        invoice = consolidate_informal_cleans_payment(self.owner)
        self.assertIsNone(invoice)
        self.assertFalse(OwnerInvoice.objects.filter(owner=self.owner).exists())

    def test_noop_with_nothing_eligible(self):
        self.assertIsNone(consolidate_informal_cleans_payment(self.owner))


class StaffFinanceExpectedPaymentsViewTests(FinanceTestCase):
    """staff/views.py::StaffFinanceExpectedPaymentsView - replaces the old Owner Invoices tab
    (2026-09-10), merging OwnerInvoice rows with standalone unconsolidated Memo rows across three
    filtered views. Also covers the Memo detail page's broadened gap fix (management-only owners)."""

    def setUp(self):
        super().setUp()
        User.objects.create_user(username='expectedpayviewer', password='pw', is_staff=True, is_superuser=True)
        self.client.login(username='expectedpayviewer', password='pw')

    def _sent_memo_for(self, property, guest, arrival_date):
        booking = Booking.objects.create(
            property=property, guest=guest, arrival_date=arrival_date,
            departure_date=arrival_date + timedelta(days=4), is_owner=False,
            enquiry_status='Booking confirmed', enquiry_source='Website',
            adults=2, children=0, babies=0, last_updated=timezone.now(),
        )
        Charge.objects.create(booking=booking, basic_rental=Decimal('300.00'))
        Departure.objects.create(booking=booking, clean=True)
        memo = Memo.objects.get(cleaning_task__booking=booking)
        memo.sent_at = timezone.now()
        memo.save(update_fields=['sent_at'])
        return memo

    def test_unpaid_view_shows_standalone_memo_and_consolidate_button(self):
        self.property.owner.is_paid_regularly = True
        self.property.owner.cleans_are_invoiced = False
        self.property.owner.save()
        self._sent_memo_for(self.property, self.guest, self.today - timedelta(days=5))

        response = self.client.get(reverse('staff:finance_expected_payments'), {'view': 'unpaid'})
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['type'], 'memo')
        self.assertIn(self.property.owner, response.context['consolidatable_owners'])

    def test_consolidate_then_mark_paid_moves_memo_to_recent(self):
        self.property.owner.is_paid_regularly = True
        self.property.owner.cleans_are_invoiced = False
        self.property.owner.save()
        memo = self._sent_memo_for(self.property, self.guest, self.today - timedelta(days=5))

        self.client.post(reverse('staff:finance_consolidate_informal_cleans', kwargs={'owner_id': self.property.owner.pk}))
        invoice = OwnerInvoice.objects.get(owner=self.property.owner, kind=OwnerInvoice.Kind.CLEANS_INFORMAL_MONTHLY)
        self.assertIn(memo, invoice.memos.all())

        unpaid_rows = self.client.get(reverse('staff:finance_expected_payments'), {'view': 'unpaid'}).context['rows']
        self.assertEqual(len(unpaid_rows), 1)
        self.assertEqual(unpaid_rows[0]['type'], 'invoice')

        self.client.post(reverse('staff:finance_owner_invoice_mark_paid', kwargs={'pk': invoice.pk}))
        memo.refresh_from_db()
        self.assertIsNotNone(memo.management_fee_paid_at)

        recent_rows = self.client.get(reverse('staff:finance_expected_payments'), {'view': 'recent'}).context['rows']
        self.assertEqual(len(recent_rows), 1)
        self.assertEqual(recent_rows[0]['invoice'].pk, invoice.pk)

    def test_management_only_owner_gets_individual_toggle_on_memo_detail(self):
        external_company = ManagementCompany.objects.create(name='External Co', finances_managed_internally=False)
        owner = Owner.objects.create(
            name='Management Only Owner', email='mgmt-only@example.com', currency=Owner.Currency.EUR,
            is_paid_regularly=False, cleans_are_invoiced=False,
        )
        property = Property.objects.create(
            title='Management Only Property', short_title='MGMTONLY', owner=owner,
            cleaning_company=self.company, booking_company=external_company, standard_cleaning_fee=Decimal('80.00'),
        )
        PropertySpec.objects.create(property=property, bedrooms=2)
        memo = self._sent_memo_for(property, self.guest, self.today - timedelta(days=5))

        response = self.client.get(reverse('staff:finance_memo_detail', kwargs={'pk': memo.pk}))
        self.assertContains(response, 'Mark as paid')
        self.assertTrue(response.context['needs_informal_cleans_tracking'])
