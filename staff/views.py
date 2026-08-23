from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from availability.utils import get_property_calendar
from bookings.models import CURRENCY_CHOICES, PAYMENT_STATUS_CHOICES, Booking
from bookings.utils import extras_summary
from guests.models import Guest
from properties.models import (
    Accountant, Amenity, Location, Manager, Owner, Price, Property, PropertyImage, PropertySpec,
    SEFDetail, iCalLink,
)
from staff.models import Deduction, OwnerPayment, TaskHistoryEntry
from staff.utils import (
    AMENITY_BOOLEAN_FIELDS, GUEST_LETTERS, OWNER_BOOLEAN_FIELDS, STAGE_TABS, STATUS_BUCKETS,
    booking_stage, next_step_hint, reservation_rows,
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
        base = Booking.objects.filter(property=selected_property) if selected_property else Booking.objects.all()
        rows = reservation_rows(base, status_filter)

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
    shows one) so that column is simply dropped rather than faked. Each row links to
    StaffGuestDetailView, klt-web's equivalent of PIMS' "View/Modify Customer" page."""
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

        context = {
            'guests': guests,
            'letters': GUEST_LETTERS,
            'selected_letter': letter,
            'query': query,
        }
        return render(request, self.template_name, context)


@method_decorator(staff_member_required, name='dispatch')
class StaffGuestDetailView(View):
    """PIMS-style "View/Modify Customer" page - a guest-info form on the left (PIMS' own address/
    flight/extras/discount fields have no klt-web equivalent - those are per-booking concepts
    already covered on the booking detail page - so the fields shown here are exactly what
    guests.models.Guest actually stores) and, on the right, that guest's reservations using the
    same Valid/Invalid/Ended/All filter as StaffHomeView (via the shared reservation_rows()
    helper) but without a property selector - Thomas confirmed one guest's own booking list
    doesn't need that extra granularity."""
    template_name = 'staff/guest_detail.html'

    def _get_guest(self, pk):
        guest = Guest.objects.filter(pk=pk).first()
        if guest is None:
            raise Http404("No guest found.")
        return guest

    def get(self, request, pk, *args, **kwargs):
        guest = self._get_guest(pk)
        status_filter = request.GET.get('status', '').strip() or 'Valid'
        context = {
            'guest': guest,
            'rows': reservation_rows(Booking.objects.filter(guest=guest), status_filter),
            'status_buckets': STATUS_BUCKETS,
            'status_filter': status_filter,
        }
        return render(request, self.template_name, context)

    def post(self, request, pk, *args, **kwargs):
        guest = self._get_guest(pk)
        post = request.POST
        guest.first_name = post.get('first_name', guest.first_name or '').strip()
        guest.last_name = post.get('last_name', guest.last_name).strip() or guest.last_name
        guest.email = post.get('email', guest.email or '').strip()
        guest.phone = post.get('phone', guest.phone or '').strip()
        guest.id_card = post.get('id_card', guest.id_card or '').strip()
        guest.nif_number = post.get('nif_number', guest.nif_number or '').strip()
        guest.nationality = post.get('nationality', guest.nationality or '').strip()
        preferred_language = post.get('preferred_language', '').strip()
        if preferred_language:
            guest.preferred_language = preferred_language
        guest.save()
        messages.success(request, "Guest info updated.")
        return redirect('staff:guest_detail', pk=guest.pk)


def _parsed_date(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except ValueError:
        return None


def _flash_validation_error(request, error):
    """ValidationError.messages repeats a message once per field it's attached to (e.g. an
    overlap error raised against both start_date and end_date), so dedupe before joining."""
    messages.error(request, '; '.join(dict.fromkeys(error.messages)))


def _conflict_query(queryset):
    """Query-string fragment carrying which rows to highlight after a redirect, for handlers
    whose validation failure should flag existing conflicting rows (e.g. a Price overlap)."""
    ids = ','.join(str(pk) for pk in queryset.values_list('pk', flat=True))
    return f"conflict={ids}" if ids else None


def _parsed_decimal(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _parsed_int(raw):
    raw = (raw or '').strip()
    return int(raw) if raw.isdigit() else None


@method_decorator(staff_member_required, name='dispatch')
class StaffPropertyListView(View):
    """PIMS puts property CRUD behind Settings rather than a dedicated list; klt-web instead
    mirrors the Home/Guests list pattern already used elsewhere - a list filterable by Location
    (Thomas's own suggestion) with an "+ Add new property" link above it."""
    template_name = 'staff/property_list.html'

    def get(self, request, *args, **kwargs):
        locations = Location.objects.order_by('title')
        selected_location = None
        location_id = request.GET.get('location', '').strip()
        if location_id.isdigit():
            selected_location = locations.filter(pk=location_id).first()

        properties = Property.objects.select_related('location', 'owner', 'manager').order_by('title')
        if selected_location:
            properties = properties.filter(location=selected_location)

        context = {
            'properties': properties,
            'locations': locations,
            'selected_location': selected_location,
        }
        return render(request, self.template_name, context)


def _property_form_context():
    return {
        'owners': Owner.objects.order_by('name'),
        'managers': Manager.objects.order_by('company'),
        'locations': Location.objects.order_by('title'),
        'accountants': Accountant.objects.order_by('company'),
        'owner_boolean_fields': OWNER_BOOLEAN_FIELDS,
    }


@method_decorator(staff_member_required, name='dispatch')
class StaffPropertyCreateView(View):
    """A minimal create form for exactly the fields StaffPropertyDetailView's "Property info"
    panel edits - everything else (specification, amenities, rate card, SEF details, iCal links,
    photos) only makes sense once the property row exists, so those stay detail-page-only."""
    template_name = 'staff/property_create.html'

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, _property_form_context())

    def post(self, request, *args, **kwargs):
        post = request.POST
        property = Property(
            title=post.get('title', '').strip(),
            short_title=post.get('short_title', '').strip(),
            door_number=post.get('door_number', '').strip(),
            owner_id=post.get('owner') or None,
            manager_id=post.get('manager') or None,
            location_id=post.get('location') or None,
            accountant_id=post.get('accountant') or None,
            al_number=_parsed_int(post.get('al_number')),
            we_book=post.get('we_book') == 'on',
            we_clean=post.get('we_clean') == 'on',
            booking_com_id=post.get('booking_com_id', '').strip(),
            airbnb_id=post.get('airbnb_id', '').strip(),
            vrbo_id=post.get('vrbo_id', '').strip(),
            standard_cleaning_fee=_parsed_decimal(post.get('standard_cleaning_fee')) or 0,
        )
        try:
            property.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return render(request, self.template_name, _property_form_context())
        property.save()
        messages.success(request, "Property created.")
        return redirect('staff:property_detail', pk=property.pk)


@method_decorator(staff_member_required, name='dispatch')
class StaffQuickAddView(View):
    """Backs the "+ New" quick-add panels next to the Location/Owner/Manager/Accountant dropdowns
    on the Create Property page - fetch()-only (no full-page navigation), so creating one of these
    can never disturb whatever's already been typed into the rest of the Create Property form.
    Returns the new row as JSON so the calling page's <select> can just gain a new, pre-selected
    option in place. Only touches full_clean()-valid rows; unique-field clashes (e.g. a duplicate
    Owner email) come back as a normal JSON error for the panel to display inline."""

    def post(self, request, model, *args, **kwargs):
        builder = {
            'location': self._build_location,
            'owner': self._build_owner,
            'manager': self._build_manager,
            'accountant': self._build_accountant,
        }.get(model)
        if builder is None:
            return JsonResponse({'error': 'Unknown type.'}, status=404)
        instance = builder(request.POST)
        try:
            instance.full_clean()
        except ValidationError as error:
            return JsonResponse({'error': '; '.join(dict.fromkeys(error.messages))}, status=400)
        instance.save()
        return JsonResponse({'id': instance.pk, 'label': str(instance)})

    def _build_location(self, post):
        return Location(
            title=post.get('title', '').strip(),
            street=post.get('street', '').strip(),
            zip_code=post.get('zip_code', '').strip(),
            city=post.get('city', '').strip(),
            coordinates=post.get('coordinates', '').strip(),
            map_link=post.get('map_link', '').strip(),
        )

    def _build_owner(self, post):
        fields = {name: post.get(name) == 'on' for name, _ in OWNER_BOOLEAN_FIELDS}
        return Owner(
            name=post.get('name', '').strip(),
            email=post.get('email', '').strip(),
            **fields,
        )

    def _build_manager(self, post):
        # "Just the essentials" - only the head contact is asked for here; the other four contact
        # roles (maintenance/liaison/cleaning/finance) have no sensible universal default and no
        # model-level default of their own (unlike Manager.finance_* alone), so they start out as
        # copies of the head contact rather than being left blank/invalid. Thomas can fill in the
        # real per-role contacts later via /admin/properties/manager/.
        head_name = post.get('head_name', '').strip()
        head_email = post.get('head_email', '').strip()
        head_phone = post.get('head_phone', '').strip()
        return Manager(
            company=post.get('company', '').strip(),
            head_name=head_name, head_email=head_email, head_phone=head_phone,
            maintenance_name=head_name, maintenance_email=head_email, maintenance_phone=head_phone,
            liaison_name=head_name, liaison_email=head_email, liaison_phone=head_phone,
            cleaning_name=head_name, cleaning_email=head_email, cleaning_phone=head_phone,
            finance_name=head_name, finance_email=head_email, finance_phone=head_phone,
        )

    def _build_accountant(self, post):
        return Accountant(
            company=post.get('company', '').strip(),
            name=post.get('name', '').strip(),
            email=post.get('email', '').strip(),
            phone=post.get('phone', '').strip(),
        )


@method_decorator(staff_member_required, name='dispatch')
class StaffPropertyDetailView(View):
    """PIMS-style "Change Property" page - one panel per related model (info, specification,
    amenities, SEF details), the rate card (each row directly editable in place - via the input
    `form=` attribute, since a <form> can't legally wrap several <td>s in one row - stacked above
    a separate "Add price line" card in the same tab for creating new ones), an add/delete-only
    iCal import links list (matching the append-then-delete pattern used for Deductions/Owner
    Payments elsewhere in this app), and a photo gallery. Every OneToOne side-model (PropertySpec/
    Amenity/SEFDetail) is get_or_create'd on load - all three have defaults for every field, so a
    freshly created Property with none of them yet still renders a fully fillable form instead of
    an empty-state message."""
    template_name = 'staff/property_detail.html'

    def _get_property(self, pk):
        property = Property.objects.filter(pk=pk).first()
        if property is None:
            raise Http404("No property found.")
        return property

    # Maps each POST action to the sidebar tab it belongs to, so saving/deleting something in a
    # given panel redirects back to that same panel (via ?panel=) instead of dropping the staffer
    # back on "Main info" every time - see property_detail.html/.css for the tabs themselves.
    # Property info and Property specification share the 'main' tab (shown side by side there on
    # wide viewports), so both actions map to the same slug.
    ACTION_PANELS = {
        'update_property_info': 'main',
        'update_specification': 'main',
        'update_amenities': 'amenities',
        'update_sef': 'sef',
        'update_price': 'rates',
        'add_price': 'rates',
        'delete_price': 'rates',
        'add_ical_link': 'ical',
        'delete_ical_link': 'ical',
        'add_image': 'photos',
        'delete_image': 'photos',
    }
    PANELS = ('main', 'amenities', 'sef', 'rates', 'ical', 'photos')

    def get(self, request, pk, *args, **kwargs):
        property = self._get_property(pk)
        panel = request.GET.get('panel', '')
        active_panel = panel if panel in self.PANELS else 'main'
        context = self._context(property, active_panel)
        context['export_url'] = request.build_absolute_uri(
            reverse('properties:calendar_export', kwargs={'token': property.ical_export_token})
        )
        conflict = request.GET.get('conflict', '')
        context['conflict_price_ids'] = {int(pk) for pk in conflict.split(',') if pk.isdigit()}
        return render(request, self.template_name, context)

    def post(self, request, pk, *args, **kwargs):
        property = self._get_property(pk)
        action = request.POST.get('action')
        handler = {
            'update_property_info': self._update_property_info,
            'update_specification': self._update_specification,
            'update_amenities': self._update_amenities,
            'update_sef': self._update_sef,
            'update_price': self._update_price,
            'add_price': self._add_price,
            'delete_price': self._delete_price,
            'add_ical_link': self._add_ical_link,
            'delete_ical_link': self._delete_ical_link,
            'add_image': self._add_image,
            'delete_image': self._delete_image,
        }.get(action)
        extra_query = handler(request, property) if handler is not None else None
        panel = self.ACTION_PANELS.get(action, 'main')
        url = f"{reverse('staff:property_detail', kwargs={'pk': property.pk})}?panel={panel}"
        if extra_query:
            url += f"&{extra_query}"
        return redirect(url)

    def _context(self, property, active_panel):
        specs, _ = PropertySpec.objects.get_or_create(property=property)
        amenities, _ = Amenity.objects.get_or_create(property=property)
        sef_details, _ = SEFDetail.objects.get_or_create(property=property)
        context = {
            'property': property,
            'active_panel': active_panel,
            'specs': specs,
            'amenities': amenities,
            'amenity_fields': AMENITY_BOOLEAN_FIELDS,
            'sef_details': sef_details,
            'ical_links': property.ical_links.all(),
            'ical_sources': iCalLink.Source.choices,
            'images': property.images.all(),
            'prices': property.prices.order_by('start_date'),
        }
        context.update(_property_form_context())
        return context

    def _update_property_info(self, request, property):
        post = request.POST
        property.title = post.get('title', property.title).strip() or property.title
        property.short_title = post.get('short_title', property.short_title).strip() or property.short_title
        property.door_number = post.get('door_number', '').strip()
        property.owner_id = post.get('owner') or None
        property.manager_id = post.get('manager') or None
        property.location_id = post.get('location') or None
        property.accountant_id = post.get('accountant') or None
        property.al_number = _parsed_int(post.get('al_number'))
        property.we_book = post.get('we_book') == 'on'
        property.we_clean = post.get('we_clean') == 'on'
        property.booking_com_id = post.get('booking_com_id', '').strip()
        property.airbnb_id = post.get('airbnb_id', '').strip()
        property.vrbo_id = post.get('vrbo_id', '').strip()
        fee = _parsed_decimal(post.get('standard_cleaning_fee'))
        if fee is not None:
            property.standard_cleaning_fee = fee
        try:
            property.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        property.save()
        messages.success(request, "Property info updated.")

    def _update_specification(self, request, property):
        specs, _ = PropertySpec.objects.get_or_create(property=property)
        post = request.POST
        specs.is_sea_view = post.get('is_sea_view') == 'on'
        specs.is_pool_view = post.get('is_pool_view') == 'on'
        specs.is_upper_floor = post.get('is_upper_floor') == 'on'
        specs.is_beachfront = post.get('is_beachfront') == 'on'
        specs.children_allowed = post.get('children_allowed') == 'on'
        specs.pets_allowed = post.get('pets_allowed') == 'on'
        for field in ('bedrooms', 'bathrooms', 'half_bathrooms', 'square_metres', 'minimum_nights',
                      'max_adults', 'max_guests'):
            value = _parsed_int(post.get(field))
            if value is not None:
                setattr(specs, field, value)
        specs.description = post.get('description', '').strip()
        specs.save()
        messages.success(request, "Property specification updated.")

    def _update_amenities(self, request, property):
        amenities, _ = Amenity.objects.get_or_create(property=property)
        post = request.POST
        for field, _label in AMENITY_BOOLEAN_FIELDS:
            setattr(amenities, field, post.get(field) == 'on')
        for field in ('double_beds', 'single_beds'):
            value = _parsed_int(post.get(field))
            if value is not None:
                setattr(amenities, field, value)
        bed_sizes = post.get('bed_sizes', '').strip()
        if bed_sizes:
            amenities.bed_sizes = bed_sizes
        amenities.save()
        messages.success(request, "Amenities updated.")

    def _update_sef(self, request, property):
        sef_details, _ = SEFDetail.objects.get_or_create(property=property)
        post = request.POST
        sef_details.unidade_hoteleira = post.get('unidade_hoteleira', '').strip()
        sef_details.estabelecimento = post.get('estabelecimento', '').strip()
        sef_details.chave_de_autenticacao = post.get('chave_de_autenticacao', '').strip()
        sef_details.save()
        messages.success(request, "SEF details updated.")

    def _add_price(self, request, property):
        post = request.POST
        start_date = _parsed_date(post.get('start_date'))
        end_date = _parsed_date(post.get('end_date'))
        if not start_date or not end_date:
            messages.error(request, "A price line needs both a start and end date.")
            return
        price = Price(
            property=property,
            start_date=start_date,
            end_date=end_date,
            rate=_parsed_decimal(post.get('rate')) or 0,
            weekly_discount_percent=_parsed_decimal(post.get('weekly_discount_percent')) or 0,
            last_minute_discount_percent=_parsed_decimal(post.get('last_minute_discount_percent')) or 0,
            last_minute_discount_days=_parsed_int(post.get('last_minute_discount_days')) or 7,
            monthly_discount_percent=_parsed_decimal(post.get('monthly_discount_percent')) or 0,
            extra_adult_rate=_parsed_decimal(post.get('extra_adult_rate')) or 0,
            extra_child_rate=_parsed_decimal(post.get('extra_child_rate')) or 0,
        )
        try:
            price.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return _conflict_query(Price.overlapping(property.pk, start_date, end_date))
        price.save()
        messages.success(request, "Price line added.")

    def _update_price(self, request, property):
        price = Price.objects.filter(pk=request.POST.get('price_id'), property=property).first()
        if price is None:
            messages.error(request, "That price line no longer exists.")
            return
        post = request.POST
        start_date = _parsed_date(post.get('start_date'))
        end_date = _parsed_date(post.get('end_date'))
        if not start_date or not end_date:
            messages.error(request, "A price line needs both a start and end date.")
            return
        price.start_date = start_date
        price.end_date = end_date
        price.rate = _parsed_decimal(post.get('rate')) or 0
        price.weekly_discount_percent = _parsed_decimal(post.get('weekly_discount_percent')) or 0
        price.monthly_discount_percent = _parsed_decimal(post.get('monthly_discount_percent')) or 0
        price.last_minute_discount_percent = _parsed_decimal(post.get('last_minute_discount_percent')) or 0
        price.last_minute_discount_days = _parsed_int(post.get('last_minute_discount_days')) or 7
        price.extra_adult_rate = _parsed_decimal(post.get('extra_adult_rate')) or 0
        price.extra_child_rate = _parsed_decimal(post.get('extra_child_rate')) or 0
        try:
            # Price.clean() excludes price.pk from its own overlap check, so editing dates that
            # still just cover this same row's existing slot won't falsely flag against itself.
            price.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return _conflict_query(Price.overlapping(property.pk, start_date, end_date, exclude_pk=price.pk))
        price.save()
        messages.success(request, "Price line updated.")

    def _delete_price(self, request, property):
        Price.objects.filter(pk=request.POST.get('price_id'), property=property).delete()
        messages.success(request, "Price line deleted.")

    def _add_ical_link(self, request, property):
        post = request.POST
        source = post.get('ical_source', '').strip()
        url = post.get('ical_url', '').strip()
        if not url:
            messages.error(request, "An iCal link needs a URL.")
            return
        iCalLink.objects.create(
            property=property, ical_source=source or None, ical_url=url,
        )
        messages.success(request, "iCal link added.")

    def _delete_ical_link(self, request, property):
        iCalLink.objects.filter(pk=request.POST.get('link_id'), property=property).delete()
        messages.success(request, "iCal link deleted.")

    def _add_image(self, request, property):
        image_file = request.FILES.get('image')
        if not image_file:
            messages.error(request, "Choose a file to upload.")
            return
        image = PropertyImage(
            property=property, image=image_file, caption=request.POST.get('caption', '').strip(),
        )
        try:
            image.full_clean()
        except ValidationError as error:
            _flash_validation_error(request, error)
            return
        image.save()
        messages.success(request, "Photo added.")

    def _delete_image(self, request, property):
        PropertyImage.objects.filter(pk=request.POST.get('image_id'), property=property).delete()
        messages.success(request, "Photo deleted.")


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
            _flash_validation_error(request, error)
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
