from django.contrib import admin

# Register your models here.
from .models import Property, Price, Location

admin.site.register(Property)
admin.site.register(Price)
admin.site.register(Location)