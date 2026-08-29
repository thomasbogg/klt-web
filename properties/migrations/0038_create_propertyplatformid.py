import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0037_remove_icallink_ical_source'),
    ]

    operations = [
        migrations.CreateModel(
            name='PropertyPlatformID',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('listing_id', models.CharField(max_length=200)),
                ('platform', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='property_ids', to='properties.platform')),
                ('property', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='platform_ids', to='properties.property')),
            ],
            options={
                'verbose_name': 'Property Platform ID',
                'verbose_name_plural': 'Property Platform IDs',
                'db_table': 'property_platform_ids',
                'unique_together': {('property', 'platform')},
            },
        ),
    ]
