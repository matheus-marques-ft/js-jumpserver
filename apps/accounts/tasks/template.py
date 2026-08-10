from datetime import datetime

from celery import shared_task
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _

from orgs.utils import tmp_to_root_org, tmp_to_org


@shared_task(
    verbose_name=_('Template sync info to related accounts'),
    activity_callback=lambda self, template_id, *args, **kwargs: (template_id, None),
    description=_(
        """When clicking 'Sync new secret to accounts' in 'Console - Account - Templates - 
        Accounts' this task will be executed"""
    )
)
def template_sync_related_accounts(template_id, user_id=None):
    from accounts.models import Account, AccountTemplate
    with tmp_to_root_org():
        template = get_object_or_404(AccountTemplate, id=template_id)
    org_id = template.org_id

    with tmp_to_org(org_id):
        accounts = Account.objects.filter(source_id=template_id)
    if not accounts:
        print('\033[35m>>> No accounts need to be synced, ending task')
        print('\033[0m')
        return

    failed, succeeded = 0, 0
    succeeded_account_ids = []
    name = template.name
    username = template.username
    secret_type = template.secret_type
    privileged = template.privileged
    print(
        f'\033[32m>>> Starting sync of template name, username, and secret type to related accounts ({datetime.now().strftime("%Y-%m-%d %H:%M:%S")})')
    with tmp_to_org(org_id):
        for account in accounts:
            account.name = name
            account.username = username
            account.secret_type = secret_type
            account.privileged = privileged
            try:
                account.save(update_fields=['name', 'username', 'secret_type', 'privileged'])
                succeeded += 1
                succeeded_account_ids.append(account.id)
            except Exception as e:
                account.source_id = None
                account.save(update_fields=['source_id'])
                print(f'\033[31m- Sync failed: [{account}] Reason: [{e}]')
                failed += 1
        accounts = Account.objects.filter(id__in=succeeded_account_ids)
        if accounts:
            print(f'\033[33m>>> Bulk updating account secrets ({datetime.now().strftime("%Y-%m-%d %H:%M:%S")})')
            template.bulk_sync_account_secret(accounts, user_id)

    total = succeeded + failed
    print(
        f'\033[33m>>> Sync complete:, '
        f'Total: {total}, '
        f'Succeeded: {succeeded}, '
        f'Failed: {failed}, '
        f'({datetime.now().strftime("%Y-%m-%d %H:%M:%S")}) '
    )
    print('\033[0m')
