from django.db import migrations

# Property's old hardcoded field name -> the exact Platform.name it corresponds to (seeded by
# 0036_seed_platforms, same three platforms).
OLD_FIELD_TO_PLATFORM_NAME = {
    'airbnb_id': 'Airbnb',
    'booking_com_id': 'Booking.com',
    'vrbo_id': 'Vrbo',
}


def backfill(apps, schema_editor):
    Property = apps.get_model('properties', 'Property')
    Platform = apps.get_model('properties', 'Platform')
    PropertyPlatformID = apps.get_model('properties', 'PropertyPlatformID')

    platforms_by_name = {p.name: p for p in Platform.objects.filter(name__in=OLD_FIELD_TO_PLATFORM_NAME.values())}

    for property in Property.objects.all():
        for field_name, platform_name in OLD_FIELD_TO_PLATFORM_NAME.items():
            value = (getattr(property, field_name) or '').strip()
            platform = platforms_by_name.get(platform_name)
            if value and platform:
                PropertyPlatformID.objects.get_or_create(
                    property=property, platform=platform, defaults={'listing_id': value},
                )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0038_create_propertyplatformid'),
    ]

    operations = [
        migrations.RunPython(backfill, noop_reverse),
    ]
