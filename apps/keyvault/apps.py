from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class KeyVaultConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'keyvault'
    verbose_name = _('App Key vault')
