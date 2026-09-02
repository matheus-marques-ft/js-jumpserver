# -*- coding: utf-8 -*-
#

import datetime
import time
from itertools import chain

import paramiko
from celery import shared_task
from celery.utils.log import get_task_logger
from django.core.files.storage import default_storage
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.const import SecretType
from accounts.models import Account
from common.storage.replay import ReplayStorageHandler
from ops.celery.decorator import (
    register_as_period_task, after_app_ready_start)
from orgs.utils import tmp_to_builtin_org
from orgs.utils import tmp_to_root_org
from .backends import server_replay_storage
from .const import ReplayStorageType, CommandStorageType
from .models import (
    Status, Session, Task, AppletHostDeployment,
    AppletHost, ReplayStorage, CommandStorage
)
from .notifications import StorageConnectivityMessage

CACHE_REFRESH_INTERVAL = 10
RUNNING = False
logger = get_task_logger(__name__)


@shared_task(
    verbose_name=_('Periodic delete terminal status'),
    description=_("Unused")
)
@register_as_period_task(interval=3600)
@after_app_ready_start
def delete_terminal_status_period():
    yesterday = timezone.now() - datetime.timedelta(days=7)
    Status.objects.filter(date_created__lt=yesterday).delete()


@shared_task(
    verbose_name=_('Clean orphan session'),
    description=_(
        """Check every 10 minutes for asset connection sessions that have been inactive for 3 
        minutes and mark these sessions as completed"""
    )
)
@register_as_period_task(interval=600)
@after_app_ready_start
@tmp_to_root_org()
def clean_orphan_session():
    active_sessions = Session.objects.filter(is_finished=False)
    for session in active_sessions:
        # finished task
        Task.objects.filter(args=str(session.id), is_finished=False).update(
            is_finished=True, date_finished=timezone.now()
        )
        # finished session
        if session.is_active():
            continue
        session.is_finished = True
        session.date_end = timezone.now()
        session.save()


@shared_task(
    verbose_name=_('Upload session replay to external storage'),
    description=_(
        """If SERVER_REPLAY_STORAGE is configured in the config.txt, session commands and 
        recordings will be uploaded to external storage"""
    )
)
def upload_session_replay_to_external_storage(session_id):
    logger.info(f'Start upload session to external storage: {session_id}')
    session = Session.objects.filter(id=session_id).first()
    if not session:
        logger.error(f'Session db item not found: {session_id}')
        return

    replay_storage = ReplayStorageHandler(session)
    local_path, url = replay_storage.find_local()
    if not local_path:
        logger.error(f'Session replay not found, may be upload error: {local_path}')
        return

    abs_path = default_storage.path(local_path)
    remote_path = session.get_relative_path_by_local_path(abs_path)
    ok, err = server_replay_storage.upload(abs_path, remote_path)
    if not ok:
        logger.error(f'Session replay upload to external error: {err}')
        return

    try:
        default_storage.delete(local_path)
    except:
        pass
    return


@shared_task(
    verbose_name=_('Upload session replay part file to external storage'),
    description=_(
        """If SERVER_REPLAY_STORAGE is configured in the config.txt, session commands and 
        recordings will be uploaded to external storage"""
    ))
def upload_session_replay_file_to_external_storage(session_id, local_path, remote_path):
    abs_path = default_storage.path(local_path)
    ok, err = server_replay_storage.upload(abs_path, remote_path)
    if not ok:
        logger.error(f'Session replay file {local_path} upload to external error: {err}')
        return

    try:
        default_storage.delete(local_path)
    except:
        pass
    return



@shared_task(
    verbose_name=_('Run applet host deployment'),
    activity_callback=lambda self, did, *args, **kwargs: ([did],),
    description=_(
        """When deploying from the remote application publisher details page, and the 'Deploy' 
        button is clicked, this task will be executed"""
    )
)
def run_applet_host_deployment(did, install_applets):
    with tmp_to_builtin_org(system=1):
        deployment = AppletHostDeployment.objects.get(id=did)
        deployment.start(install_applets=install_applets)


@shared_task(
    verbose_name=_('Install applet'),
    activity_callback=lambda self, ids, applet_id, *args, **kwargs: (ids,),
    description=_(
        """When the 'Deploy' button is clicked in the 'Remote Application' section of the remote 
        application publisher details page, this task will be executed"""
    )
)
def run_applet_host_deployment_install_applet(ids, applet_id):
    with tmp_to_builtin_org(system=1):
        for did in ids:
            deployment = AppletHostDeployment.objects.get(id=did)
            deployment.install_applet(applet_id)


@shared_task(
    verbose_name=_('Uninstall applet'),
    activity_callback=lambda self, ids, applet_id, *args, **kwargs: (ids,),
    description=_(
        """When the 'Uninstall' button is clicked in the 'Remote Application' section of the 
        remote application publisher details page, this task will be executed"""
    )
)
def run_applet_host_deployment_uninstall_applet(ids, applet_id):
    with tmp_to_builtin_org(system=1):
        for did in ids:
            deployment = AppletHostDeployment.objects.get(id=did)
            deployment.uninstall_applet(applet_id)


@shared_task(
    queue='ansible',
    verbose_name=_('Generate applet host accounts'),
    activity_callback=lambda self, host_id, *args, **kwargs: ([host_id],),
    description=_(
        """When a remote publishing server is created and an account needs to be created
        automatically, this task will be executed. For Linux hosts, this also
        provisions the new accounts as OS users via Ansible, which is why it runs
        on the 'ansible' queue"""
    )
)
def applet_host_generate_accounts(host_id):
    applet_host = AppletHost.objects.filter(id=host_id).first()
    if not applet_host:
        return

    with tmp_to_builtin_org(system=1):
        applet_host.generate_accounts()


@shared_task(
    verbose_name=_('Kill applet host session processes'),
    description=_(
        """When a RemoteApp session on a Linux applet host (xrdp) finishes, this
        force-kills the pooled account's OS processes over SSH (the account
        killing its own processes, no root needed) instead of relying on
        xrdp/xorgxrdp's own disconnect-timeout - live testing showed that
        mechanism can leave xrdp-sesman thinking a session is still alive
        (and reconnecting new logins to it, no process behind it) well after
        the underlying Xorg process already exited on its own"""
    )
)
@tmp_to_root_org()
def kill_applet_host_session_processes(account_id, asset_id):
    applet_host = AppletHost.objects.filter(id=asset_id).first()
    if not applet_host or applet_host.platform.type != 'linux':
        return

    account = Account.objects.filter(id=account_id).first()
    if not account or not account.username.startswith(('frete_', 'js_', 'jms_')):
        return

    ip = applet_host.get_target_ip()
    port = applet_host.get_target_ssh_port()
    if not ip or not port:
        logger.error(f'Applet host {applet_host} has no reachable ssh address, skip kill')
        return

    connect_kwargs = {}
    if account.secret_type == SecretType.SSH_KEY:
        connect_kwargs['pkey'] = account.private_key_obj
    else:
        connect_kwargs['password'] = account.secret

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=ip, port=port, username=account.username,
            timeout=10, banner_timeout=10, auth_timeout=10, **connect_kwargs,
        )
        # Clean the account's own X11 socket first (it can only remove ones
        # it owns, no root needed) - order matters: the pkill below kills
        # this very shell too (it owns its own SSH session), so it has to run
        # last or the socket cleanup below it would never get a chance to run.
        client.exec_command(
            'for f in /tmp/.X11-unix/X*; do [ -O "$f" ] && rm -f "$f"; done; '
            'pkill -9 -u "$(whoami)"'
        )
        # Best-effort, fire-and-forget: give the remote command a moment to
        # actually run before closing the connection (and thus its channel).
        time.sleep(1)
    except Exception as e:
        logger.error(f'Failed to kill session processes for account {account} on {applet_host}: {e}')
    finally:
        client.close()


@shared_task(
    verbose_name=_('Check command replay storage connectivity'),
    description=_(
        """Check every day at midnight whether the external storage for commands and recordings 
        is accessible. If it is not accessible, send a notification to the recipients specified 
        in 'System Settings - Notifications - Subscription - Storage - Connectivity'"""
    )
)
@register_as_period_task(crontab='0 0 * * *')
@tmp_to_root_org()
def check_command_replay_storage_connectivity():
    errors = []
    replays = ReplayStorage.objects.exclude(
        type__in=[ReplayStorageType.server, ReplayStorageType.null]
    )
    commands = CommandStorage.objects.exclude(
        type__in=[CommandStorageType.server, CommandStorageType.null]
    )

    for instance in chain(replays, commands):
        msg = None
        try:
            is_valid = instance.is_valid()
        except Exception as e:
            is_valid = False
            msg = _("Test failure: {}".format(str(e)))
        if is_valid:
            continue
        errors.append({
            'msg': msg or _("Test failure: Account invalid"),
            'type': instance.get_type_display(),
            'name': instance.name
        })

    if not errors:
        return

    StorageConnectivityMessage(errors).publish_async()
