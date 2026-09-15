from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.shortcuts import render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from accountants.permissions import accountant_login_required
from finance.models import OwnerInvoice
from properties.models import Property
from staff.reports import ACCOUNTANT_REPORT_COLUMNS, ZERO, booking_report_rows, report_totals
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
    row-builder the Owner Suite's own OwnerReportView uses, but ACCOUNTANT_REPORT_COLUMNS rather
    than OWNER_SAFE_REPORT_COLUMNS (accountants need to see Commission, unlike owners - see that
    tuple's own comment), scoped across every property this accountant looks after (potentially
    spanning several owners) rather than a single owner's properties.

    Figures on top of booking_report_rows' own output, computed here rather than added to the
    shared row-builder since they're accountant-report-specific, not something staff/owner
    reports need:
      - total_to_be_receipted: basic_rental + platform_fee + platform_fee_vat, the amount the
        accountant actually has to issue a receipt for (2026-09-15, per Thomas, working from his
        own real accountancy spreadsheets).
      - clean_cost/meet_greet_cost/maintenance_cost blanked to None per-row when that row's
        booking's property's owner has cleans_are_invoiced=False - "management fees" (Thomas's
        term for clean+meet&greet+maintenance, NOT rental commission, which is always invoiced
        with no opt-out) shouldn't appear in a report for an owner who never actually gets them
        formally invoiced. Per-row, not per-request, since one accountant can span owners with
        different cleans_are_invoiced settings. Must run before _attach_invoice_total, which reads
        the (possibly now-None) clean/meet-greet/maintenance figures.
      - invoice_total: commission + platform_fee + management fees (clean/meet-greet/maintenance),
        each "if applicable" (None/gated treated as zero) - replaces Owner Net Revenue in the
        accountant report (2026-09-15, per Thomas - owners' own net revenue isn't what an
        accountant needs; what KLT actually invoices the owner is).
      - invoice: whichever OwnerInvoice (if any) this row's booking is linked to via the
        `bookings` M2M - covers COMMISSION_PAYOUT/COMMISSION_MONTHLY/COMBINED_MONTHLY kinds (the
        ones that actually populate `bookings`; see finance/services.py). Renders as Invoice N°
        via invoice.sage_invoice_id."""
    template_name = 'accountants/reports.html'
    COLUMNS = ACCOUNTANT_REPORT_COLUMNS

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
        self._apply_management_fee_gate(rows)
        self._attach_total_to_be_receipted(rows)
        self._attach_invoice_total(rows)
        self._attach_invoices(rows)

        totals = report_totals(rows)
        for key in ('total_to_be_receipted', 'invoice_total'):
            values = [row[key] for row in rows if row[key] is not None]
            totals[key] = sum(values, ZERO) if values else None

        return render(request, self.template_name, {
            'accountant': accountant,
            'properties': properties,
            'property': selected_property,
            'start': start,
            'end': end,
            'columns': self.COLUMNS,
            'selected_columns': selected_columns,
            'rows': rows,
            'totals': totals,
            'active_section': 'reports',
        })

    def _apply_management_fee_gate(self, rows):
        for row in rows:
            owner = row['booking'].property.owner
            if owner is None or not owner.cleans_are_invoiced:
                row['clean_cost'] = None
                row['meet_greet_cost'] = None
                row['maintenance_cost'] = None

    def _attach_total_to_be_receipted(self, rows):
        for row in rows:
            if row['basic_rental'] is None:
                row['total_to_be_receipted'] = None
            else:
                row['total_to_be_receipted'] = (
                    row['basic_rental'] + (row['platform_fee'] or ZERO) + (row['platform_fee_vat'] or ZERO)
                )

    def _attach_invoice_total(self, rows):
        for row in rows:
            if row['commission'] is None:
                row['invoice_total'] = None
            else:
                row['invoice_total'] = (
                    row['commission'] + (row['platform_fee'] or ZERO)
                    + (row['clean_cost'] or ZERO) + (row['meet_greet_cost'] or ZERO) + (row['maintenance_cost'] or ZERO)
                )

    def _attach_invoices(self, rows):
        bookings = [row['booking'] for row in rows]
        invoices_by_booking_id = {}
        for invoice in OwnerInvoice.objects.filter(bookings__in=bookings).prefetch_related('bookings'):
            for booking in invoice.bookings.all():
                invoices_by_booking_id[booking.id] = invoice
        for row in rows:
            row['invoice'] = invoices_by_booking_id.get(row['booking'].id)
