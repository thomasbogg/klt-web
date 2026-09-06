from django.db import migrations

# 2026-09-06, per Thomas: company policy change - stop collecting new security deposits for the
# time being. This is the actual live pause (the field's own default stays True so a fresh/test
# DB is unaffected - see security_deposits_enabled's help_text, bookings/models.py). Reversing
# this migration is deliberately a no-op: turning deposits back on is a staff-settings checkbox,
# not a migration rollback.


def pause_deposits(apps, schema_editor):
    BookingSettings = apps.get_model('bookings', 'BookingSettings')
    BookingSettings.objects.filter(pk=1).update(security_deposits_enabled=False)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0051_bookingsettings_security_deposits_enabled'),
    ]

    operations = [
        migrations.RunPython(pause_deposits, noop_reverse),
    ]
