# Generated manually on 2026-09-02

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('terminal', '0011_endpoint_magnus_port'),
    ]

    operations = [
        migrations.CreateModel(
            name='AppletCookieStore',
            fields=[
                ('created_by', models.CharField(blank=True, max_length=128, null=True, verbose_name='Created by')),
                ('updated_by', models.CharField(blank=True, max_length=128, null=True, verbose_name='Updated by')),
                ('date_created', models.DateTimeField(auto_now_add=True, null=True, verbose_name='Date created')),
                ('date_updated', models.DateTimeField(auto_now=True, verbose_name='Date updated')),
                ('comment', models.TextField(blank=True, default='', verbose_name='Comment')),
                ('id', models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ('asset_key', models.CharField(db_index=True, max_length=255, unique=True, verbose_name='Asset key')),
                ('cookies', models.JSONField(default=list, verbose_name='Cookies')),
            ],
            options={
                'verbose_name': 'Applet cookie store',
            },
        ),
    ]
