from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from accountants.permissions import accountant_login_required
from finance.models import Memo, PayoutRecord
from properties.models import Property
from staff.reports import OWNER_SAFE_REPORT_COLUMNS, booking_report_rows, report_totals
from staff.utils import last_day_of_month, parsed_date


class AccountantLoginView(auth_views.LoginView):
    """Accountants Suite login - mirrors owners/views.py::OwnerLoginView exactly (same reasoning
    for not using redirect_authenticated_user=True: this project's auth/session is shared across
    staff/guests/owners/accountants, so an already-authenticated non-accountant landing here must
    just see the login form again, not get caught in a redirect loop)."""
    template_name = 'accountants/login.html'

    def get_success_url(self):
        return self.get_redirect_url() or str(reverse('accountants:home'))

    def form_valid(self, form):
        user = form.get_user()
        if getattr(user, 'accountant_profile', None) is None:
            form.add_error(None, "This account isn't linked to an accountant.")
            return self.form_invalid(form)
        return super().form_valid(form)


class AccountantAcceptInviteView(auth_views.PasswordResetConfirmView):
    """Where a newly-invited accountant lands to choose their own password - mirrors
    owners/views.py::OwnerAcceptInviteView exactly. See staff.views.py::StaffSettingsView.
    _invite_accountant, which creates the account with set_unusable_password() rather than a
    staff-chosen one, then accountants/utils.py::send_accountant_invite_email emails this link."""
    template_name = 'accountants/accept_invite.html'
    success_url = reverse_lazy('accountants:home')

    def form_valid(self, form):
        response = super().form_valid(form)
        login(self.request, form.user, backend='django.contrib.auth.backends.ModelBackend')
        messages.success(self.request, "Your password is set - welcome!")
        return response


def _accountant_properties(accountant):
    return Property.objects.filter(accountant=accountant).select_related('owner', 'location').order_by('owner__name', 'title')


@method_decorator(accountant_login_required, name='dispatch')
class AccountantHomeView(View):
    """Accountants Suite landing page - a welcome + the owners/properties this accounting firm
    looks after, grouped by owner (one accountant can be assigned to several owners' properties
    via Property.accountant, unlike the Owner Suite's single-owner scope)."""
    template_name = 'accountants/home.html'

    def get(self, request, *args, **kwargs):
        accountant = request.user.accountant_profile
        return render(request, self.template_name, {
            'accountant': accountant,
            'properties': _accountant_properties(accountant),
            'active_section': 'home',
        })


@method_decorator(accountant_login_required, name='dispatch')
class AccountantReportView(View):
    """The accountant-facing booking-listing report - same staff.reports.py::booking_report_rows
    row-builder and OWNER_SAFE_REPORT_COLUMNS the Owner Suite's own OwnerReportView uses, scoped
    across every property this accountant looks after (potentially spanning several owners)
    rather than a single owner's properties."""
    template_name = 'accountants/reports.html'
    COLUMNS = OWNER_SAFE_REPORT_COLUMNS

    def get(self, request, *args, **kwargs):
        accountant = request.user.accountant_profile
        properties = list(_accountant_properties(accountant))

        today = timezone.now().date()
        start = parsed_date(request.GET.get('start')) or today.replace(day=1)
        end = parsed_date(request.GET.get('end')) or last_day_of_month(today)
        property_id = request.GET.get('property_id', '')
        selected_property = next((p for p in properties if str(p.pk) == property_id), None)

        if 'columns' in request.GET:
            selected_columns = set(request.GET.getlist('columns'))
        else:
            selected_columns = {key for key, _label in self.COLUMNS}

        rows = booking_report_rows(
            start, end, properties=[selected_property] if selected_property else properties,
        )

        return render(request, self.template_name, {
            'accountant': accountant,
            'properties': properties,
            'property': selected_property,
            'start': start,
            'end': end,
            'columns': self.COLUMNS,
            'selected_columns': selected_columns,
            'rows': rows,
            'totals': report_totals(rows),
            'active_section': 'reports',
        })


@method_decorator(accountant_login_required, name='dispatch')
class AccountantPayoutsMemosView(View):
    """Payouts & Memos - mirrors owners/views.py::OwnerPayoutsMemosView exactly, scoped to
    property__accountant instead of property__owner. See that view's own docstring for why a
    payout/memo row is always exactly one booking, and _payout_detail_url below for why the
    detail link's date range depends on the booking's own owner's is_paid_regularly flag (not a
    single fixed owner, since this view can span several)."""
    template_name = 'accountants/payouts_memos.html'

    def get(self, request, *args, **kwargs):
        accountant = request.user.accountant_profile
        payout_records = PayoutRecord.objects.filter(
            booking__property__accountant=accountant,
        ).select_related('booking', 'booking__property', 'booking__property__owner')
        memos = Memo.objects.filter(
            property__accountant=accountant, sent_at__isnull=False,
        ).select_related('property', 'property__owner', 'cleaning_task__booking')

        rows = []
        for record in payout_records:
            rows.append({
                'type': 'Payout',
                'property': record.booking.property,
                'reference': record.booking.reference,
                'date': record.paid_at,
                'amount': record.amount,
                'is_charge': False,
                'detail_url': self._payout_detail_url(record.booking),
            })
        for memo in memos:
            booking = memo.cleaning_task.booking if memo.cleaning_task else None
            rows.append({
                'type': 'Memo',
                'property': memo.property,
                'reference': booking.reference if booking else '—',
                'date': memo.sent_at,
                'amount': memo.total(),
                'is_charge': True,
                'detail_url': reverse('accountants:memo_detail', kwargs={'pk': memo.pk}),
            })
        rows.sort(key=lambda row: row['date'], reverse=True)

        return render(request, self.template_name, {
            'accountant': accountant,
            'rows': rows,
            'active_section': 'payouts_memos',
        })

    def _payout_detail_url(self, booking):
        owner = booking.property.owner
        if owner is not None and owner.is_paid_regularly:
            start, end = booking.arrival_date, booking.departure_date
        else:
            start = booking.arrival_date.replace(day=1)
            end = last_day_of_month(booking.arrival_date)
        return (
            f"{reverse('accountants:reports')}?property_id={booking.property_id}"
            f"&start={start.isoformat()}&end={end.isoformat()}"
        )


@method_decorator(accountant_login_required, name='dispatch')
class AccountantMemoDetailView(View):
    """Trimmed, accountant-facing mirror of owners/views.py::OwnerMemoDetailView, scoped to
    property__accountant instead of property__owner."""
    template_name = 'accountants/memo_detail.html'

    def get(self, request, pk, *args, **kwargs):
        accountant = request.user.accountant_profile
        memo = Memo.objects.select_related('property', 'cleaning_task__booking', 'sent_by').filter(
            pk=pk, property__accountant=accountant, sent_at__isnull=False,
        ).first()
        if memo is None:
            raise Http404("No memo found.")
        return render(request, self.template_name, {
            'accountant': accountant,
            'memo': memo,
            'ad_hoc_services': memo.ad_hoc_services.order_by('date'),
            'active_section': 'payouts_memos',
        })
