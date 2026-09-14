from datetime import time

from django.db import migrations


# Preserves today's actual behavior for every existing row (see Arrival.time_unknown's own
# docstring) - every row already using the time(0,0) sentinel gets flagged so
# compute_eta_from_given_time() keeps treating it as "no time known", exactly as it already does
# today. Only a future save (which explicitly resets this flag to False) can make a genuine
# midnight time distinguishable going forward.
def backfill_time_unknown(apps, schema_editor):
    Arrival = apps.get_model('bookings', 'Arrival')
    Arrival.objects.filter(time=time(0, 0)).update(time_unknown=True)


def unset_time_unknown(apps, schema_editor):
    Arrival = apps.get_model('bookings', 'Arrival')
    Arrival.objects.filter(time=time(0, 0), time_unknown=True).update(time_unknown=False)


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0063_arrival_time_unknown'),
    ]

    operations = [
        migrations.RunPython(backfill_time_unknown, unset_time_unknown),
    ]
