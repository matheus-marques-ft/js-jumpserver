from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.db.models import JMSBaseModel
from common.utils import get_logger

logger = get_logger(__name__)

__all__ = ['AppletHostRouting', 'get_asset_routing', 'save_asset_routing', 'filter_hosts_by_routing']


class AppletHostRouting(JMSBaseModel):
    asset = models.OneToOneField(
        'assets.Asset', on_delete=models.CASCADE, related_name='applet_routing',
        verbose_name=_('Asset')
    )
    balance = models.BooleanField(default=True, verbose_name=_('Balance'))
    host = models.ForeignKey(
        'terminal.AppletHost', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+', verbose_name=_('Applet host')
    )
    excluded_hosts = models.ManyToManyField(
        'terminal.AppletHost', blank=True, related_name='+',
        verbose_name=_('Excluded applet hosts')
    )

    class Meta:
        verbose_name = _('Applet host routing')


def get_asset_routing(asset):
    try:
        return asset.applet_routing
    except ObjectDoesNotExist:
        return None
    except Exception as e:
        logger.warning(f'Get applet routing for asset {asset.id} failed: {e}')
        return None


def save_asset_routing(asset, balance, host_id, excluded_ids):
    from .host import AppletHost

    host_id = str(host_id) if host_id else None
    excluded_ids = [str(i) for i in (excluded_ids or [])]

    if not balance and not host_id:
        raise ValueError('applet host is required when balancing is disabled')
    if host_id and host_id in excluded_ids:
        raise ValueError('applet host cannot be both selected and excluded')

    # Estado padrao (balanceado, sem host, sem exclusoes) = sem linha: o ativo segue o
    # comportamento legado (labels AppletHostOnly/AppletHost continuam valendo).
    if balance and not excluded_ids:
        AppletHostRouting.objects.filter(asset=asset).delete()
        return None

    host = None
    if not balance:
        host = AppletHost.objects.filter(id=host_id).first()
        if host is None:
            raise ValueError('applet host not found')

    routing, created = AppletHostRouting.objects.update_or_create(
        asset=asset, defaults={'balance': bool(balance), 'host': host}
    )
    routing.excluded_hosts.set(AppletHost.objects.filter(id__in=excluded_ids))
    return routing


def filter_hosts_by_routing(routing, hosts):
    hosts = list(hosts)
    if routing is None:
        return hosts

    excluded = set(str(i) for i in routing.excluded_hosts.values_list('id', flat=True))
    candidates = [h for h in hosts if str(h.id) not in excluded]
    if not routing.balance:
        fixed_id = str(routing.host_id) if routing.host_id else None
        candidates = [h for h in candidates if str(h.id) == fixed_id]
    return candidates
