from django.core.cache import cache
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from django.utils import timezone

from accounts.models import Account
from assets.models import Asset
from common.decorators import merge_delay_run
from terminal.models import Applet, Session


@merge_delay_run(ttl=5)
def update_session_last_login_date(login_infos=()):
    account_ids = set()
    asset_ids = set()

    for account_id, asset_id in login_infos:
        if account_id:
            account_ids.add(account_id)
        if asset_id:
            asset_ids.add(asset_id)

    now = timezone.now()
    if account_ids:
        Account.objects.filter(pk__in=account_ids).update(date_last_login=now)
    if asset_ids:
        Asset.objects.filter(pk__in=asset_ids).update(date_last_login=now)


@receiver(pre_save, sender=Session)
def on_session_pre_save(sender, instance, **kwargs):
    if instance.need_update_cmd_amount:
        instance.cmd_amount = instance.compute_command_amount()

    account = instance.account_obj
    account_id = account.pk if account else None
    asset_id = instance.asset_id if instance.asset_id and instance.is_success else None
    if account_id or asset_id:
        update_session_last_login_date.delay(
            login_infos=((account_id, asset_id),)
        )


@receiver(post_save, sender=Session)
def on_session_finished(sender, instance: Session, created, **kwargs):
    if not instance.is_finished:
        return
    # Clean up cached data that may exist because the task didn't run
    Session.unlock_session(instance.id)
    release_applet_account_locks(instance)


def release_applet_account_locks(session):
    # Applet.select_host_account() locks a pooled host account for 24h
    # (ttl-only, see accounts_using_key_tmpl) so it isn't handed out to
    # another connection concurrently. Fires here too instead of only
    # relying on that ttl, so the account frees up as soon as the session
    # actually ends - on timeout (SECURITY_MAX_IDLE_TIME/session cleanup) or
    # the user closing the tab - instead of staying locked for up to 24h
    # after a session that may have lasted seconds.
    if not session.user_id or not session.asset_id:
        return
    idx_pattern = Applet.account_lock_idx_key_tmpl.format(session.user_id, session.asset_id, '*')
    idx_keys = cache.keys(idx_pattern) or []
    if not idx_keys:
        return
    lock_keys = [key for key in cache.get_many(idx_keys).values() if key]
    if lock_keys:
        cache.delete_many(lock_keys)
    cache.delete_many(idx_keys)
