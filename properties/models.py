from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
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

class Manager(models.Model):
    """Property management company information."""
    company = models.CharField(max_length=200)
    head_name = models.CharField(max_length=200)
    head_email = models.EmailField()
    head_phone = models.CharField(max_length=50)
    maintenance_name = models.CharField(max_length=200)
    maintenance_phone = models.CharField(max_length=50)
    maintenance_email = models.EmailField()
    liaison_name = models.CharField(max_length=200)
    liaison_phone = models.CharField(max_length=50)
    liaison_email = models.EmailField()
    cleaning_name = models.CharField(max_length=200)
    cleaning_phone = models.CharField(max_length=50)
    cleaning_email = models.EmailField()
    finance_name = models.CharField(max_length=200, default="N/A")
    finance_phone = models.CharField(max_length=50, default="N/A")
    finance_email = models.EmailField(default="N/A")

    class Meta:
        db_table = 'property_managers'
        verbose_name = 'Property Manager'
        verbose_name_plural = 'Property Managers'

    def __str__(self):
        return f"{self.company} - {self.head_name}"


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
    manager = models.ForeignKey(Manager, on_delete=models.SET_NULL, null=True)
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True)
    accountant = models.ForeignKey(Accountant, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Property details
    al_number = models.IntegerField(blank=True, null=True)
    we_book = models.BooleanField(default=False)
    we_clean = models.BooleanField(default=False)
    booking_com_id = models.CharField(max_length=200, blank=True, null=True)
    airbnb_id = models.CharField(max_length=200, blank=True, null=True)
    vrbo_id = models.CharField(max_length=200, blank=True, null=True)
    standard_cleaning_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    @property
    def slug(self):
        return self.short_title.lower().replace(" ", "-")

    class Meta:
        db_table = 'properties'
        verbose_name = 'Property'
        verbose_name_plural = 'Properties'

    def __str__(self):
        return pretty_title(self.title)


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
    name = models.CharField(max_length=100)
    start_date = models.DateField()
    end_date = models.DateField()
    rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    weekly_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Discount applied to stays of 7 or more nights, as a percentage."
    )
    last_minute_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Discount applied when booking within the last-minute window, as a percentage."
    )
    last_minute_discount_days = models.PositiveIntegerField(
        default=7,
        help_text="Number of days before arrival within which the last-minute discount applies."
    )
    monthly_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Discount applied to stays of at least monthly_discount_min_nights nights, as a percentage."
    )
    monthly_discount_min_nights = models.PositiveIntegerField(
        default=28,
        help_text="Minimum number of nights a stay must be for the monthly discount to apply."
    )

    class Meta:
        db_table = 'property_prices'
        verbose_name = 'Property Price'
        verbose_name_plural = 'Property Prices'

    def __str__(self):
        return f"{self.property.title} - {self.name} - {self.start_date} to {self.end_date}"


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


class iCalLink(models.Model):
    """Property iCal links for calendar synchronization."""

    class Source(models.TextChoices):
        AIRBNB = 'airbnb', 'Airbnb'
        BOOKING_COM = 'booking.com', 'Booking.com'
        VRBO = 'vrbo', 'Vrbo'

    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name='ical_links')
    ical_source = models.CharField(max_length=20, choices=Source.choices, blank=True, null=True)
    ical_url = models.URLField(blank=True, null=True)
    last_synced = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'property_ical_links'
        verbose_name = 'Property iCal Link'
        verbose_name_plural = 'Property iCal Links'

    def __str__(self):
        return f"{self.property.title} - iCal"