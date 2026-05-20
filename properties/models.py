from django.db import models
from django.db.models.enums import Choices


class Location(models.Model):
    """Property location information."""
    title = models.CharField(max_length=100)
    street = models.CharField(max_length=200)
    city = models.CharField(max_length=100)
    coordinates = models.CharField(max_length=100)
    map_link = models.URLField()
    directions = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    zip_code = models.CharField(max_length=20)
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
        return self.title
    

class Manager(models.Model):
    """Property management company information."""
    company = models.CharField(max_length=200)
    head_name = models.CharField(max_length=200)
    head_email = models.EmailField(unique=True)
    head_phone = models.CharField(max_length=50, unique=True)
    maintenance = models.CharField(max_length=200)
    maintenance_phone = models.CharField(max_length=50)
    maintenance_email = models.EmailField()
    liaison_name = models.CharField(max_length=200)
    liaison_phone = models.CharField(max_length=50)
    liaison_email = models.EmailField()
    cleaning_name = models.CharField(max_length=200)
    cleaning_phone = models.CharField(max_length=50)
    cleaning_email = models.EmailField()

    class Meta:
        db_table = 'property_managers'
        verbose_name = 'Property Manager'
        verbose_name_plural = 'Property Managers'

    def __str__(self):
        return f"{self.company} - {self.name}"


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
    wants_accounting = models.BooleanField()
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


class Price(models.Model):
    """Property pricing information by year."""
    year = models.IntegerField()
    name = models.CharField(max_length=200, default='Undefined Price List')
    
    # Monthly rates
    january = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    february = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    march = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    april = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    may = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    june = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    july = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    august = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    september = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    october = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    november = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    december = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    # Special rates
    festive = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    early_winter_monthly_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    late_winter_monthly_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        db_table = 'property_prices'
        verbose_name = 'Property Price'
        verbose_name_plural = 'Property Prices'

    def __str__(self):
        return f"{self.name} - {self.year}"


class Property(models.Model):
    """Main property model."""
    title = models.CharField(max_length=200, unique=True, blank=False)
    short_title = models.CharField(max_length=100, unique=True, blank=False)
    
    # Foreign key relationships
    owner = models.ForeignKey(Owner, on_delete=models.SET_NULL, null=True)
    manager = models.ForeignKey(Manager, on_delete=models.SET_NULL, null=True)
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True)
    price = models.ForeignKey(Price, on_delete=models.SET_NULL, null=True, blank=True)
    accountant = models.ForeignKey(Accountant, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Property details
    al_number = models.IntegerField(blank=True, null=True)
    we_book = models.BooleanField(default=False)
    booking_com_title = models.CharField(max_length=200, blank=True, null=True)
    airbnb_title = models.CharField(max_length=200, blank=True, null=True)
    we_clean = models.BooleanField(default=False)
    standard_cleaning_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    send_owner_booking_forms = models.BooleanField(default=False)

    class Meta:
        db_table = 'properties'
        verbose_name = 'Property'
        verbose_name_plural = 'Properties'

    def __str__(self):
        return self.title


class Spec(models.Model):
    """Property specifications and features."""
    property = models.OneToOneField(Property, on_delete=models.CASCADE, related_name='specs')
    is_listed = models.BooleanField()
    is_sea_view = models.BooleanField()
    is_upper_floor = models.BooleanField()
    is_beachfront = models.BooleanField()
    bedrooms = models.IntegerField()
    bathrooms = models.IntegerField()
    square_metres = models.IntegerField()
    minimum_nights = models.IntegerField(default=4)
    max_adults = models.IntegerField(default=4)
    max_guests = models.IntegerField(default=4)
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
    double_bed = models.BooleanField(default=True)
    single_beds = models.IntegerField(default=0)
    bed_size = models.TextField(default='160 x 200')
    washing_machine = models.BooleanField(default=True)
    dryer = models.BooleanField(default=False)
    iron = models.BooleanField(default=True)
    ironing_board = models.BooleanField(default=True)
    wifi = models.BooleanField(default=True)
    tv = models.BooleanField(default=True)
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

    class Meta:
        db_table = 'property_amenities'
        verbose_name = 'Property Amenity'
        verbose_name_plural = 'Property Amenities'

    def __str__(self):
        return f"{self.property.title} - {self.name}"


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