import common.db.fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_alter_vault_secret_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='account',
            name='mfa_secret',
            field=common.db.fields.EncryptTextField(blank=True, null=True, verbose_name='MFA secret'),
        ),
    ]
