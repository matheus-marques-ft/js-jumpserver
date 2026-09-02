from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.db import fields
from orgs.mixins.models import JMSOrgBaseModel

__all__ = ['Secret']


class Secret(JMSOrgBaseModel):
    source = models.CharField(max_length=128, default='', blank=True, verbose_name=_('Source'))
    name = models.CharField(max_length=128, verbose_name=_('Name'), db_index=True)
    value = fields.EncryptTextField(blank=True, null=True, verbose_name=_('Value'))
    expiration_date = models.DateTimeField(null=True, blank=True, verbose_name=_('Expiration date'))
    is_active = models.BooleanField(default=True, verbose_name=_('Active'))

    class Meta:
        verbose_name = _('Secret')
        unique_together = [('name', 'org_id')]
        ordering = ('-date_created',)
        permissions = [
            ('view_secretvalue', _('Can view secret value')),
        ]

    @property
    def is_expired(self) -> bool:
        if not self.expiration_date:
            return False
        return self.expiration_date < timezone.now()

    def __str__(self):
        return self.name
