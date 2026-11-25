# Generated manually on 2026-11-25

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0024_remote_app_host_linux_platform'),
        ('terminal', '0012_appletcookiestore'),
    ]

    operations = [
        migrations.CreateModel(
            name='AppletHostRouting',
            fields=[
                ('created_by', models.CharField(blank=True, max_length=128, null=True, verbose_name='Created by')),
                ('updated_by', models.CharField(blank=True, max_length=128, null=True, verbose_name='Updated by')),
                ('date_created', models.DateTimeField(auto_now_add=True, null=True, verbose_name='Date created')),
                ('date_updated', models.DateTimeField(auto_now=True, verbose_name='Date updated')),
                ('comment', models.TextField(blank=True, default='', verbose_name='Comment')),
                ('id', models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ('balance', models.BooleanField(default=True, verbose_name='Balance')),
                ('asset', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='applet_routing', to='assets.asset', verbose_name='Asset')),
                ('host', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='terminal.applethost', verbose_name='Applet host')),
                ('excluded_hosts', models.ManyToManyField(blank=True, related_name='+', to='terminal.applethost', verbose_name='Excluded applet hosts')),
            ],
            options={
                'verbose_name': 'Applet host routing',
            },
        ),
    ]
