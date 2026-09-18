from django.db import migrations

# Reworks guest_booking_confirmation's subject/body so it no longer says "confirmed" before any
# payment has actually been received (2026-09-18, per Thomas) - adds the pay_url link and states
# the payment_clearing_business_days hold window in plain terms. Same guarded-update pattern as
# 0004_multi_property_confirmation_copy.py: only touches the row if it still exactly matches what
# that migration last left it as - if Thomas has since edited this template via Settings > Emails,
# this migration leaves his copy alone rather than clobbering it.

ORIGINAL_GUEST_SUBJECT = 'Your booking at {{ property_name }} is confirmed - {{ reference }}'

ORIGINAL_GUEST_BODY = (
    'Your stay at {{ property_name }} from {{ arrival_date }} to {{ departure_date }} is confirmed.\n\n'
    '{% if is_multi_property %}This booking is linked to your other stay at {{ other_property_names }} - '
    'both apartments share this same reference.{% endif %}\n'
    '{% if amount_due_now %}Amount due now: {{ amount_due_now }} {{ amount_due_now_currency }}.{% endif %}\n'
    '{% if amount_due_balance %}A balance of {{ amount_due_balance }} {{ amount_due_balance_currency }} '
    'is due by {{ balance_due_date }}.{% endif %}\n\n'
    'You can view and manage your booking at any time here: {{ manage_hub_url }}\n\n'
    'Your booking reference is {{ reference }} - please keep this safe.'
)

NEW_GUEST_SUBJECT = 'Your reservation at {{ property_name }} - {{ reference }}'

NEW_GUEST_BODY = (
    'Your stay at {{ property_name }} from {{ arrival_date }} to {{ departure_date }} is reserved.\n\n'
    '{% if is_multi_property %}This booking is linked to your other stay at {{ other_property_names }} - '
    'both apartments share this same reference.{% endif %}\n'
    "{% if amount_due_now %}Your reservation will be confirmed once we receive your payment of "
    "{{ amount_due_now }} {{ amount_due_now_currency }}. If you haven't paid yet, you can do so securely "
    "here: {{ pay_url }}. Once your payment is on its way, we'll hold these dates for up to 3 working days "
    "while it clears - if we haven't received it within that time, the dates may be released again."
    "{% endif %}\n"
    '{% if amount_due_balance %}A balance of {{ amount_due_balance }} {{ amount_due_balance_currency }} '
    'is due by {{ balance_due_date }}.{% endif %}\n\n'
    'You can view and manage your booking at any time here: {{ manage_hub_url }}\n\n'
    'Your booking reference is {{ reference }} - please keep this safe.'
)


def update_pending_payment_copy(apps, schema_editor):
    EmailTemplate = apps.get_model('communications', 'EmailTemplate')
    EmailTemplate.objects.filter(
        slug='guest_booking_confirmation', subject=ORIGINAL_GUEST_SUBJECT, body=ORIGINAL_GUEST_BODY,
    ).update(subject=NEW_GUEST_SUBJECT, body=NEW_GUEST_BODY)


def revert_pending_payment_copy(apps, schema_editor):
    EmailTemplate = apps.get_model('communications', 'EmailTemplate')
    EmailTemplate.objects.filter(
        slug='guest_booking_confirmation', subject=NEW_GUEST_SUBJECT, body=NEW_GUEST_BODY,
    ).update(subject=ORIGINAL_GUEST_SUBJECT, body=ORIGINAL_GUEST_BODY)


class Migration(migrations.Migration):

    dependencies = [
        ('communications', '0005_seed_owner_informal_cleans_statement'),
    ]

    operations = [
        migrations.RunPython(update_pending_payment_copy, revert_pending_payment_copy),
    ]
