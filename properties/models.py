import secrets
from datetime import time, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models, transaction
from properties.utils import pretty_title, location_image_path, property_image_path


class Location(models.Model):
    """Property location information."""
    title = models.CharField(max_length=100)
    block = models.CharField(max_length=100, default="N/A")
    street = models.CharField(max_length=200)
    zip_code = models.CharField(max_length=20)
    city = models.CharField(max_length=100)
    coordinates = models.CharField(max_length=100)
    map_link = models.URLField()
    directions = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    nearest_bins = models.TextField(blank=True, null=True)
    nearest_corner_shop = models.TextField(blank=True, null=True)
    nearest_supermarket = models.TextField(blank=True, null=True)
    # Hex colour (e.g. "#4C6EF5"), curated by staff from the Location detail page - lets the
    # staff cleaning calendar (staff/views.py::StaffCleaningEventsView) group same-location
    # properties visually. Blank means "not curated yet" - the calendar falls back to an
    # auto-generated colour rather than requiring every location to have one set.
    color = models.CharField(max_length=7, blank=True)

    @property
    def slug(self):
        return self.title.lower().replace(" ", "-")

    @property
    def osm_embed_url(self):
        try:
            lat_str, lon_str = self.coordinates.split(',')
            lat, lon = float(lat_str.strip()), float(lon_str.strip())
        except (ValueError, AttributeError):
            return None
        delta = 0.006
        bbox = f"{lon - delta},{lat - delta},{lon + delta},{lat + delta}"
        return f"https://www.openstreetmap.org/export/embed.html?bbox={bbox}&layer=mapnik&marker={lat},{lon}"

    class Meta:
        db_table = 'property_locations'
        verbose_name = 'Property Location'
        verbose_name_plural = 'Property Locations'

    def __str__(self):
        return pretty_title(self.title)


class LocationImage(models.Model):
    """Images associated with a property location."""
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to=location_image_path)
    caption = models.CharField(max_length=200, blank=True, null=True)
    priority = models.IntegerField(default=0)

    class Meta:
        db_table = 'property_location_images'
        verbose_name = 'Property Location Image'
        verbose_name_plural = 'Property Location Images'
        ordering = ['priority']

    def __str__(self):
        return f"{self.location.title} - {self.caption or 'Image'}"


class LocationSpec(models.Model):
    """Specifications for a property location."""
    location = models.OneToOneField(Location, on_delete=models.CASCADE, related_name='specs')
    sea_views = models.BooleanField(default=False)
    pool = models.BooleanField(default=True)
    lift = models.BooleanField(default=True)
    beachfront = models.BooleanField(default=False)
    wifi = models.BooleanField(default=True)
    private_parking = models.BooleanField(default=False)
    street_parking = models.BooleanField(default=True)
    gym = models.BooleanField(default=False)

    FEATURE_PRIORITY = [
        ('beachfront', 'Beachfront', 'locations/icons/beachfront.svg'),
        ('sea_views', 'Sea Views', 'locations/icons/sea_views.svg'),
        ('pool', 'Pool', 'locations/icons/pool.svg'),
        ('wifi', 'WiFi', 'locations/icons/wifi.svg'),
        ('private_parking', 'Parking', 'locations/icons/parking.svg'),
        ('street_parking', 'Parking', 'locations/icons/parking.svg'),
        ('gym', 'Gym', 'locations/icons/gym.svg'),
        ('lift', 'Lift', 'locations/icons/lift.svg'),
    ]

    def top_features(self, count=8):
        features = []
        seen_labels = set()
        for field, label, icon in self.FEATURE_PRIORITY:
            if not getattr(self, field) or label in seen_labels:
                continue
            seen_labels.add(label)
            features.append({'label': label, 'icon': icon})
            if len(features) == count:
                break
        return features

    class Meta:
        db_table = 'property_location_specs'
        verbose_name = 'Property Location Specification'
        verbose_name_plural = 'Property Location Specifications'

    def __str__(self):
        return f"{self.location.title} - Specs"


class LocationRules(models.Model):
    """Rules associated with a property location."""
    location = models.OneToOneField(Location, on_delete=models.CASCADE, related_name='rules')
    quiet_hours_start = models.TimeField()
    quiet_hours_end = models.TimeField()
    pool_hours_start = models.TimeField()
    pool_hours_end = models.TimeField()
    pool_rules = models.TextField(blank=True, null=True)
    condominium_rules = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'property_location_rules'
        verbose_name = 'Property Location Rule'
        verbose_name_plural = 'Property Location Rules'

    def __str__(self):
        return f"{self.location.title} - Rules"

class ManagementCompany(models.Model):
    """The business entity that books and/or cleans a property, tracked independently per property
    via Property.booking_company/cleaning_company below. A company can optionally have contacts for
    any of five roles (head/maintenance/liaison/cleaning/finance) - none are required, and a company
    acting on a narrow scope (e.g. cleaning only) may only ever need one. An untracked/external
    booking or cleaning party simply has no ManagementCompany row - that's what
    Property.booking_company/cleaning_company being NULL means.

    towels_per_guest/includes_beach_towels/standard_meet_and_greet_fee/check_in_method/
    self_check_in_after/freshen_after_days (2026-08-27, per Thomas) are operational defaults a
    property under this company's booking/cleaning management can inherit - deliberately all
    nullable, since a company running its own full operation (e.g. Thomas's own) will want most of
    these set, while a company we only handle bookings or cleaning for may only ever specify a
    handful, or none. None means "not specified by this company", distinct from a real zero/off
    value (e.g. towels_per_guest=0, includes_beach_towels=False). Washing materials are a separate
    per-company list (see WashingMaterial below), not a single field here."""
    class CheckInMethod(models.TextChoices):
        SELF_CHECK_IN = 'self_check_in', 'Always self check-in'
        IN_PERSON = 'in_person', 'Always in person'
        MIXED = 'mixed', 'Mixed - depends on arrival time'

    name = models.CharField(max_length=200, unique=True)
    head_name = models.CharField(max_length=200, blank=True, default='')
    head_email = models.EmailField(blank=True, default='')
    head_phone = models.CharField(max_length=50, blank=True, default='')
    maintenance_name = models.CharField(max_length=200, blank=True, default='')
    maintenance_phone = models.CharField(max_length=50, blank=True, default='')
    maintenance_email = models.EmailField(blank=True, default='')
    liaison_name = models.CharField(max_length=200, blank=True, default='')
    liaison_phone = models.CharField(max_length=50, blank=True, default='')
    liaison_email = models.EmailField(blank=True, default='')
    cleaning_name = models.CharField(max_length=200, blank=True, default='')
    cleaning_phone = models.CharField(max_length=50, blank=True, default='')
    cleaning_email = models.EmailField(blank=True, default='')
    finance_name = models.CharField(max_length=200, blank=True, default='')
    finance_phone = models.CharField(max_length=50, blank=True, default='')
    finance_email = models.EmailField(blank=True, default='')

    towels_per_guest = models.PositiveSmallIntegerField(null=True, blank=True)
    includes_beach_towels = models.BooleanField(
        null=True, blank=True, help_text="Whether towels_per_guest includes a beach towel.",
    )
    linen_provided = models.BooleanField(
        null=True, blank=True,
        help_text="Whether this company dresses beds in linen appropriate to the season - drives "
                  "the Manage Booking hub's Amenities page (bookings/views.py::"
                  "BookingManageAmenitiesView), via Property.cleaning_company.",
    )
    standard_meet_and_greet_fee = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    check_in_method = models.CharField(max_length=20, choices=CheckInMethod.choices, null=True, blank=True)
    # Only meaningful when check_in_method is MIXED - an arrival at or after this time defaults to
    # self check-in, anything earlier gets an in-person meet & greet instead.
    self_check_in_after = models.TimeField(null=True, blank=True)
    # Only meaningful for a property under this company's cleaning management (Property.
    # cleaning_company) - triggers a 'freshen' CleaningTask (staff/models.py) ahead of an arrival
    # when this many days or more will have passed since the property's last clean, covering a
    # vacant stretch between bookings that would otherwise go uncleaned until the next guest's own
    # turnover. See staff/utils.py::sync_cleaning_tasks_for_booking for how it's actually applied.
    freshen_after_days = models.PositiveSmallIntegerField(null=True, blank=True)
    # Unlike the operational fields above, these two always have a real value rather than "not
    # specified" - the check-ins calendar (staff/utils.py::compute_arrival_eta) falls back to
    # standard_checkin_time to place an arrival with no real ETA at a sensible clock time instead
    # of the all-day band, so a null here would just push the ambiguity somewhere else.
    # standard_checkout_time has no consumer yet (no departure-side calendar exists), added ahead
    # of that per Thomas, 2026-08-28.
    standard_checkin_time = models.TimeField(default=time(14, 0))
    standard_checkout_time = models.TimeField(default=time(10, 0))
    # Per-company on/off switch for whether a property under this company's cleaning/booking
    # management generates CleaningTask/Checkin rows at all (staff/utils.py::
    # sync_cleaning_tasks_for_booking, sync_freshen_tasks_for_property, sync_checkins_for_booking)
    # - lets a company that manages its own cleaning/check-ins separately (2026-08-28, per Thomas)
    # stay off those calendars entirely, rather than cluttering them with tasks nobody here acts
    # on. Default True preserves today's behaviour (every property currently appears) for every
    # existing company. Only consulted when a company is actually set (Property.cleaning_company/
    # booking_company is nullable) - an untracked property is unaffected either way, same as every
    # other operational field above. Toggling this doesn't retroactively clean up or backfill
    # already-existing task rows - re-run the sync_cleaning_tasks/sync_checkins management commands
    # to reconcile, same as any other operational-setting change here.
    cleans_on_calendar = models.BooleanField(default=True)
    checkins_on_calendar = models.BooleanField(default=True)
    # Gates the new Finance area (finance app: Memo/AdHocService/PayoutRecord) - checked against
    # two different Property FKs for two different purposes: as a property's cleaning_company, it
    # gates whether Memo/AdHocService rows exist for it at all (see finance/services.py::
    # sync_memo_for_turnover_task); as a property's booking_company, it gates whether its owner
    # appears on the Payouts tab / in a Statement. A property with neither company set, or the
    # flag off on the relevant one, is excluded entirely - deliberately opt-in (default False),
    # unlike cleans_on_calendar/checkins_on_calendar's opt-out default: an untracked property
    # shouldn't silently start appearing in real financial documents. Toggling this on from False
    # to True auto-backfills Memo rows for this company's turnover cleans dated today-or-later
    # (staff/views.py::StaffSettingsView._update_management_company, via finance/services.py::
    # backfill_memos_for_company) - never anything already in the past, so a clean that was never
    # billed contemporaneously doesn't retroactively appear. The sync_finance_memos management
    # command still exists for a manual/global re-reconcile (e.g. after a direct DB edit that
    # bypasses the save() path above).
    finances_managed_internally = models.BooleanField(default=False)
    # Per Thomas 2026-09-01: gates whether this company's properties can appear in the public
    # website's own availability search (availability/views.py::SearchView.
    # get_available_properties()) - only meaningful as a property's booking_company, same scoping
    # as finances_managed_internally above. Before this field existed, ANY booking_company being
    # set at all made a property search-visible, with no way to distinguish "we book this
    # ourselves" from "a different company books this property (through their own channels), we
    # just track them as a ManagementCompany for ops purposes" - real gap found 2026-09-01: 1
    # property booked through a company that isn't Thomas's own was already search-visible with no
    # way to turn it off. Default True preserves every existing company's current visible/hidden
    # set (every company that currently has search-visible properties keeps them, nothing changes
    # on deploy) - Thomas then turns it off per company as needed, same opt-out default pattern as
    # cleans_on_calendar/checkins_on_calendar above, not finances_managed_internally's opt-in one.
    bookable_on_website = models.BooleanField(default=True)

    class Meta:
        db_table = 'management_companies'
        verbose_name = 'Management Company'
        verbose_name_plural = 'Management Companies'

    def __str__(self):
        return self.name


class WashingMaterial(models.Model):
    """One line item in a ManagementCompany's washing-materials list (e.g. "Dish soap" x2,
    "Laundry tabs" x20) - per-company and freeform (title is whatever the company calls it), not a
    shared catalog like bookings.models.RequestType, since what a company stocks and how they name
    it varies too much to usefully standardise across companies."""
    company = models.ForeignKey(ManagementCompany, on_delete=models.CASCADE, related_name='washing_materials')
    title = models.CharField(max_length=100)
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = 'management_company_washing_materials'
        verbose_name = 'Washing Material'
        verbose_name_plural = 'Washing Materials'
        ordering = ('title',)

    def __str__(self):
        return f"{self.title} x{self.quantity} ({self.company})"


class Owner(models.Model):
    """Property owner information."""
    name = models.CharField(max_length=200, unique=True)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=50, blank=True, null=True, unique=True)
    nif_number = models.CharField(max_length=50, blank=True, null=True, unique=True)
    default_clean = models.BooleanField()
    default_meet_greet = models.BooleanField()
    takes_euros = models.BooleanField()
    takes_pounds = models.BooleanField()
    cleans_are_invoiced = models.BooleanField()
    rental_commissions_are_invoiced = models.BooleanField()
    is_paid_regularly = models.BooleanField()
    # Owner Suite login (owners app) - a superuser creates the User and sets its password
    # directly in Django admin (no self-service signup/password-reset flow, same no-outbound-
    # email constraint every other account in this project already lives with - see
    # [[project_klt_web_automation_roadmap]]), then links it here. NULL means this owner has no
    # portal access yet - the overwhelmingly common case until Thomas onboards someone.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='owner_profile',
    )

    class Meta:
        db_table = 'property_owners'
        verbose_name = 'Property Owner'
        verbose_name_plural = 'Property Owners'

    def __str__(self):
        return self.name


class Accountant(models.Model):
    """Property accountant information."""
    company = models.CharField(max_length=200)
    name = models.CharField(max_length=200)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=50, unique=True)

    class Meta:
        db_table = 'property_accountants'
        verbose_name = 'Property Accountant'
        verbose_name_plural = 'Property Accountants'

    def __str__(self):
        return f"{self.company} - {self.name}"


class Property(models.Model):
    """Main property model."""
    title = models.CharField(max_length=200, unique=True, blank=False)
    short_title = models.CharField(max_length=100, unique=True, blank=False)
    door_number = models.CharField(max_length=20, blank=True, null=True)
    
    # Foreign key relationships
    owner = models.ForeignKey(Owner, on_delete=models.SET_NULL, null=True)
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True)
    accountant = models.ForeignKey(Accountant, on_delete=models.SET_NULL, null=True, blank=True)

    # Property details
    al_number = models.IntegerField(blank=True, null=True)
    # Which company (if any) actually books this property vs. cleans it. Both NULL means an
    # untracked external party does that job.
    booking_company = models.ForeignKey(
        ManagementCompany, on_delete=models.SET_NULL, null=True, blank=True, related_name='booked_properties'
    )
    cleaning_company = models.ForeignKey(
        ManagementCompany, on_delete=models.SET_NULL, null=True, blank=True, related_name='cleaned_properties'
    )
    standard_cleaning_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Opaque bearer token for this property's exported .ics feed (see
    # properties/views.py::PropertyCalendarExportView) - not a booking-reference-style short code
    # meant to be typed by a person, so no collision-retry loop like Booking.save()'s reference
    # generation: 32 random bytes is astronomically collision-free, the unique=True constraint is
    # just a backstop.
    ical_export_token = models.CharField(max_length=64, unique=True, blank=True, null=True)

    @property
    def slug(self):
        return self.short_title.lower().replace(" ", "-")

    class Meta:
        db_table = 'properties'
        verbose_name = 'Property'
        verbose_name_plural = 'Properties'

    def __str__(self):
        return pretty_title(self.title)

    def save(self, *args, **kwargs):
        if not self.ical_export_token:
            self.ical_export_token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)
        Amenity.objects.get_or_create(property=self)


class PropertyImage(models.Model):
    """Images associated with a property."""
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to=property_image_path)
    caption = models.CharField(max_length=200, blank=True, null=True)
    priority = models.IntegerField(default=0)

    class Meta:
        db_table = 'property_images'
        verbose_name = 'Property Image'
        verbose_name_plural = 'Property Images'
        ordering = ['priority']

    def __str__(self):
        return f"{self.property.title} - {self.caption or 'Image'}"


class Price(models.Model):
    """Property pricing information by year."""
    property = models.ForeignKey('Property', on_delete=models.CASCADE, related_name='prices')
    start_date = models.DateField(verbose_name='start')
    end_date = models.DateField(verbose_name='end')
    rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    weekly_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        verbose_name='weekly %',
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Discount applied to stays of 7 or more nights, as a percentage."
    )
    last_minute_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        verbose_name='last-min %',
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Discount applied when booking within the last-minute window, as a percentage."
    )
    last_minute_discount_days = models.PositiveIntegerField(
        default=7,
        verbose_name='last-min days',
        help_text="Number of days before arrival within which the last-minute discount applies."
    )
    monthly_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        verbose_name='monthly %',
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Discount applied to stays meeting the site-wide monthly-stay minimum (see Booking Settings), as a percentage."
    )
    extra_adult_rate = models.DecimalField(
        max_digits=10, decimal_places=2, default=10,
        verbose_name='extra adult',
        validators=[MinValueValidator(0)],
        help_text="Charge per night for each adult beyond the first 2."
    )
    extra_child_rate = models.DecimalField(
        max_digits=10, decimal_places=2, default=5,
        verbose_name='extra child',
        validators=[MinValueValidator(0)],
        help_text="Charge per night for each child."
    )

    class Meta:
        db_table = 'property_prices'
        verbose_name = 'Property Price'
        verbose_name_plural = 'Property Prices'

    def __str__(self):
        return f"{self.property.title} - {self.start_date} to {self.end_date}"

    @staticmethod
    def overlapping(property_id, start_date, end_date, exclude_pk=None):
        qs = Price.objects.filter(
            property_id=property_id,
            start_date__lte=end_date,
            end_date__gte=start_date,
        )
        if exclude_pk is not None:
            qs = qs.exclude(pk=exclude_pk)
        return qs

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError({'end_date': 'End date must be on or after the start date.'})
        if self.property_id and self.start_date and self.end_date:
            overlap = self.overlapping(self.property_id, self.start_date, self.end_date, exclude_pk=self.pk).first()
            if overlap:
                message = f"Overlaps with the {overlap.start_date} to {overlap.end_date} price line."
                raise ValidationError({'start_date': message, 'end_date': message})


class PropertyOwnership(models.Model):
    """One row per continuous ownership window. NULL start_date means 'owned since before
    klt-web began tracking ownership history' - used only by the one-time backfill migration and
    by record_initial_ownership() (a brand-new Property/Owner pairing has the same 'we don't
    actually know when this started' problem). NULL end_date means this is the current/ongoing
    owner - at most one NULL-end_date row should exist per property at a time, enforced by
    overlapping()/clean() below, not a DB constraint (same convention as Price.overlapping())."""
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name='ownership_history')
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name='ownership_history')
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'property_ownership_history'
        verbose_name = 'Property Ownership'
        verbose_name_plural = 'Property Ownership History'
        # Postgres's default NULL ordering for DESC is NULLS FIRST - a plain '-start_date' would
        # put the "owned since before tracking" row at the top of a newest-first list instead of
        # the bottom, so nulls_last is explicit here.
        ordering = [models.F('start_date').desc(nulls_last=True)]

    def __str__(self):
        return f"{self.property.title} - {self.owner} ({self.start_date or '…'} to {self.end_date or 'present'})"

    @staticmethod
    def overlapping(property_id, start_date, end_date, exclude_pk=None):
        """NULL-safe interval overlap check, modeled on Price.overlapping() but treating a NULL
        start_date as unbounded-past and a NULL end_date as unbounded-future/ongoing."""
        qs = PropertyOwnership.objects.filter(property_id=property_id)
        start_ok = models.Q() if end_date is None else (
            models.Q(start_date__isnull=True) | models.Q(start_date__lte=end_date)
        )
        end_ok = models.Q() if start_date is None else (
            models.Q(end_date__isnull=True) | models.Q(end_date__gte=start_date)
        )
        qs = qs.filter(start_ok & end_ok)
        if exclude_pk is not None:
            qs = qs.exclude(pk=exclude_pk)
        return qs

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError({'end_date': 'End date must be on or after the start date.'})
        if self.property_id:
            overlap = self.overlapping(self.property_id, self.start_date, self.end_date, exclude_pk=self.pk).first()
            if overlap:
                message = (
                    f"Overlaps with {overlap.owner}'s ownership window "
                    f"({overlap.start_date or 'the beginning'} to {overlap.end_date or 'ongoing'})."
                )
                raise ValidationError({'start_date': message, 'end_date': message})

    @classmethod
    def record_initial_ownership(cls, property, owner):
        """Called once, right after a brand-new Property is saved with an owner already chosen
        on the create form - not for later changes, see record_handover()."""
        row = cls(property=property, owner=owner, start_date=None, end_date=None)
        row.full_clean()
        row.save()
        return row

    @classmethod
    @transaction.atomic
    def record_handover(cls, property, new_owner, effective_date):
        """The one sanctioned way to change who currently owns a property once it has ownership
        history: closes the current open-ended row (end_date = effective_date - 1 day, so the
        old and new windows are adjacent and non-overlapping), opens a new row for new_owner
        starting effective_date, and syncs Property.owner - atomically. If the property has no
        current owner yet, the close-out step is skipped."""
        current = cls.objects.filter(property=property, end_date__isnull=True).order_by('-start_date').first()
        if current is not None:
            if current.owner_id == new_owner.pk:
                raise ValidationError("This owner is already the current owner.")
            if current.start_date is not None and effective_date <= current.start_date:
                raise ValidationError(
                    f"Effective date must be after the current owner's start date ({current.start_date})."
                )
            current.end_date = effective_date - timedelta(days=1)
            current.full_clean()
            current.save()
        new_row = cls(property=property, owner=new_owner, start_date=effective_date, end_date=None)
        new_row.full_clean()
        new_row.save()
        property.owner = new_owner
        property.save(update_fields=['owner'])
        return new_row


class PropertySpec(models.Model):
    """Property specifications and features."""
    property = models.OneToOneField(Property, on_delete=models.CASCADE, related_name='specs')
    is_sea_view = models.BooleanField(default=False)
    is_pool_view = models.BooleanField(default=False)
    is_upper_floor = models.BooleanField(default=False)
    is_beachfront = models.BooleanField(default=False)
    bedrooms = models.IntegerField(default=1)
    bathrooms = models.IntegerField(default=1)
    half_bathrooms = models.IntegerField(default=0)
    square_metres = models.IntegerField(default=60)
    minimum_nights = models.IntegerField(default=4)
    max_adults = models.IntegerField(default=4)
    max_guests = models.IntegerField(default=4)
    children_allowed = models.BooleanField(default=True)
    pets_allowed = models.BooleanField(default=False)
    description = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'property_specs'
        verbose_name = 'Property Specification'
        verbose_name_plural = 'Property Specifications'

    def __str__(self):
        return f"{self.property.title} - Specs"
    

class Amenity(models.Model):
    """Property amenities."""
    property = models.OneToOneField(Property, on_delete=models.CASCADE, related_name='amenities')
    bathtub_and_shower = models.BooleanField(default=True)
    walk_in_shower = models.BooleanField(default=False)
    hairdryer = models.BooleanField(default=True)
    double_beds = models.IntegerField(default=1)
    single_beds = models.IntegerField(default=0)
    bed_sizes = models.TextField(default='160 x 200')
    washing_machine = models.BooleanField(default=True)
    dryer = models.BooleanField(default=False)
    iron = models.BooleanField(default=True)
    ironing_board = models.BooleanField(default=True)
    wifi = models.BooleanField(default=True)
    tv = models.BooleanField(default=True)
    iptv = models.BooleanField(default=True)
    air_conditioning = models.BooleanField(default=True)
    heating = models.BooleanField(default=True)
    kitchen = models.BooleanField(default=True)
    oven = models.BooleanField(default=True)
    hob = models.BooleanField(default=True)
    toaster = models.BooleanField(default=True)
    kettle = models.BooleanField(default=True)
    microwave = models.BooleanField(default=True)
    fridge = models.BooleanField(default=True)
    freezer = models.BooleanField(default=True)
    dishwasher = models.BooleanField(default=True)
    barbecue = models.BooleanField(default=False)
    pool = models.BooleanField(default=True)
    hot_tub = models.BooleanField(default=False)
    garden = models.BooleanField(default=False)
    air_conditioning_in_bedrooms = models.BooleanField(default=True)
    air_conditioning_in_living_room = models.BooleanField(default=True)
    heating_in_bedrooms = models.BooleanField(default=True)
    heating_in_living_room = models.BooleanField(default=True)
    sofa_bed = models.BooleanField(default=True)
    safe = models.BooleanField(default=True)
    vacuum_cleaner = models.BooleanField(default=True)
    mop_and_bucket = models.BooleanField(default=True)
    clothes_horse = models.BooleanField(default=True)
    coffee_machine = models.BooleanField(default=True)
    hand_towels_per_guest = models.IntegerField(default=1)
    bath_towels_per_guest = models.IntegerField(default=1)
    beach_towels_per_guest = models.IntegerField(default=1)

    FEATURE_PRIORITY = [
        ('wifi', 'WiFi', 'properties/icons/wifi.svg'),
        ('pool', 'Pool', 'properties/icons/pool.svg'),
        ('air_conditioning', 'Air Conditioning', 'properties/icons/air_conditioning.svg'),
        ('air_conditioning_in_bedrooms', 'Air Conditioning', 'properties/icons/air_conditioning.svg'),
        ('air_conditioning_in_living_room', 'Air Conditioning', 'properties/icons/air_conditioning.svg'),
        ('heating', 'Heating', 'properties/icons/heating.svg'),
        ('heating_in_bedrooms', 'Heating', 'properties/icons/heating.svg'),
        ('heating_in_living_room', 'Heating', 'properties/icons/heating.svg'),
        ('kitchen', 'Kitchen', 'properties/icons/kitchen.svg'),
        ('hob', 'Kitchen', 'properties/icons/kitchen.svg'),
        ('oven', 'Oven', 'properties/icons/oven.svg'),
        ('microwave', 'Microwave', 'properties/icons/microwave.svg'),
        ('fridge', 'Fridge', 'properties/icons/fridge.svg'),
        ('freezer', 'Fridge', 'properties/icons/fridge.svg'),
        ('dishwasher', 'Dishwasher', None),
        ('washing_machine', 'Washing Machine', 'properties/icons/washing_machine.svg'),
        ('dryer', 'Dryer', 'properties/icons/dryer.svg'),
        ('iron', 'Iron', 'properties/icons/iron.svg'),
        ('ironing_board', 'Iron', 'properties/icons/iron.svg'),
        ('tv', 'TV', 'properties/icons/tv.svg'),
        ('iptv', 'TV', 'properties/icons/tv.svg'),
        ('bathtub_and_shower', 'Bathtub', 'properties/icons/bathtub.svg'),
        ('walk_in_shower', 'Walk-in Shower', 'properties/icons/shower.svg'),
        ('hairdryer', 'Hairdryer', None),
        ('toaster', 'Toaster', 'properties/icons/toaster.svg'),
        ('kettle', 'Kettle', 'properties/icons/kettle.svg'),
        ('barbecue', 'Barbecue', 'properties/icons/barbecue.svg'),
        ('hot_tub', 'Hot Tub', None),
        ('garden', 'Garden', 'properties/icons/garden.svg'),
        ('sofa_bed', 'Sofa Bed', 'properties/icons/sofa_bed.svg'),
        ('safe', 'Safe', 'properties/icons/safe.svg'),
        ('vacuum_cleaner', 'Vacuum Cleaner', 'properties/icons/vacuum_cleaner.svg'),
        ('mop_and_bucket', 'Mop & Bucket', 'properties/icons/mop_and_bucket.svg'),
        ('clothes_horse', 'Clothes Horse', 'properties/icons/clothes_horse.svg'),
        ('coffee_machine', 'Coffee Machine', 'properties/icons/coffee_machine.svg'),
    ]

    TOWEL_TYPES = (
        ('hand_towels_per_guest', 'hand'), ('bath_towels_per_guest', 'bath'), ('beach_towels_per_guest', 'beach'),
    )

    def towel_summary(self):
        """Short guest-facing towel highlight for the property page's feature tile - deliberately
        not the exact per-type counts (Thomas asked for this to fit two lines like its neighbours,
        same as the other multi-word tile labels, rather than the three-line spelled-out version
        this used to be). Hand towels are treated as a given and not worth calling out; beach
        towels are what actually varies property to property, so lead with "Bath and beach
        towels" whenever they're offered, falling back to "Hand and bath towels" to still show
        something when they're not. Returns None (hides the tile) only when no towel type is
        provided at all."""
        if not any(getattr(self, field) > 0 for field, _ in self.TOWEL_TYPES):
            return None
        if self.beach_towels_per_guest > 0:
            return "Bath and beach towels"
        return "Hand and bath towels"

    def towel_line_items(self, guest_count):
        """(total_count, label) pairs for the Manage Booking hub's Amenities page - one bullet per
        provided towel type, scaled by the booking's actual current guest count rather than shown
        as a flat per-guest rate (e.g. 2 guests x 1 hand towel each -> (2, 'Hand towels')). Skips
        any type set to 0 (not provided) entirely; guest_count is the caller's responsibility - see
        bookings.models.Booking.total_guests for the guest-list-aware count this is meant to be
        called with."""
        items = []
        for field, kind in self.TOWEL_TYPES:
            per_guest = getattr(self, field)
            if per_guest <= 0:
                continue
            total = per_guest * guest_count
            noun = 'towel' if total == 1 else 'towels'
            items.append((total, f"{kind.capitalize()} {noun}"))
        return items

    # Every boolean field, guest-labelled and NOT deduplicated by shared label like
    # FEATURE_PRIORITY (whose job is a small highlight-icon grid) - this drives the Manage Booking
    # hub's Amenities page, where a guest wants the full, precise list (e.g. air conditioning
    # broken out by room, matching the specificity of staff's own guest-reply email) rather than a
    # handful of highlights.
    GUEST_FEATURE_LIST = (
        ('wifi', 'WiFi'),
        ('air_conditioning', 'Air conditioning'),
        ('air_conditioning_in_bedrooms', 'Air conditioning in bedrooms'),
        ('air_conditioning_in_living_room', 'Air conditioning in living room'),
        ('heating', 'Heating'),
        ('heating_in_bedrooms', 'Heating in bedrooms'),
        ('heating_in_living_room', 'Heating in living room'),
        ('tv', 'TV'),
        ('iptv', 'IPTV'),
        ('safe', 'Safe for valuables'),
        ('bathtub_and_shower', 'Bathtub & shower'),
        ('walk_in_shower', 'Walk-in shower'),
        ('hairdryer', 'Hairdryer'),
        ('sofa_bed', 'Sofa bed'),
        ('kitchen', 'Kitchen'),
        ('oven', 'Oven'),
        ('hob', 'Hob'),
        ('microwave', 'Microwave'),
        ('toaster', 'Toaster'),
        ('kettle', 'Kettle'),
        ('coffee_machine', 'Filter coffee machine'),
        ('fridge', 'Fridge'),
        ('freezer', 'Freezer'),
        ('dishwasher', 'Dishwasher'),
        ('barbecue', 'Barbecue'),
        ('washing_machine', 'Washing machine'),
        ('dryer', 'Dryer'),
        ('iron', 'Iron'),
        ('ironing_board', 'Ironing board'),
        ('clothes_horse', 'Clothes horse'),
        ('vacuum_cleaner', 'Vacuum cleaner'),
        ('mop_and_bucket', 'Mop & bucket'),
        ('pool', 'Pool'),
        ('hot_tub', 'Hot tub'),
        ('garden', 'Garden'),
    )

    def full_feature_list(self):
        """Every provided amenity's guest-friendly label, in GUEST_FEATURE_LIST order - see that
        constant's own docstring for why this doesn't reuse top_features()'s deduplicated,
        icon-gated logic. The one exception: the generic 'Air conditioning'/'Heating' entries are
        dropped whenever either room-specific flag for that amenity is set, since listing both the
        blanket claim and the specific rooms reads as redundant (Thomas asked for this once the
        room-specific fields started being used) - the room-specific entries alone are more
        informative anyway."""
        skip_generic = set()
        if self.air_conditioning_in_bedrooms or self.air_conditioning_in_living_room:
            skip_generic.add('air_conditioning')
        if self.heating_in_bedrooms or self.heating_in_living_room:
            skip_generic.add('heating')
        return [
            label for field, label in self.GUEST_FEATURE_LIST
            if field not in skip_generic and getattr(self, field)
        ]

    def bed_label(self):
        if self.double_beds > 0:
            size = self.bed_sizes.replace(' ', '')
            return f"{size}cm Bed"
        if self.single_beds > 0:
            return "Twin Beds"
        return None

    def top_features(self, count=None):
        features = []
        bed_label = self.bed_label()
        if bed_label:
            features.append({'label': bed_label, 'icon': 'properties/icons/bed.svg'})
        seen_labels = set()
        for field, label, icon in self.FEATURE_PRIORITY:
            if icon is None or not getattr(self, field) or label in seen_labels:
                continue
            seen_labels.add(label)
            features.append({'label': label, 'icon': icon})
            if count and len(features) == count:
                break
        return features

    class Meta:
        db_table = 'property_amenities'
        verbose_name = 'Property Amenity'
        verbose_name_plural = 'Property Amenities'

    def __str__(self):
        return f"{self.property.title} - Amenities"


class SEFDetail(models.Model):
    """Property SEF (Portuguese tourism authority) details."""
    property = models.OneToOneField(Property, on_delete=models.CASCADE, related_name='sef_details')
    unidade_hoteleira = models.CharField(max_length=200, blank=True, null=True)
    estabelecimento = models.CharField(max_length=200, blank=True, null=True)
    chave_de_autenticacao = models.CharField(max_length=200, blank=True, null=True)

    class Meta:
        db_table = 'property_sef_details'
        verbose_name = 'Property SEF Detail'
        verbose_name_plural = 'Property SEF Details'

    def __str__(self):
        return f"{self.property.title} - SEF"


class Platform(models.Model):
    """Admin-editable catalog of booking platforms an iCalLink can be sourced from (Airbnb,
    Booking.com, Vrbo, ...) - replaces iCalLink's previously-hardcoded Source choices so Thomas can
    add a new platform from Settings without a code deploy (2026-08-28).

    take_security_deposits (default False) is a per-platform flag for whether that platform's own
    booking terms already cover damage/security deposits - not read anywhere yet, added ahead of a
    consumer per Thomas.

    NOTE: env_settings.PLATFORMS still separately hardcodes 'Airbnb'/'Booking.com'/'Vrbo' for
    platform-payout and direct-guest-exclusion logic (bookings/payouts.py, staff/views.py,
    properties/views.py) - a Platform added here beyond those three won't automatically be
    recognised as a platform booking by that logic. Flagged, not migrated, in this pass."""
    name = models.CharField(max_length=100, unique=True)
    take_security_deposits = models.BooleanField(default=False)

    class Meta:
        db_table = 'platforms'
        verbose_name = 'Platform'
        verbose_name_plural = 'Platforms'
        ordering = ('name',)

    def __str__(self):
        return self.name


class PropertyPlatformID(models.Model):
    """One property's own listing ID/reference on one Platform (e.g. its Airbnb listing number) -
    replaces Property's previously-hardcoded booking_com_id/airbnb_id/vrbo_id fields with an
    open-ended per-Platform list, so a new Platform automatically gets its own listing-ID field on
    the property form (2026-08-28, per Thomas). Purely a staff-facing reference value, same as
    those three fields were - not read by any sync/matching logic (iCalLink.platform is what
    actually drives feed syncing).

    platform is PROTECT, same as iCalLink.platform - a live incident during this same build
    (deleting/re-adding the seeded Vrbo row from Settings silently cascade-deleted A02's real,
    migrated Vrbo listing ID) showed CASCADE treats "just a reference value" as lower-stakes than
    it actually is for real historical data. Deleting a Platform that still has a listing ID
    attached now has to go through StaffSettingsView._delete_platform's explicit ProtectedError
    handling instead."""
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name='platform_ids')
    platform = models.ForeignKey(Platform, on_delete=models.PROTECT, related_name='property_ids')
    listing_id = models.CharField(max_length=200)

    class Meta:
        db_table = 'property_platform_ids'
        verbose_name = 'Property Platform ID'
        verbose_name_plural = 'Property Platform IDs'
        unique_together = ('property', 'platform')

    def __str__(self):
        return f"{self.property} - {self.platform}: {self.listing_id}"


class iCalLink(models.Model):
    """Property iCal links for calendar synchronization."""

    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name='ical_links')
    platform = models.ForeignKey(
        Platform, on_delete=models.PROTECT, null=True, blank=True, related_name='ical_links',
    )
    ical_url = models.URLField(blank=True, null=True)
    last_synced = models.DateTimeField(blank=True, null=True)
    # True for a feed that reflects a listing the OWNER runs and controls themselves (e.g. their
    # own separate Booking.com listing for a property that's otherwise sold through our site) -
    # as opposed to the normal case, a platform listing WE manage. The single source of truth
    # bookings/utils.py::sync_ical_link() reads to decide whether an imported booking gets
    # Booking.is_owner=True - see that function's own call site for why: those bookings are real
    # guest stays we had no part in arranging, so they need the same Owner Suite visibility/
    # payout-exempt treatment as the owner's own direct reservations, not the normal platform-
    # booking treatment (2026-08-30, per Thomas).
    is_owner_link = models.BooleanField(default=False)
    # Per Thomas 2026-09-01: mirrors PIMS' own per-import "Summary/Name of booking Contains X"
    # filter (Settings > Sync calendars) - a feed event whose SUMMARY contains any of these terms
    # (case-insensitive substring, one term per line) is skipped entirely by sync_ical_link()
    # rather than becoming a Booking. Exists because a real feed's VEVENTs aren't all real
    # bookings: Airbnb includes host-set "Not Available" blocks, Vrbo includes "Tentative" entries
    # that are enquiries rather than confirmed stays - neither was being filtered before this field
    # existed, since sync_ical_link() previously only ever read UID/DTSTART/DTEND, never SUMMARY.
    # Deliberately just substring-contains (PIMS' own simplest filter type) rather than also
    # offering PIMS' date-range/duration filter types - nothing has needed those yet.
    exclude_summary_contains = models.TextField(
        blank=True, default='',
        help_text="One term per line. A calendar event whose title contains any of these (case-insensitive) is skipped entirely - e.g. 'Not Available' for Airbnb blocks, 'Tentative' for Vrbo enquiries.",
    )

    class Meta:
        db_table = 'property_ical_links'
        verbose_name = 'Property iCal Link'
        verbose_name_plural = 'Property iCal Links'

    def __str__(self):
        return f"{self.property.title} - iCal"

    def excluded_summary_terms(self):
        """exclude_summary_contains split into a clean list of terms, one per non-blank line -
        sync_ical_link()'s only consumer, but also usable by the property_detail form to redisplay
        the raw text."""
        return [line.strip() for line in self.exclude_summary_contains.splitlines() if line.strip()]