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
def on_session_created(sender, instance: Session, created, **kwargs):
    if not created:
        return
    claim_applet_account_lock(instance)


@receiver(post_save, sender=Session)
def on_session_finished(sender, instance: Session, created, **kwargs):
    if not instance.is_finished:
        return
    # Clean up cached data that may exist because the task didn't run
    Session.unlock_session(instance.id)
    # instance.account_id/asset_id are the RemoteApp's own virtual account/asset
    # (e.g. "SemUser"/"Google") - NOT the real pooled OS account or AppletHost VM,
    # which only released_host_account below actually knows about.
    released = release_applet_account_lock(instance)
    if released:
        host_id, account_id = released
        # Don't rely on xrdp/xorgxrdp's own disconnect-timeout to free the OS
        # session - live testing showed xrdp-sesman can keep believing a
        # session is still alive (and reconnect new logins into it, black
        # screen, nothing actually running) well after the underlying Xorg
        # process already exited on its own. Force it closed right now
        # instead, so the account is guaranteed clean before anyone connects
        # to it again.
        kill_applet_host_session_processes.delay(account_id, host_id)


def claim_applet_account_lock(session):
    """
    Claims exactly one pending applet-account lock for this brand-new
    session, if one is pending for (user_id, asset_id) - i.e. this is a
    RemoteApp session and Applet.select_host_account() locked a pooled
    account for it moments ago, in the same connect request that led here
    (see applet.py). The claim is stored keyed by this session's own id, so
    release_applet_account_lock() can look it up directly and exactly later
    instead of a (user_id, asset_id) search - which can't tell two
    concurrent sessions to the same asset apart (confirmed live: closing one
    of two concurrent sessions to the same asset was releasing, and killing
    the OS processes of, both).
    """
    if not session.user_id or not session.asset_id:
        return
    idx_pattern = Applet.account_lock_idx_key_tmpl.format(session.user_id, session.asset_id, '*')
    idx_keys = cache.keys(idx_pattern) or []
    for idx_key in idx_keys:
        entry = cache.get(idx_key)
        # cache.delete() reports whether it actually removed something -
        # Redis DEL is atomic, so exactly one concurrent claimer can ever see
        # True for a given idx_key, even if two sessions are created at once.
        if entry and cache.delete(idx_key):
            cache.set(Applet.session_claim_key_tmpl.format(session.id), entry, 60 * 60 * 24)
            return


def release_applet_account_lock(session):
    """
    Releases the pooled-account lock claimed by claim_applet_account_lock()
    for this exact session, if any. Returns (host_id, account_id) if this
    was an applet session, None otherwise.
    """
    claim_key = Applet.session_claim_key_tmpl.format(session.id)
    entry = cache.get(claim_key)
    if not entry:
        return None
    cache.delete(entry['lock_key'])
    cache.delete(claim_key)
    return entry['host_id'], entry['account_id']
