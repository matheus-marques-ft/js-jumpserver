from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('acls', '0004_clipboardacl'),
    ]

    operations = [
        migrations.AddField(
            model_name='connectmethodacl',
            name='applet_hosts',
            field=models.JSONField(blank=True, default=list, verbose_name='Applet hosts'),
        ),
    ]
