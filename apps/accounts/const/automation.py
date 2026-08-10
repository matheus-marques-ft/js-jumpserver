from django.db import models
from django.utils.translation import gettext_lazy as _

from assets.const import Connectivity
from common.db.fields import TreeChoices

DEFAULT_PASSWORD_LENGTH = 30
DEFAULT_PASSWORD_RULES = {
    'length': DEFAULT_PASSWORD_LENGTH,
    'uppercase': True,
    'lowercase': True,
    'digit': True,
    'symbol': True,
}

__all__ = [
    'AutomationTypes', 'SecretStrategy', 'SSHKeyStrategy', 'Connectivity',
    'DEFAULT_PASSWORD_LENGTH', 'DEFAULT_PASSWORD_RULES', 'TriggerChoice',
    'PushAccountActionChoice', 'AccountBackupType', 'ChangeSecretRecordStatusChoice',
    'GatherAccountDetailField', 'ChangeSecretAccountStatus'
]


class AutomationTypes(models.TextChoices):
    push_account = 'push_account', _('Push account')
    change_secret = 'change_secret', _('Change secret')
    verify_account = 'verify_account', _('Verify account')
    remove_account = 'remove_account', _('Remove account')
    gather_accounts = 'gather_accounts', _('Gather accounts')
    verify_gateway_account = 'verify_gateway_account', _('Verify gateway account')
    check_account = 'check_account', _('Check account')
    backup_account = 'backup_account', _('Backup account')

    @classmethod
    def get_type_model(cls, tp):
        from accounts.models import (
            PushAccountAutomation, ChangeSecretAutomation,
            VerifyAccountAutomation, GatherAccountsAutomation,
            CheckAccountAutomation, BackupAccountAutomation
        )
        type_model_dict = {
            cls.push_account: PushAccountAutomation,
            cls.change_secret: ChangeSecretAutomation,
            cls.verify_account: VerifyAccountAutomation,
            cls.gather_accounts: GatherAccountsAutomation,
            cls.check_account: CheckAccountAutomation,
            cls.backup_account: BackupAccountAutomation,
        }
        return type_model_dict.get(tp)


class SecretStrategy(models.TextChoices):
    custom = 'specific', _('Specific secret')
    random = 'random', _('Random generate')


class SSHKeyStrategy(models.TextChoices):
    # add = 'add', _('Append SSH KEY')
    set_jms = 'set_jms', _('Replace (Replace only keys pushed by system) ')
    set = 'set', _('Empty and append SSH KEY')


class TriggerChoice(models.TextChoices, TreeChoices):
    # When an asset is created, the account is created directly; if it is a dynamic
    # account, the users authorized for this asset need to be queried from the
    # permissions, and accounts are created using those usernames
    on_asset_create = 'on_asset_create', _('On asset create')
    # Permission changes include: user added to permission, user group added to
    # permission, asset added to permission, node added to permission, account changed
    # When a user is added to a permission, query all account automations with the
    # same name, and create the users (user groups) on this permission onto the
    # assets (nodes) of this permission
    on_perm_add_user = 'on_perm_add_user', _('On perm add user')
    # When a user group is added to a permission, query all account automations with
    # the same name, and create the users (user groups) on this permission onto the
    # assets (nodes) of this permission
    on_perm_add_user_group = 'on_perm_add_user_group', _('On perm add user group')
    # When an asset is added to a permission, query all account automations of the
    # permission, and create them onto the asset of this permission
    on_perm_add_asset = 'on_perm_add_asset', _('On perm add asset')
    # When a node is added to a permission, query all account automations of the
    # permission, and create them onto the assets of the node of this permission
    on_perm_add_node = 'on_perm_add_node', _('On perm add node')
    # When the account of a permission changes, query all account automations of the
    # permission, and create them onto the assets (nodes) of this permission
    on_perm_add_account = 'on_perm_add_account', _('On perm add account')
    # When an asset is added to a node, query the node's permission rules, query all
    # account automations of the permission, and create them onto the assets (nodes)
    # of this permission
    on_asset_join_node = 'on_asset_join_node', _('On asset join node')
    # When a user joins a user group, query the user group's permission rules, query
    # all account automations of the permission, and create them onto the assets
    # (nodes) of this permission
    on_user_join_group = 'on_user_join_group', _('On user join group')

    @classmethod
    def branches(cls):
        # Anything related to users and user groups is a dynamic account
        #
        return [
            cls.on_asset_create,
            (_("On perm change"), [
                cls.on_perm_add_user,
                cls.on_perm_add_user_group,
                cls.on_perm_add_asset,
                cls.on_perm_add_node,
                cls.on_perm_add_account,
            ]),
            (_("Inherit from group or node"), [
                cls.on_asset_join_node,
                cls.on_user_join_group,
            ])
        ]


class PushAccountActionChoice(models.TextChoices):
    create_and_push = 'create_and_push', _('Create and push')
    only_create = 'only_create', _('Only create')


class AccountBackupType(models.TextChoices):
    """Backup type"""
    email = 'email', _('Email')
    # Currently only the SFTP method is supported
    object_storage = 'object_storage', _('SFTP')


class ChangeSecretRecordStatusChoice(models.TextChoices):
    success = 'success', _('Success')
    failed = 'failed', _('Failed')
    pending = 'pending', _('Pending')
    unverified = 'unverified', _('Unverified')


class ChangeSecretAccountStatus(models.TextChoices):
    QUEUED = 'queued', _('Queued')
    READY = 'ready', _('Ready')
    PROCESSING = 'processing', _('Processing')


class GatherAccountDetailField(models.TextChoices):
    can_login = 'can_login', _('Can login')
    superuser = 'superuser', _('Superuser')
    create_date = 'create_date', _('Create date')
    is_disabled = 'is_disabled', _('Is disabled')
    default_database_name = 'default_database_name', _('Default database name')
    uid = 'uid', _('UID')
    account_status = 'account_status', _('Account status')
    default_tablespace = 'default_tablespace', _('Default tablespace')
    roles = 'roles', _('Role')
    privileges = 'privileges', _('Perms')
    groups = 'groups', _('Groups')
    sudoers = 'sudoers', 'sudoers'
    authorized_keys = 'authorized_keys', _('Authorized keys')
    db = 'db', _('DB')
