from django.core.cache import cache
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from django.utils import timezone

from accounts.models import Account
from assets.models import Asset
from common.decorators import merge_delay_run
from terminal.models import Applet, Session
from terminal.tasks import kill_applet_host_session_processes


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
    # instance.account_id/asset_id are the RemoteApp's own virtual account/asset
    # (e.g. "SemUser"/"Google") - NOT the real pooled OS account or AppletHost VM,
    # which only released_host_accounts below actually knows about. Passing the
    # instance ids straight to the kill task was a real bug: AppletHost lookup by
    # that asset_id always missed, so the task returned instantly without ever
    # attempting to kill anything (confirmed live - task "succeeded" in ~26ms with
    # the process still running on the host after).
    released_host_accounts = release_applet_account_locks(instance)
    for host_id, account_id in released_host_accounts:
        # Don't rely on xrdp/xorgxrdp's own disconnect-timeout to free the OS
        # session - live testing showed xrdp-sesman can keep believing a
        # session is still alive (and reconnect new logins into it, black
        # screen, nothing actually running) well after the underlying Xorg
        # process already exited on its own. Force it closed right now
        # instead, so the account is guaranteed clean before anyone connects
        # to it again.
        kill_applet_host_session_processes.delay(account_id, host_id)


def release_applet_account_locks(session):
    """
    Releases the pooled-account lock/cache entries for a finished applet
    session. Returns a list of (host_id, account_id) pairs actually released
    (empty if this session wasn't an applet session) - callers use this both
    to know whether there's applet-host-specific cleanup to do, and to get
    back to the real pooled OS account/AppletHost, which session.account_id/
    asset_id don't point at (see on_session_finished).
    """
    # Applet.select_host_account() locks a pooled host account for 24h
    # (ttl-only, see accounts_using_key_tmpl) so it isn't handed out to
    # another connection concurrently. Fires here too instead of only
    # relying on that ttl, so the account frees up as soon as the session
    # actually ends - on timeout (SECURITY_MAX_IDLE_TIME/session cleanup) or
    # the user closing the tab - instead of staying locked for up to 24h
    # after a session that may have lasted seconds.
    if not session.user_id or not session.asset_id:
        return []
    idx_pattern = Applet.account_lock_idx_key_tmpl.format(session.user_id, session.asset_id, '*')
    idx_keys = cache.keys(idx_pattern) or []
    if not idx_keys:
        return []
    entries = [v for v in cache.get_many(idx_keys).values() if v]
    lock_keys = [entry['lock_key'] for entry in entries]
    if lock_keys:
        cache.delete_many(lock_keys)
    cache.delete_many(idx_keys)
    return [(entry['host_id'], entry['account_id']) for entry in entries]
