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
    def random_password():
        return random_string(16, special_char=True)

    @staticmethod
    def random_ssh_key():
        # Kept for accounts that are explicitly ssh_key (push_account's posix
        # playbook and _stage_remote_app_token_for_linux both already support
        # either type) - but NOT used by the auto-created pool below anymore.
        # RDP login on Linux applet hosts goes through xrdp's own PAM auth
        # against /etc/shadow, completely separate from sshd - an ssh_key
        # account never gets a real password set (push_account's posix
        # playbook only does that `when: secret_type == "password"`), so xrdp
        # has nothing to authenticate against and every RDP connection fails
        # with a generic "Upstream error" (Guacamole status 515). Password
        # accounts need PasswordAuthentication yes in sshd for SFTP token
        # staging to work (see playbook_linux.yml) - that's the actual fix for
        # the "Bad authentication type" SSH failures, not switching to keys.
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
        # Counting every 'frete_*' row (active or not) toward "already have
        # enough" - not just the active ones - means an account that was
        # created but never successfully provisioned as an OS user (see
        # provision_and_activate_accounts) permanently occupies a pool slot:
        # `need` drops to 0 and stays there, so a stuck account is silently
        # never retried, generate_accounts() looks like a no-op forever, and
        # a fresh EC2/host swap (accounts_create_amount unchanged, but every
        # existing row now points at OS users that don't exist there) never
        # tops the pool back up either. Retry the inactive ones instead of
        # ignoring them, and only create brand-new rows to make up the rest.
        existing = list(self.accounts.filter(privileged=False, username__startswith='frete_'))
        pending = [account for account in existing if not account.is_active]
        need = self.accounts_create_amount - len(existing)

        new_accounts = []
        usernames = []
        account_model = self.accounts.model
        for i in range(max(need, 0)):
            username = self.random_username()
            password = self.random_password()
            usernames.append(username)
            account = account_model(
                username=username, secret=password, name=username,
                asset_id=self.id, secret_type=SecretType.PASSWORD, version=1,
                org_id=self.LOCKING_ORG, is_active=False,
            )
            new_accounts.append(account)
        if new_accounts:
            bulk_create_with_history(new_accounts, account_model, batch_size=20, ignore_conflicts=True)
            new_accounts = list(self.accounts.filter(username__in=usernames))
        return pending + new_accounts

    def generate_private_accounts_by_usernames(self, usernames):
        accounts = []
        created_usernames = []
        account_model = self.accounts.model
        for username in usernames:
            password = self.random_password()
            username = 'js_' + username
            created_usernames.append(username)
            account = account_model(
                username=username, secret=password, name=username,
                asset_id=self.id, secret_type=SecretType.PASSWORD, version=1,
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
