from django.db import migrations, models


def currency_for(takes_euros, takes_pounds):
    """The actual EUR/GBP/BOTH mapping, factored out of populate_currency below so it can be unit
    tested directly (properties/tests.py::PopulateOwnerCurrencyMigrationTests) without needing a
    historical-apps Owner row that has both the old boolean fields and the new currency field at
    once - by the time this repo's *current* Owner model exists, takes_euros/takes_pounds are
    already gone, so a normal Owner.objects.create() can't reproduce this migration's own
    mid-flight schema state the way properties/migrations/0020_propertyownership's own
    backfill-migration test could. EUR is the fallback for "neither set" - a genuine data gap
    (one live row as of 2026-09-08), not a guess dressed up as one."""
    if takes_euros and takes_pounds:
        return 'BOTH'
    if takes_pounds:
        return 'GBP'
    return 'EUR'


def populate_currency(apps, schema_editor):
    """Derives every Owner's currency from the takes_euros/takes_pounds pair being retired in this
    same migration - see currency_for() above for the actual mapping. Owners with neither flag set
    are logged so it's easy to find and confirm with the owner directly if it matters."""
    Owner = apps.get_model('properties', 'Owner')
    fallback_names = []
    for owner in Owner.objects.all():
        owner.currency = currency_for(owner.takes_euros, owner.takes_pounds)
        if not owner.takes_euros and not owner.takes_pounds:
            fallback_names.append(owner.name)
        owner.save(update_fields=['currency'])
    if fallback_names:
        print(f"populate_currency: defaulted to EUR for owner(s) with neither flag set: {fallback_names}")


def reverse_populate_currency(apps, schema_editor):
    """No-op: takes_euros/takes_pounds are re-added (as non-null booleans with no default) by the
    RemoveField operations' own reverse, but this migration has no way to know what they used to
    be once currency has collapsed them into one value - BOTH in particular is lossless forward
    but ambiguous backward. Reversing this migration already requires re-populating those two
    columns by hand regardless of what this function does, so it deliberately leaves them as
    Django's own post-AddField default (False/False) rather than guessing."""


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0055_property_active'),
    ]

    operations = [
        migrations.AddField(
            model_name='owner',
            name='currency',
            field=models.CharField(
                choices=[('EUR', 'Euros'), ('GBP', 'Pounds'), ('BOTH', 'Both')], max_length=4, default='EUR',
            ),
            preserve_default=False,
        ),
        migrations.RunPython(populate_currency, reverse_populate_currency),
        migrations.RemoveField(model_name='owner', name='takes_euros'),
        migrations.RemoveField(model_name='owner', name='takes_pounds'),
        migrations.RemoveField(model_name='owner', name='default_clean'),
        migrations.RemoveField(model_name='owner', name='default_meet_greet'),
        migrations.RemoveField(model_name='owner', name='cleans_are_invoiced'),
        migrations.RemoveField(model_name='owner', name='rental_commissions_are_invoiced'),
    ]
