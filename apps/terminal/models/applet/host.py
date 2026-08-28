from collections import defaultdict
from django.core.cache import cache
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError
from simple_history.utils import bulk_create_with_history

from assets.models import Host
from accounts.const import SecretType
from common.db.models import JMSBaseModel
from common.utils import get_logger, random_string, ssh_key_gen
from terminal.const import PublishStatus

__all__ = ['AppletHost', 'AppletHostDeployment']

logger = get_logger(__file__)


class AppletHost(Host):
    deploy_options = models.JSONField(default=dict, verbose_name=_('Deploy options'))
    auto_create_accounts = models.BooleanField(default=True, verbose_name=_('Auto create accounts'))
    accounts_create_amount = models.IntegerField(default=100, verbose_name=_('Accounts create amount'))
    inited = models.BooleanField(default=False, verbose_name=_('Inited'))
    date_inited = models.DateTimeField(null=True, blank=True, verbose_name=_('Date inited'))
    date_synced = models.DateTimeField(null=True, blank=True, verbose_name=_('Date synced'))
    terminal = models.OneToOneField(
        'terminal.Terminal', on_delete=models.PROTECT, null=True, blank=True,
        related_name='applet_host', verbose_name=_('Terminal')
    )
    using_same_account = models.BooleanField(default=False, verbose_name=_('Using same account'))
    applets = models.ManyToManyField(
        'Applet', verbose_name=_('Applet'),
        through='AppletPublication', through_fields=('host', 'applet'),
    )
    LOCKING_ORG = '00000000-0000-0000-0000-000000000004'

    class Meta:
        verbose_name = _('Hosting')

    def __str__(self):
        return self.name

    @property
    def load(self):
        if not self.terminal:
            return 'offline'
        return self.terminal.load

    def check_terminal_binding(self, request):
        request_terminal = getattr(request.user, 'terminal', None)
        if not request_terminal:
            raise ValidationError('Request user has no terminal')

        self.date_synced = timezone.now()
        if self.terminal == request_terminal:
            self.save(update_fields=['date_synced'])
        else:
            self.terminal = request_terminal
            self.save(update_fields=['terminal', 'date_synced'])

    def check_applets_state(self, applets_value_list):
        applets = self.applets.all()
        name_version_mapper = {
            value['name']: value['version']
            for value in applets_value_list
        }

        status_applets = defaultdict(list)
        for applet in applets:
            if applet.name not in name_version_mapper:
                status_applets[PublishStatus.failed.value].append(applet)
            elif applet.version != name_version_mapper[applet.name]:
                status_applets[PublishStatus.mismatch.value].append(applet)
            else:
                status_applets[PublishStatus.success.value].append(applet)

        for status, applets in status_applets.items():
            self.publications.filter(applet__in=applets) \
                .exclude(status=status) \
                .update(status=status)

    @staticmethod
    def random_username():
        return 'frete_' + random_string(8)

    @staticmethod
    def random_ssh_key():
        # Password accounts hit a wall on hardened cloud images (this one included):
        # sshd ships with PasswordAuthentication disabled by default, and even after
        # turning it on and reloading sshd, password auth kept failing - so these
        # accounts use a generated keypair instead, same as Account.gen_key()/
        # SecretGenerator.generate_ssh_key() do elsewhere. push_account's posix
        # playbook already knows how to push a ssh_key account's public key into
        # authorized_keys (accounts/automations/base/manager.py's
        # handle_ssh_secret() derives it from this private key automatically).
        private_key, _public_key = ssh_key_gen()
        return private_key

    def generate_accounts(self):
        if not self.auto_create_accounts:
            return
        # Only the random/pooled accounts (random_username(), 'frete_...') are
        # auto-created - per-user private accounts (generate_private_accounts,
        # 'js_<username>') are intentionally not wired in here anymore.
        new_accounts = list(self.generate_public_accounts())
        # New accounts are created with `is_active=False` above. On Linux applet
        # hosts they must actually be provisioned as OS users before being made
        # selectable, otherwise remote app connections fail at SSH login time
        # (the account is visible in JumpServer but doesn't exist on the box).
        # This call flips `is_active` to True only for accounts it successfully
        # provisions.
        self.provision_and_activate_accounts(new_accounts)

    def generate_public_accounts(self):
        now_count = self.accounts.filter(privileged=False, username__startswith='frete_').count()
        need = self.accounts_create_amount - now_count

        accounts = []
        usernames = []
        account_model = self.accounts.model
        for i in range(need):
            username = self.random_username()
            private_key = self.random_ssh_key()
            usernames.append(username)
            account = account_model(
                username=username, secret=private_key, name=username,
                asset_id=self.id, secret_type=SecretType.SSH_KEY, version=1,
                org_id=self.LOCKING_ORG, is_active=False,
            )
            accounts.append(account)
        bulk_create_with_history(accounts, account_model, batch_size=20, ignore_conflicts=True)
        if not usernames:
            return []
        return list(self.accounts.filter(username__in=usernames))

    def generate_private_accounts_by_usernames(self, usernames):
        accounts = []
        created_usernames = []
        account_model = self.accounts.model
        for username in usernames:
            private_key = self.random_ssh_key()
            username = 'js_' + username
            created_usernames.append(username)
            account = account_model(
                username=username, secret=private_key, name=username,
                asset_id=self.id, secret_type=SecretType.SSH_KEY, version=1,
                org_id=self.LOCKING_ORG, is_active=False,
            )
            accounts.append(account)
        bulk_create_with_history(accounts, account_model, batch_size=20, ignore_conflicts=True)
        if not created_usernames:
            return []
        return list(self.accounts.filter(username__in=created_usernames))

    def generate_private_accounts(self):
        from users.models import User
        usernames = User.objects \
            .filter(is_active=True, is_service_account=False) \
            .exclude(username__startswith='[') \
            .values_list('username', flat=True)
        account_usernames = self.accounts.all().values_list('username', flat=True)
        account_usernames = [username[3:] for username in account_usernames if username.startswith('js_')]
        not_exist_users = set(usernames) - set(account_usernames)
        return self.generate_private_accounts_by_usernames(not_exist_users)

    def provision_and_activate_accounts(self, accounts):
        """
        Provision newly created accounts as real OS users on this applet host,
        and only mark them `is_active=True` once that provisioning actually
        succeeds. Reuses the same posix `push_account` automation
        (apps/accounts/automations/push_account/host/posix/main.yml,
        `ansible.builtin.user`) that's already used for regular assets, run
        ad-hoc against just these accounts via `quickstart_automation_by_snapshot`
        (the same helper `accounts.tasks.push_account.push_accounts_to_assets_task`
        and `accounts.tasks.automation.execute_automation_record_task` use to run
        an automation without a persisted model row).

        Only applies to Linux applet hosts. Windows applet host accounts are
        provisioned through a separate (Tinker-driven) mechanism and are left
        untouched here.
        """
        if not accounts:
            return
        if self.platform.type != 'linux':
            return

        from accounts.const import AutomationTypes, ChangeSecretRecordStatusChoice
        from accounts.models import Account, PushAccountAutomation, PushSecretRecord
        from accounts.tasks.common import quickstart_automation_by_snapshot

        account_ids = [str(account.id) for account in accounts]
        task_name = PushAccountAutomation.generate_unique_name(_('Push applet host accounts '))
        snapshot = {
            'params': {},
            'accounts': account_ids,
            'assets': [str(self.id)],
        }
        try:
            quickstart_automation_by_snapshot(task_name, AutomationTypes.push_account, snapshot)
        except Exception as e:
            logger.error(
                'Provision applet host accounts on "%s" failed, accounts stay inactive: %s',
                self, e
            )
            return

        success_account_ids = list(
            PushSecretRecord.objects.filter(
                account_id__in=account_ids,
                status=ChangeSecretRecordStatusChoice.success.value,
            ).values_list('account_id', flat=True)
        )
        if success_account_ids:
            Account.objects.filter(id__in=success_account_ids).update(is_active=True)

        failed_count = len(account_ids) - len(success_account_ids)
        if failed_count:
            logger.error(
                'Provision applet host accounts on "%s": %s/%s accounts failed '
                'to provision as OS users and remain inactive',
                self, failed_count, len(account_ids)
            )


class AppletHostDeployment(JMSBaseModel):
    host = models.ForeignKey('AppletHost', on_delete=models.CASCADE, verbose_name=_('Hosting'))
    initial = models.BooleanField(default=False, verbose_name=_('Initial'))
    status = models.CharField(max_length=16, default='pending', verbose_name=_('Status'))
    date_start = models.DateTimeField(null=True, verbose_name=_('Date start'), db_index=True)
    date_finished = models.DateTimeField(null=True, verbose_name=_("Date finished"))
    comment = models.TextField(default='', blank=True, verbose_name=_('Comment'))
    task = models.UUIDField(null=True, verbose_name=_('Task'))

    class Meta:
        ordering = ('-date_start',)
        verbose_name = _("Applet host deployment")

    def start(self, **kwargs):
        # Re-initialize deployment; the terminal associated with the applet host needs to be deleted
        # Otherwise tinker will conflict due to the component having the same registered name, causing task execution to fail
        if self.host.terminal:
            terminal = self.host.terminal
            self.host.terminal = None
            self.host.save()
            terminal.delete()

        cache.set(f'APPLET_HOST_DELOYING', str(self.id), timeout=300)
        from ...automations.deploy_applet_host import DeployAppletHostManager
        manager = DeployAppletHostManager(self, **kwargs)
        manager.run()

    def install_applet(self, applet_id, **kwargs):
        manager = self.create_deploy_manager(applet_id, **kwargs)
        manager.install_applet(**kwargs)

    def uninstall_applet(self, applet_id, **kwargs):
        manager = self.create_deploy_manager(applet_id, **kwargs)
        manager.uninstall_applet(**kwargs)

    def create_deploy_manager(self, applet_id, **kwargs):
        from ...automations.deploy_applet_host import DeployAppletHostManager
        from .applet import Applet
        if applet_id:
            applet = Applet.objects.get(id=applet_id)
        else:
            applet = None
        return DeployAppletHostManager(self, applet=applet)

    def save_task(self, task):
        self.task = task
        self.save(update_fields=['task'])
