from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from celery import shared_task
from django.utils.translation import gettext_lazy as _

from accounts.backends import vault_client
from accounts.const import VaultTypeChoices
from accounts.models import AccountTemplate, Account
from common.utils import get_logger
from orgs.utils import tmp_to_root_org

logger = get_logger(__name__)


def sync_instance(instance):
    instance_desc = f'[{instance._meta.verbose_name}-{instance.id}-{instance}]'
    if instance.secret_has_save_to_vault:
        msg = f'\033[32m- Skipping sync: {instance_desc}, Reason: [already synced]'
        return "skipped", msg

    try:
        vault_client.create(instance)
    except Exception as e:
        msg = f'\033[31m- Sync failed: {instance_desc}, Reason: [{e}]'
        return "failed", msg
    else:
        msg = f'\033[32m- Sync succeeded: {instance_desc}'
        return "succeeded", msg


@shared_task(
    verbose_name=_('Sync secret to vault'),
    description=_(
        "When clicking 'Sync' in 'System Settings - Features - Account Storage' this task will be executed"
    )
)
def sync_secret_to_vault():
    if not vault_client.enabled:
        # Cannot check settings.VAULT_ENABLED here, must check the current vault_client type instead
        print('\033[35m>>> Vault feature is not currently enabled, no sync needed')
        return
    if VaultTypeChoices.local == vault_client.type:
        print('\033[31m>>> The current third-party Vault client failed to initialize, data is stored in the local database')
        return

    failed, skipped, succeeded = 0, 0, 0
    to_sync_models = [Account, AccountTemplate, Account.history.model]
    print(f'\033[33m>>> Starting sync of secret data to Vault ({datetime.now().strftime("%Y-%m-%d %H:%M:%S")})')
    with tmp_to_root_org():
        instances = []
        for model in to_sync_models:
            instances += list(model.objects.all())

        max_workers = 1 if VaultTypeChoices.azure == vault_client.type else 10
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            tasks = [executor.submit(sync_instance, instance) for instance in instances]

            for future in as_completed(tasks):
                status, msg = future.result()
                print(msg)
                if status == "succeeded":
                    succeeded += 1
                elif status == "failed":
                    failed += 1
                elif status == "skipped":
                    skipped += 1

    total = succeeded + failed + skipped
    print(
        f'\033[33m>>> Sync complete: {model.__module__}, '
        f'Total: {total}, '
        f'Succeeded: {succeeded}, '
        f'Failed: {failed}, '
        f'Skipped: {skipped}'
    )
    print(f'\033[33m>>> All syncs complete ({datetime.now().strftime("%Y-%m-%d %H:%M:%S")})')
    print('\033[0m')
