from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from availability.utils import get_property_calendar
from bookings.models import CURRENCY_CHOICES, PAYMENT_STATUS_CHOICES, Booking
from bookings.utils import extras_summary
from guests.models import Guest
from properties.models import Property
from staff.models import Deduction, OwnerPayment, TaskHistoryEntry
from staff.utils import (
    CLOSED_STATUSES, GUEST_LETTERS, STAGE_TABS, STATUS_BUCKETS, booking_stage, next_step_hint, status_bucket,
)


@method_decorator(staff_member_required, name='dispatch')
class StaffHomeView(View):
    """Mini-calendars + active-reservations list, per property or across all of them - mirrors
    PIMS' own Home screen. Reuses availability/utils.py::get_property_calendar() as-is (built for
    the guest-facing property page's own calendar - same day-status logic, called once per
    property shown here rather than modified) and staff/utils.py::booking_stage() (built for the
    booking detail page) for the reservation table's Status column. Deliberately no PIMS-style
    "BLOCK - Unbookable" rows (no such model exists) or overlap-warning icons - see the plan this
    was built from for what's deferred."""
    template_name = 'staff/home.html'

    def get(self, request, *args, **kwargs):
        properties = Property.objects.all()

        selected_property = None
        property_id = request.GET.get('property', '').strip()
        if property_id.isdigit():
            selected_property = properties.filter(pk=property_id).first()

        shown_properties = [selected_property] if selected_property else list(properties)
        months = 12 if selected_property else 3
        calendars = [
            {'property': property, 'months': get_property_calendar(property, months=months)}
            for property in shown_properties
        ]

        status_filter = request.GET.get('status', '').strip() or 'Valid'
        # .holding() never returns a CLOSED_STATUSES booking (that's what makes it "holding"), so
        # Invalid needs the complementary query instead, and All needs everything, unfiltered -
        # Valid and Ended are both subsets of what .holding() returns.
        if status_filter == 'Invalid':
            reservations = Booking.objects.filter(enquiry_status__in=CLOSED_STATUSES)
        elif status_filter == 'All':
            reservations = Booking.objects.all()
        else:
            reservations = Booking.objects.holding()
        reservations = reservations.select_related('property', 'guest')
        if selected_property:
            reservations = reservations.filter(property=selected_property)
        reservations = reservations.order_by('arrival_date')

        rows = []
        for booking in reservations:
            stage = booking_stage(booking)
            bucket = status_bucket(stage)
            if status_filter != 'All' and bucket != status_filter:
                continue
            # Bucketing collapses every dead/cancelled status into one "Closed" stage - not
            # useful on its own, so Invalid rows show the real reason (e.g. "Payment failed")
            # instead of the generic label.
            status_label = booking.enquiry_status if bucket == 'Invalid' else stage
            rows.append({'booking': booking, 'status_label': status_label})

        context = {
            'properties': properties,
            'selected_property': selected_property,
            'calendars': calendars,
            'rows': rows,
            'status_buckets': STATUS_BUCKETS,
            'status_filter': status_filter,
        }
        return render(request, self.template_name, context)


@method_decorator(staff_member_required, name='dispatch')
class StaffBookingLookupView(View):
    """A single reference lookup, not a search - for quickly opening a known booking without
    going through the Home reservation list (see StaffHomeView)."""
    template_name = 'staff/booking_lookup.html'

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, {})

    def post(self, request, *args, **kwargs):
        reference = request.POST.get('reference', '').strip()
        if Booking.objects.filter(reference__iexact=reference).exists():
            return redirect('staff:booking_detail', reference=reference)
        messages.error(request, f'No booking found for reference "{reference}".')
        return render(request, self.template_name, {})


@method_decorator(staff_member_required, name='dispatch')
class StaffGuestListView(View):
    """PIMS-style "All Customers" list - an A-Z surname index plus a free-text search box (name,
    email, or phone - phone included so an unfamiliar incoming call can be matched to a guest),
    both over guests.models.Guest. klt-web's Guest has no address field (PIMS' own screenshot
    shows one) so that column is simply dropped rather than faked. Each row links to the guest's
    most recent booking (by arrival_date) since klt-web has no standalone guest detail page - a
    Guest can be shared across several bookings (see the comment on BookingGuest in
    bookings/models.py), so "most recent" is a reasonable single destination without building a
    guest detail page."""
    template_name = 'staff/guest_list.html'

    def get(self, request, *args, **kwargs):
        query = request.GET.get('q', '').strip()
        letter = request.GET.get('letter', '').strip().upper()

        guests = Guest.objects.all()
        if query:
            letter = ''
            guests = guests.filter(
                Q(first_name__icontains=query) | Q(last_name__icontains=query)
                | Q(email__icontains=query) | Q(phone__icontains=query)
            )
        else:
            # No letter in the URL yet (first visit) defaults to the first letter, matching PIMS'
            # own behaviour rather than dumping every guest on first load; 'ALL' is a real,
            # explicit choice ("anything") that bypasses the surname filter entirely.
            letter = letter or GUEST_LETTERS[0]
            if letter != 'ALL':
                guests = guests.filter(last_name__istartswith=letter)
        guests = guests.order_by('last_name', 'first_name')

        # One extra query per row rather than a batched prefetch - guest counts are small for a
        # single-business PMS, same tradeoff already made for the Home page's per-property
        # calendar loop (see StaffHomeView).
        rows = [{'guest': guest, 'latest_booking': guest.booking_set.order_by('-arrival_date').first()}
                for guest in guests]

        context = {
            'rows': rows,
            'letters': GUEST_LETTERS,
            'selected_letter': letter,
            'query': query,
        }
        return render(request, self.template_name, context)


def _parsed_date(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except ValueError:
        return None


def _parsed_decimal(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


@method_decorator(staff_member_required, name='dispatch')
class StaffBookingDetailView(View):
    """PIMS-style single-booking admin page - see the staff booking detail page plan for the
    full panel-by-panel field mapping and what's deliberately deferred (per-line charge locking,
    a real reminders system, Guest field expansion, etc). Each panel POSTs its own `action` to
    this same view/URL, matching PIMS' own separate "Update" buttons per panel rather than one
    giant admin-style save."""
    template_name = 'staff/booking_detail.html'

    def _get_booking(self, reference):
        booking = Booking.objects.filter(reference__iexact=reference).first()
        if booking is None:
            raise Http404("No booking found for this reference.")
        return booking

    def get(self, request, reference, *args, **kwargs):
        booking = self._get_booking(reference)
        return render(request, self.template_name, self._context(booking))

    def post(self, request, reference, *args, **kwargs):
        booking = self._get_booking(reference)
        handler = {
            'update_booking_info': self._update_booking_info,
            'update_guest_info': self._update_guest_info,
            'update_charges': self._update_charges,
            'update_enquiry': self._update_enquiry,
            'update_payments': self._update_payments,
            'add_deduction': self._add_deduction,
            'add_owner_payment': self._add_owner_payment,
            'add_task_note': self._add_task_note,
        }.get(request.POST.get('action'))
        if handler is not None:
            handler(request, booking)
        return redirect('staff:booking_detail', reference=booking.reference)

    def _context(self, booking):
        charge = getattr(booking, 'charges', None)
        balance_payment = getattr(booking, 'balance_payment', None)
        return {
            'booking': booking,
            'guest': booking.guest,
            'charge': charge,
            'payment': getattr(booking, 'payment', None),
            'balance_payment': balance_payment,
            'arrival': getattr(booking, 'arrival', None),
            'departure': getattr(booking, 'departure', None),
            'properties': Property.objects.all(),
            'stage_tabs': STAGE_TABS,
            'current_stage': booking_stage(booking),
            'extras': extras_summary(booking),
            'next_step': next_step_hint(booking, charge, balance_payment),
            'deductions': booking.deductions.all(),
            'owner_payments': booking.owner_payments.all(),
            'task_history': booking.task_history.all(),
            'currency_choices': CURRENCY_CHOICES,
            'payment_status_choices': PAYMENT_STATUS_CHOICES,
        }

    def _update_booking_info(self, request, booking):
        post = request.POST
        property_id = post.get('property')
        if property_id:
            booking.property_id = property_id
        booking.is_owner = post.get('is_owner') == 'on'

        arrival_date = _parsed_date(post.get('arrival_date'))
        departure_date = _parsed_date(post.get('departure_date'))
        if arrival_date:
            booking.arrival_date = arrival_date
        if departure_date:
            booking.departure_date = departure_date

        for field in ('adults', 'children', 'babies'):
            raw = post.get(field, '').strip()
            if raw.isdigit():
                setattr(booking, field, int(raw))

        try:
            booking.full_clean()
        except ValidationError as error:
            messages.error(request, '; '.join(error.messages))
            return
        booking.save()
        messages.success(request, "Booking info updated.")

    def _update_guest_info(self, request, booking):
        guest = booking.guest
        post = request.POST
        guest.first_name = post.get('first_name', guest.first_name or '').strip()
        guest.last_name = post.get('last_name', guest.last_name).strip() or guest.last_name
        guest.email = post.get('email', guest.email or '').strip()
        guest.phone = post.get('phone', guest.phone or '').strip()
        guest.nif_number = post.get('nif_number', guest.nif_number or '').strip()
        guest.nationality = post.get('nationality', guest.nationality or '').strip()
        preferred_language = post.get('preferred_language', '').strip()
        if preferred_language:
            guest.preferred_language = preferred_language
        guest.save()
        messages.success(request, "Guest info updated.")

    def _update_charges(self, request, booking):
        charge = getattr(booking, 'charges', None)
        if charge is None:
            messages.error(request, "This booking has no Charge record yet.")
            return

        post = request.POST
        for field in ('basic_rental', 'admin', 'security', 'due_at_booking', 'due_at_balance'):
            raw = post.get(field, '').strip()
            if not raw:
                continue
            value = _parsed_decimal(raw)
            if value is None:
                messages.error(request, f"'{raw}' isn't a valid amount for {field.replace('_', ' ')}.")
                return
            setattr(charge, field, value)

        currency = post.get('currency')
        if currency in dict(CURRENCY_CHOICES):
            charge.currency = currency

        charge.save()
        TaskHistoryEntry.objects.create(booking=booking, description="Rental charges updated by staff")
        messages.success(request, "Rental charges updated.")

    def _update_enquiry(self, request, booking):
        post = request.POST
        old_status = booking.enquiry_status

        enquiry_source = post.get('enquiry_source', '').strip()
        if enquiry_source:
            booking.enquiry_source = enquiry_source

        enquiry_date = _parsed_date(post.get('enquiry_date'))
        if enquiry_date:
            booking.enquiry_date = enquiry_date

        new_status = post.get('enquiry_status', '').strip()
        if new_status:
            booking.enquiry_status = new_status

        booking.save()
        if new_status and new_status != old_status:
            TaskHistoryEntry.objects.create(
                booking=booking, description=f"Status changed from '{old_status}' to '{new_status}' by staff",
            )
        messages.success(request, "Enquiry data updated.")

    def _update_payments(self, request, booking):
        post = request.POST
        payment = getattr(booking, 'payment', None)
        if payment is not None:
            status = post.get('payment_status', '').strip()
            if status:
                payment.status = status
                payment.save(update_fields=['status'])

        balance_payment = getattr(booking, 'balance_payment', None)
        if balance_payment is not None:
            status = post.get('balance_payment_status', '').strip()
            if status:
                balance_payment.status = status
                balance_payment.save(update_fields=['status'])

        messages.success(request, "Payments updated.")

    def _add_deduction(self, request, booking):
        post = request.POST
        description = post.get('description', '').strip()
        amount = _parsed_decimal(post.get('amount'))
        if not description or amount is None:
            messages.error(request, "A deduction needs both a description and a valid amount.")
            return
        Deduction.objects.create(
            booking=booking, description=description, amount=amount,
            date=_parsed_date(post.get('date')) or timezone.now().date(),
        )
        messages.success(request, "Deduction added.")

    def _add_owner_payment(self, request, booking):
        post = request.POST
        amount = _parsed_decimal(post.get('amount'))
        currency = post.get('currency')
        if amount is None or currency not in dict(CURRENCY_CHOICES):
            messages.error(request, "An owner payment needs a valid amount and currency.")
            return
        OwnerPayment.objects.create(
            booking=booking, amount=amount, currency=currency, note=post.get('note', '').strip(),
            date=_parsed_date(post.get('date')) or timezone.now().date(),
        )
        messages.success(request, "Owner payment recorded.")

    def _add_task_note(self, request, booking):
        description = request.POST.get('description', '').strip()
        if not description:
            messages.error(request, "Enter a note before adding it.")
            return
        TaskHistoryEntry.objects.create(booking=booking, description=description)
        messages.success(request, "Note added.")
