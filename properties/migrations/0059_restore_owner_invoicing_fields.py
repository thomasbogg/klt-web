from django.db import migrations, models


class Migration(migrations.Migration):
    """cleans_are_invoiced/sage_contact_id restored+added to Owner 2026-09-09, per Thomas -
    cleans_are_invoiced existed from the original klt_main.db migration and was only removed the
    day before (0056_owner_currency_and_field_cleanup) as dead weight; it's now genuinely needed
    as the per-owner gate for the Sage One cleaning/meet-greet invoicing integration
    (finance/services.py::dispatch_memo_to_sage). Its true historical per-owner value was real
    data, but that data no longer exists anywhere in this database once 0056 dropped the column -
    defaulting every existing owner to False (not invoiced) on this one-off backfill is the
    conservative choice (never silently starts invoicing someone who wasn't set up for it), not a
    guess dressed up as a fact. Thomas needs to review and set the real value per owner via the
    now-restored Owners settings checkbox (staff/utils.py::OWNER_BOOLEAN_FIELDS) before this
    feature is relied on for any given owner.

    rental_commissions_are_invoiced was restored alongside cleans_are_invoiced in an earlier draft
    of this same migration, then dropped again the same session (per Thomas: rental commission is
    always invoiced now, no per-owner opt-out exists) - never actually applied, so there's nothing
    to reverse here, just no AddField for it."""

    dependencies = [
        ('properties', '0058_merge_20260908_0326'),
    ]

    operations = [
        migrations.AddField(
            model_name='owner',
            name='cleans_are_invoiced',
            field=models.BooleanField(default=False),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='owner',
            name='sage_contact_id',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
    ]
