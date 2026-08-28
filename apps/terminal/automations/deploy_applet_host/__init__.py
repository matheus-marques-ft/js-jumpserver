import datetime
import os
import re
import shutil
import uuid

import yaml
from django.conf import settings
from django.utils import timezone

from common.db.utils import safe_db_connection
from common.utils import get_logger, random_string
from ops.ansible import SuperPlaybookRunner, JMSInventory
from terminal.models import Applet, AppletHostDeployment

logger = get_logger(__name__)
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))


class DeployAppletHostManager:
    def __init__(self, deployment: AppletHostDeployment, applet: Applet = None,
                 install_applets: bool = True, **kwargs):
        self.deployment = deployment
        self.applet = applet
        self.run_dir = self.get_run_dir()
        self.install_applets = bool(install_applets)

    @staticmethod
    def get_run_dir():
        base = os.path.join(settings.ANSIBLE_DIR, "applet_host_deploy")
        now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        suffix = uuid.uuid4().hex[:8]
        return os.path.join(base, f"{now}_{suffix}")

    def run(self, **kwargs):
        self._run(self._run_initial_deploy, **kwargs)

    def install_applet(self, **kwargs):
        self._run(self._run_install_applet, **kwargs)

    def uninstall_applet(self, **kwargs):
        self._run(self._run_uninstall_applet, **kwargs)

    def _run_initial_deploy(self, **kwargs):
        playbook = self.generate_initial_playbook
        return self._run_playbook(playbook, **kwargs)

    def _run_install_applet(self, **kwargs):
        if self.applet:
            generate_playbook = self.generate_install_applet_playbook
        else:
            generate_playbook = self.generate_install_all_playbook
        return self._run_playbook(generate_playbook, **kwargs)

    def _run_uninstall_applet(self, **kwargs):
        if self.applet:
            generate_playbook = self.generate_uninstall_applet_playbook
        else:
            raise ValueError("applet is required for uninstall_applet")
        return self._run_playbook(generate_playbook, **kwargs)

    @property
    def is_linux(self):
        return self.deployment.host.platform.type == 'linux'

    def _zip_applet(self, applet):
        """Zip applet.path into run_dir, for local->remote push via the Linux
        playbooks (ansible.builtin.unarchive). Same packaging the download API
        action already does (apps/terminal/api/applet/applet.py `download`)."""
        os.makedirs(self.run_dir, exist_ok=True)
        archive_base = os.path.join(self.run_dir, applet.name)
        zip_path = shutil.make_archive(archive_base, 'zip', applet.path)
        return zip_path

    def generate_initial_playbook(self):
        site_url = settings.SITE_URL
        download_host = settings.APPLET_DOWNLOAD_HOST
        bootstrap_token = settings.BOOTSTRAP_TOKEN
        host_id = str(self.deployment.host.id)
        if not site_url:
            site_url = "http://localhost:8080"
        options = self.deployment.host.deploy_options
        core_host = options.get("CORE_HOST", site_url)
        core_host = core_host.rstrip("/")
        if not download_host:
            download_host = core_host
        download_host = download_host.rstrip("/")

        def handler(plays):
            # Replace all special characters with underscore _, to prevent task execution failures caused by the host name
            applet_host_name = re.sub(r'\W', '_', self.deployment.host.name, flags=re.UNICODE)
            hostname = '{}-{}'.format(applet_host_name, random_string(7))
            for play in plays:
                play["vars"].update(options)
                play["vars"]["APPLET_DOWNLOAD_HOST"] = download_host
                play["vars"]["CORE_HOST"] = core_host
                play["vars"]["BOOTSTRAP_TOKEN"] = bootstrap_token
                play["vars"]["HOST_ID"] = host_id
                play["vars"]["HOST_NAME"] = hostname
                play["vars"]["INSTALL_APPLETS"] = self.install_applets
                if self.is_linux:
                    play["vars"]["SHIM_SRC_PATH"] = os.path.join(CURRENT_DIR, "files", "applet_shim.py")
                    play["vars"]["HEARTBEAT_SRC_PATH"] = os.path.join(CURRENT_DIR, "files", "applet_heartbeat.py")
                    # Accounts JumpServer still tracks for this host (pooled
                    # 'frete_...' and any legacy per-user 'js_...') - anything
                    # else matching those prefixes on the box is orphaned (e.g.
                    # a soft-deleted/regenerated Account whose OS user was never
                    # cleaned up) and gets removed on every deploy. See
                    # "Sanitize orphaned applet accounts" in playbook_linux.yml.
                    play["vars"]["VALID_ACCOUNT_USERNAMES"] = list(
                        self.deployment.host.accounts
                        .filter(username__regex=r'^(frete_|js_)')
                        .values_list('username', flat=True)
                    )
            return plays

        playbook_name = "playbook_linux.yml" if self.is_linux else "playbook.yml"
        return self._generate_playbook(playbook_name, handler)

    def generate_install_all_playbook(self):
        if not self.is_linux:
            return self._generate_playbook("install_all.yml")

        applets = list(self.deployment.host.applet_set.filter(is_active=True))
        zips = {applet.name: self._zip_applet(applet) for applet in applets}

        def handler(plays):
            for play in plays:
                play["vars"]["applet_zips"] = zips
            return plays

        return self._generate_playbook("install_all_linux.yml", handler)

    def generate_install_applet_playbook(self):
        applet_name = self.applet.name
        options = self.deployment.host.deploy_options

        if not self.is_linux:
            def handler(plays):
                for play in plays:
                    play["vars"].update(options)
                    play["vars"]["applet_name"] = applet_name
                return plays

            return self._generate_playbook("install_applet.yml", handler)

        zip_path = self._zip_applet(self.applet)

        def handler(plays):
            for play in plays:
                play["vars"]["applet_name"] = applet_name
                play["vars"]["applet_zip_path"] = zip_path
            return plays

        return self._generate_playbook("install_applet_linux.yml", handler)

    def generate_uninstall_applet_playbook(self):
        applet_name = self.applet.name

        def handler(plays):
            for play in plays:
                play["vars"]["applet_name"] = applet_name
            return plays

        playbook_name = "uninstall_applet_linux.yml" if self.is_linux else "uninstall_applet.yml"
        return self._generate_playbook(playbook_name, handler)

    def generate_inventory(self):
        inventory = JMSInventory(
            [self.deployment.host], account_policy="privileged_only"
        )
        inventory_dir = os.path.join(self.run_dir, "inventory")
        inventory_path = os.path.join(inventory_dir, "hosts.yml")
        inventory.write_to_file(inventory_path)
        return inventory_path

    def _generate_playbook(self, playbook_template_name, plays_handler: callable = None):
        playbook_src = os.path.join(CURRENT_DIR, playbook_template_name)
        with open(playbook_src) as f:
            plays = yaml.safe_load(f)
        if plays_handler:
            plays = plays_handler(plays)
        playbook_dir = os.path.join(self.run_dir, "playbook")
        playbook_dst = os.path.join(playbook_dir, "main.yml")
        os.makedirs(playbook_dir, exist_ok=True)
        with open(playbook_dst, "w") as f:
            yaml.safe_dump(plays, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return playbook_dst

    def _run_playbook(self, generate_playbook: callable, **kwargs):
        inventory = self.generate_inventory()
        playbook = generate_playbook()
        runner = SuperPlaybookRunner(
            inventory=inventory,
            playbook=playbook,
            project_dir=self.run_dir,
            safety_mode="playbook_unsafe",
            inventory_safety="json_escape",
        )
        return runner.run(**kwargs)

    def delete_runtime_dir(self):
        if settings.DEBUG_DEV:
            return
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def _run(self, cb_func: callable, **kwargs):
        try:
            self.deployment.date_start = timezone.now()
            cb = cb_func(**kwargs)
            self.deployment.status = cb.status
        except Exception as e:
            logger.error("Error: {}".format(e))
            self.deployment.status = "error"
        finally:
            self.deployment.date_finished = timezone.now()
            with safe_db_connection():
                self.deployment.save()
        self.delete_runtime_dir()
