from django.db import models
from django.utils.translation import gettext_lazy as _

from .base import UserBaseACL

__all__ = ['ConnectMethodACL']


class ConnectMethodACL(UserBaseACL):
    connect_methods = models.JSONField(default=list, verbose_name=_('Connect methods'))
    applet_hosts = models.JSONField(default=list, blank=True, verbose_name=_('Applet hosts'))

    class Meta(UserBaseACL.Meta):
        verbose_name = _('Connect method acl')
        abstract = False

    @classmethod
    def filter_applet_hosts(cls, user, applet_name, hosts):
        """Return the hosts of `hosts` that `user` may use for applet `applet_name`.

        Only rules with non-empty `applet_hosts` and `applet_name` in `connect_methods`
        are considered. accept rules form an allowlist (when any exists); reject rules
        form a deny set, minus the allowlist (accept wins).
        """
        hosts = list(hosts)
        rules = [
            acl for acl in cls.get_user_acls(user)
            if acl.applet_hosts and applet_name in (acl.connect_methods or [])
        ]
        if not rules:
            return hosts

        allow_ids = None
        deny_ids = set()
        for acl in rules:
            ids = {str(i) for i in acl.applet_hosts}
            if acl.action == cls.ActionChoices.accept:
                allow_ids = (allow_ids or set()) | ids
            elif acl.action == cls.ActionChoices.reject:
                deny_ids |= ids
        deny_ids -= (allow_ids or set())

        return [
            h for h in hosts
            if (allow_ids is None or str(h.id) in allow_ids) and str(h.id) not in deny_ids
        ]
