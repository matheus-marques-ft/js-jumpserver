import json

from django.db import migrations

from assets.const import AllTypes

# Mirrors the Windows 'RemoteAppHost' platform (created in 0003_auto_20180109_2331's frozen
# fixture) so xrdp-based RemoteApp hosts can be registered as Linux assets. Needs its own
# migration - 'RemoteAppHostLinux' was only added to HostTypes.internal_platforms() (used to
# bootstrap brand-new databases), which never runs again on an already-migrated one, so an
# existing server never picks it up without an explicit RunPython step like this.
# Automation methods reused from the plain 'Linux' platform entry in that same fixture -
# nothing Linux-specific about account management here (see js-jumpserver README.md's
# RemoteApp/xrdp migration notes: generic Ansible/SSH account handling is unmodified).
platform_data_json = '''
{
    "created_by": "system",
    "updated_by": "system",
    "comment": "",
    "name": "RemoteAppHostLinux",
    "category": "host",
    "type": "linux",
    "meta": {},
    "internal": true,
    "gateway_enabled": true,
    "su_enabled": true,
    "su_method": null,
    "custom_fields": [],
    "automation": {
        "ansible_enabled": true,
        "ansible_config": {
            "ansible_connection": "smart"
        },
        "ping_enabled": true,
        "ping_method": "posix_ping",
        "ping_params": {},
        "gather_facts_enabled": true,
        "gather_facts_method": "gather_facts_posix",
        "gather_facts_params": {},
        "change_secret_enabled": true,
        "change_secret_method": "change_secret_posix",
        "change_secret_params": {},
        "push_account_enabled": true,
        "push_account_method": "push_account_posix",
        "push_account_params": {
            "sudo": "/bin/whoami",
            "shell": "/bin/bash",
            "home": "",
            "groups": ""
        },
        "verify_account_enabled": true,
        "verify_account_method": "verify_account_posix",
        "verify_account_params": {},
        "gather_accounts_enabled": true,
        "gather_accounts_method": "gather_accounts_posix",
        "gather_accounts_params": {},
        "remove_account_enabled": true,
        "remove_account_method": "remove_account_posix",
        "remove_account_params": {}
    },
    "protocols": [
        {
            "name": "rdp",
            "port": 3389,
            "primary": true,
            "required": false,
            "default": true,
            "public": true,
            "setting": {
                "console": false,
                "security": "any"
            }
        },
        {
            "name": "ssh",
            "port": 22,
            "primary": false,
            "required": true,
            "default": false,
            "public": true,
            "setting": {
                "sftp_enabled": true,
                "sftp_home": "/tmp"
            }
        }
    ]
}
'''


def add_remote_app_host_linux_platform(apps, schema_editor):
    platform_model = apps.get_model('assets', 'Platform')
    automation_cls = apps.get_model('assets', 'PlatformAutomation')
    platform_data = json.loads(platform_data_json)
    AllTypes.create_or_update_by_platform_data(
        platform_data, platform_cls=platform_model,
        automation_cls=automation_cls
    )


def remove_remote_app_host_linux_platform(apps, schema_editor):
    platform_model = apps.get_model('assets', 'Platform')
    platform_model.objects.filter(name='RemoteAppHostLinux', type='linux').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('assets', '0023_platformpackage_platform_package'),
    ]

    operations = [
        migrations.RunPython(
            add_remote_app_host_linux_platform,
            reverse_code=remove_remote_app_host_linux_platform,
        )
    ]
