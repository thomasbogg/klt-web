from django.contrib import admin

# Register your models here.
from .models import (
    Property, Price, Location, Owner, Manager, Accountant,
    Amenity, PropertySpec, SEFDetail, iCalLink, PropertyImage,
    LocationImage, LocationSpec, LocationRules,
)


class SpecInline(admin.StackedInline):
    model = PropertySpec
    max_num = 1


class AmenityInline(admin.StackedInline):
    model = Amenity
    max_num = 1


class SEFDetailInline(admin.StackedInline):
    model = SEFDetail
    max_num = 1


class iCalLinkInline(admin.TabularInline):
    model = iCalLink
    extra = 1


class PropertyImageInline(admin.TabularInline):
    model = PropertyImage
    extra = 1


class LocationSpecInline(admin.StackedInline):
    model = LocationSpec
    max_num = 1


class LocationImageInline(admin.TabularInline):
    model = LocationImage
    extra = 1


class LocationRulesInline(admin.StackedInline):
    model = LocationRules
    max_num = 1


class PropertyAdmin(admin.ModelAdmin):
    inlines = [SpecInline, AmenityInline, SEFDetailInline, iCalLinkInline, PropertyImageInline]


class LocationAdmin(admin.ModelAdmin):
    inlines = [LocationSpecInline, LocationImageInline, LocationRulesInline]


admin.site.register(Property, PropertyAdmin)
admin.site.register(Price)
admin.site.register(Location, LocationAdmin)
admin.site.register(Owner)
admin.site.register(Manager)
admin.site.register(Accountant)