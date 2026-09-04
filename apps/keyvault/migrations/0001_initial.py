import uuid

from django.db import migrations, models

import common.db.fields


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Secret',
            fields=[
                ('created_by', models.CharField(blank=True, max_length=128, null=True, verbose_name='Created by')),
                ('updated_by', models.CharField(blank=True, max_length=128, null=True, verbose_name='Updated by')),
                ('date_created', models.DateTimeField(auto_now_add=True, null=True, verbose_name='Date created')),
                ('date_updated', models.DateTimeField(auto_now=True, verbose_name='Date updated')),
                ('comment', models.TextField(blank=True, default='', verbose_name='Comment')),
                ('id', models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ('org_id', models.CharField(blank=True, db_index=True, default='', max_length=36, verbose_name='Organization')),
                ('source', models.CharField(max_length=128, verbose_name='Source')),
                ('name', models.CharField(db_index=True, max_length=128, verbose_name='Name')),
                ('value', common.db.fields.EncryptTextField(blank=True, null=True, verbose_name='Value')),
                ('expiration_date', models.DateTimeField(blank=True, null=True, verbose_name='Expiration date')),
                ('is_active', models.BooleanField(default=True, verbose_name='Active')),
            ],
            options={
                'verbose_name': 'Secret',
                'ordering': ('-date_created',),
                'permissions': [('view_secretvalue', 'Can view secret value')],
                'unique_together': {('name', 'org_id')},
            },
        ),
    ]
