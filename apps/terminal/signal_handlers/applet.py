from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils.functional import LazyObject

from accounts.const import AliasAccount
from accounts.models import Account, VirtualAccount
from common.decorators import on_transaction_commit
from common.signals import django_ready
from common.utils import get_logger
from common.utils.connection import RedisPubSub
from orgs.utils import tmp_to_builtin_org
from users.models import User
from ..models import Applet, AppletHost
from ..tasks import applet_host_generate_accounts

logger = get_logger(__file__)


@receiver(post_save, sender=AppletHost)
@on_transaction_commit
def on_applet_host_create(sender, instance, created=False, **kwargs):
    if not created:
        return
    # Clear the previous preference on creation, to avoid always scheduling to the same one
    Applet.clear_host_prefer()
    applets = Applet.objects.all()
    instance.applets.set(applets)
    applet_host_change_pub_sub.publish(True)


@receiver(post_save, sender=AppletHost)
@on_transaction_commit
def on_applet_host_update_or_create(sender, instance, created=False, **kwargs):
    if instance.auto_create_accounts:
        applet_host_generate_accounts.delay(instance.id)

    # For those using the same-named account, just enable the login option for it
    if instance.using_same_account:
        alias = AliasAccount.USER.value
        same_account, __ = VirtualAccount.objects.get_or_create(
            alias=alias, defaults={'alias': alias, 'secret_from_login': True}
        )
        if same_account.secret_from_login:
            return
        same_account.secret_from_login = True
        same_account.save(update_fields=['secret_from_login'])


@receiver(post_delete, sender=User)
def on_user_delete_remove_account(sender, instance, **kwargs):
    account_username = 'js_{}'.format(instance.username)
    with tmp_to_builtin_org(system=1):
        applet_hosts = AppletHost.objects.all().values_list('id', flat=True)
        accounts = Account.objects.filter(asset_id__in=applet_hosts, username=account_username)
        accounts.delete()


@receiver(post_delete, sender=AppletHost)
def on_applet_host_delete(sender, instance, **kwargs):
    applet_host_change_pub_sub.publish(True)


@receiver(post_save, sender=Applet)
@on_transaction_commit
def on_applet_create(sender, instance, created=False, **kwargs):
    if not created:
        return
    # Mirrors on_applet_host_create's `instance.applets.set(...)` from the other
    # direction: that one only backfills publications for hosts that already
    # existed when a NEW host is created. Without this, a newly installed/uploaded
    # applet is never linked (AppletPublication) to any pre-existing AppletHost -
    # it just never shows up as an option to deploy anywhere, on any host type,
    # until that host happens to get recreated.
    for host in AppletHost.objects.all():
        host.applets.add(instance)
    applet_host_change_pub_sub.publish(True)


@receiver(post_delete, sender=Applet)
def on_applet_delete(sender, instance, **kwargs):
    applet_host_change_pub_sub.publish(True)


class AppletHostPubSub(LazyObject):
    def _setup(self):
        self._wrapped = RedisPubSub('fm.applet_host_change')


@receiver(django_ready)
def subscribe_applet_host_change(sender, **kwargs):
    logger.debug("Start subscribe for expire node assets id mapping from memory")

    def on_change(message):
        from terminal.connect_methods import ConnectMethodUtil
        ConnectMethodUtil.refresh_methods()

    applet_host_change_pub_sub.subscribe(on_change)


applet_host_change_pub_sub = AppletHostPubSub()
