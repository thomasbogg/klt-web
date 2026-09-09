from django.db import migrations, models


def split_comma_joined_emails(apps, schema_editor):
    """One-off, 2026-09-09 per Thomas: 7 real owners had two people's email addresses jammed into
    the single `email` field as 'first@x.com, second@y.com' - found live when staff couldn't save
    unrelated changes to those rows (blocked by both the browser's native email input validation
    and StaffSettingsView._update_owner's own full_clean() call). Splits on the FIRST comma only
    (none of the 7 had more than two addresses) - email becomes the first address, secondary_email
    the second, both trimmed. Logged so it's easy to double check against the pre-migration values
    if anything looks off afterward."""
    Owner = apps.get_model('properties', 'Owner')
    split_names = []
    for owner in Owner.objects.filter(email__contains=','):
        first, _, second = owner.email.partition(',')
        split_names.append((owner.name, owner.email))
        owner.email = first.strip()
        owner.secondary_email = second.strip()
        owner.save(update_fields=['email', 'secondary_email'])
    if split_names:
        print(f"split_comma_joined_emails: {len(split_names)} owner(s) split: {split_names}")


def rejoin_comma_joined_emails(apps, schema_editor):
    """Lossless reverse of split_comma_joined_emails - only touches rows secondary_email actually
    populated, so an owner who genuinely just has one email (added after this migration ran) is
    left untouched rather than gaining a trailing comma."""
    Owner = apps.get_model('properties', 'Owner')
    for owner in Owner.objects.exclude(secondary_email__isnull=True).exclude(secondary_email=''):
        owner.email = f"{owner.email}, {owner.secondary_email}"
        owner.secondary_email = None
        owner.save(update_fields=['email', 'secondary_email'])


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0059_restore_owner_invoicing_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='owner',
            name='secondary_email',
            field=models.EmailField(blank=True, max_length=254, null=True, unique=True),
        ),
        migrations.RunPython(split_comma_joined_emails, rejoin_comma_joined_emails),
    ]
