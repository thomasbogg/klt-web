from django.db import models
from django.db.models.enums import Choices
from properties.utils import pretty_title


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

    class Meta:
        db_table = 'property_locations'
        verbose_name = 'Property Location'
        verbose_name_plural = 'Property Locations'

    def __str__(self):
        return pretty_title(self.title)
    

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

    class Meta:
        db_table = 'properties'
        verbose_name = 'Property'
        verbose_name_plural = 'Properties'

    def __str__(self):
        return pretty_title(self.title)


class Price(models.Model):
    """Property pricing information by year."""
    property = models.ForeignKey('Property', on_delete=models.CASCADE, related_name='prices')
    name = models.CharField(max_length=100)
    start_date = models.DateField()
    end_date = models.DateField()
    rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_special_rate = models.BooleanField(default=False)
    
    class Meta:
        db_table = 'property_prices'
        verbose_name = 'Property Price'
        verbose_name_plural = 'Property Prices'

    def __str__(self):
        return f"{self.property.title} - {self.name} - {self.start_date} to {self.end_date}"


class Spec(models.Model):
    """Property specifications and features."""
    property = models.OneToOneField(Property, on_delete=models.CASCADE, related_name='specs')
    is_sea_view = models.BooleanField()
    is_pool_view = models.BooleanField()
    is_upper_floor = models.BooleanField()
    is_beachfront = models.BooleanField()
    bedrooms = models.IntegerField()
    bathrooms = models.IntegerField()
    half_bathrooms = models.IntegerField()
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
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name='amenities')
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
    property = models.OneToOneField(Property, on_delete=models.CASCADE, related_name='ical_links')
    ical_source = models.CharField(max_length=200, blank=True, null=True)
    ical_url = models.URLField(blank=True, null=True)
    last_synced = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'property_ical_links'
        verbose_name = 'Property iCal Link'
        verbose_name_plural = 'Property iCal Links'

    def __str__(self):
        return f"{self.property.title} - iCal"