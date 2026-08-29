from django.db import migrations

# Matches PLATFORM_NAMES_BY_ICAL_SOURCE (bookings/utils.py) and the old iCalLink.Source choices
# exactly, so every existing iCalLink's platform resolves to the same platform its ical_source
# already meant.
ICAL_SOURCE_TO_PLATFORM_NAME = {
    'airbnb': 'Airbnb',
    'booking.com': 'Booking.com',
    'vrbo': 'Vrbo',
}


def seed_and_backfill(apps, schema_editor):
    Platform = apps.get_model('properties', 'Platform')
    iCalLink = apps.get_model('properties', 'iCalLink')

    platforms_by_name = {}
    for name in ICAL_SOURCE_TO_PLATFORM_NAME.values():
        platforms_by_name[name], _ = Platform.objects.get_or_create(name=name)

    for link in iCalLink.objects.exclude(ical_source__isnull=True).exclude(ical_source=''):
        platform_name = ICAL_SOURCE_TO_PLATFORM_NAME.get(link.ical_source)
        if platform_name:
            link.platform = platforms_by_name[platform_name]
            link.save(update_fields=['platform'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0035_platform_model'),
    ]

    operations = [
        migrations.RunPython(seed_and_backfill, noop_reverse),
    ]
