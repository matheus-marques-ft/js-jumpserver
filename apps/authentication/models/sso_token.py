import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.db.models import BaseCreateUpdateModel, CASCADE_SIGNAL_SKIP


class SSOToken(BaseCreateUpdateModel):
    """
    Similar to Tencent Enterprise Mail's [single sign-on](https://exmail.qq.com/qy_mng_logic/doc#10036)
    For security reasons, the `token` here expires immediately after one use. However, we keep every `token` that has been generated.
    """
    authkey = models.UUIDField(primary_key=True, default=uuid.uuid4, verbose_name=_('Token'))
    expired = models.BooleanField(default=False, verbose_name=_('Expired'))
    user = models.ForeignKey('users.User', on_delete=CASCADE_SIGNAL_SKIP, verbose_name=_('User'),
                             db_constraint=False)

    class Meta:
        verbose_name = _('SSO token')
