from collections import defaultdict

from django.utils import timezone

from accounts.const import AutomationTypes
from accounts.models import GatheredAccount, Account, AccountRisk, RiskChoice
from common.const import ConfirmOrIgnore
from common.decorators import bulk_create_decorator, bulk_update_decorator
from common.utils import get_logger
from common.utils.strings import get_text_diff
from orgs.utils import tmp_to_org
from .filter import GatherAccountsFilter
from ..base.manager import AccountBasePlaybookManager

logger = get_logger(__name__)

risk_items = [
    "authorized_keys",
    "sudoers",
    "groups",
]
common_risk_items = [
    "address_last_login",
    "date_last_login",
    "date_password_change",
    "date_password_expired",
    "detail"
]
diff_items = risk_items + common_risk_items


@bulk_create_decorator(AccountRisk)
def _create_risk(data):
    return AccountRisk(**data)


@bulk_update_decorator(AccountRisk, update_fields=["details"])
def _update_risk(account):
    return account


def format_datetime(value):
    if isinstance(value, timezone.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return value


def get_items_diff(ori_account, d):
    if hasattr(ori_account, "_diff"):
        return ori_account._diff

    diff = {}
    for item in diff_items:
        get_item_diff(item, ori_account, d, diff)
    ori_account._diff = diff
    return diff


def get_item_diff(item, ori_account, d, diff):
    detail = getattr(ori_account, 'detail', {})
    new_detail = d.get('detail', {})
    ori = getattr(ori_account, item, None) or detail.get(item)
    new = d.get(item, "") or new_detail.get(item)
    if not ori and not new:
        return

    ori = format_datetime(ori)
    new = format_datetime(new)

    if new != ori:
        diff[item] = get_text_diff(str(ori), str(new))


class AnalyseAccountRisk:
    long_time = timezone.timedelta(days=90)
    datetime_check_items = [
        {"field": "date_last_login", "risk": "long_time_no_login", "delta": long_time},
        {
            "field": "date_password_change",
            "risk": RiskChoice.long_time_password,
            "delta": long_time,
        },
        {
            "field": "date_password_expired",
            "risk": "password_expired",
            "delta": timezone.timedelta(seconds=1),
        },
    ]

    def __init__(self, check_risk=True):
        self.check_risk = check_risk
        self.now = timezone.now()
        self.pending_add_risks = []

    def _analyse_item_changed(self, ori_ga, d):
        diff = get_items_diff(ori_ga, d)
        if not diff:
            return

        risks = []
        for k, v in diff.items():
            if k not in risk_items:
                continue
            risks.append(
                dict(
                    asset_id=str(ori_ga.asset_id),
                    username=ori_ga.username,
                    gathered_account=ori_ga,
                    risk=k + "_changed",
                    detail={"diff": v},
                )
            )
        self.save_or_update_risks(risks)

    def _analyse_datetime_changed(self, ori_account, d, asset, username):
        basic = {"asset_id": str(asset.id), "username": username}

        risks = []
        for item in self.datetime_check_items:
            field = item["field"]
            risk = item["risk"]
            delta = item["delta"]

            date = d.get(field)
            if not date:
                continue

            # If the server-collected time matches the database time, skip the comparison
            # since risk can't be detected that way — not quite right, commenting out for now
            # pre_date = ori_account and getattr(ori_account, field)
            # if pre_date == date:
            #     continue

            if date and date < timezone.now() - delta:
                risks.append(
                    dict(**basic, risk=risk, detail={"date": date.isoformat()})
                )

        self.save_or_update_risks(risks)

    def save_or_update_risks(self, risks):
        # Fetch these ahead of time to avoid querying the database every time
        asset_ids = {r["asset_id"] for r in risks}
        assets_risks = AccountRisk.objects.filter(asset_id__in=asset_ids)
        assets_risks = {f"{r.asset_id}_{r.username}_{r.risk}": r for r in assets_risks}

        for d in risks:
            detail = d.pop("detail", {})
            detail["datetime"] = self.now.isoformat()
            key = f"{d['asset_id']}_{d['username']}_{d['risk']}"
            found = assets_risks.get(key)

            if not found:
                _create_risk(dict(**d, details=[detail]))
                continue

            found.details.append(detail)
            _update_risk(found)

    def lost_accounts(self, asset, lost_users):
        if not self.check_risk:
            return
        for user in lost_users:
            _create_risk(
                dict(
                    asset_id=str(asset.id),
                    username=user,
                    risk=RiskChoice.account_deleted,
                    details=[{"datetime": self.now.isoformat()}],
                )
            )

    def analyse_risk(self, asset, ga, d, sys_found):
        if not self.check_risk:
            return

        if ga:
            self._analyse_item_changed(ga, d)
        if not sys_found:
            basic = {"asset": asset, "username": d["username"], 'gathered_account': ga}
            _create_risk(
                dict(
                    **basic,
                    risk=RiskChoice.new_found,
                    details=[{"datetime": self.now.isoformat()}],
                )
            )
        self._analyse_datetime_changed(ga, d, asset, d["username"])


class GatherAccountsManager(AccountBasePlaybookManager):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.host_asset_mapper = {}
        self.asset_account_info = {}
        self.asset_usernames_mapper = defaultdict(set)
        self.ori_asset_usernames = defaultdict(set)
        self.ori_gathered_usernames = defaultdict(set)
        self.ori_gathered_accounts_mapper = dict()
        self.is_sync_account = self.execution.snapshot.get("is_sync_account")
        self.check_risk = self.execution.snapshot.get("check_risk", False)

    @classmethod
    def method_type(cls):
        return AutomationTypes.gather_accounts

    def host_callback(self, host, asset=None, **kwargs):
        super().host_callback(host, asset=asset, **kwargs)
        self.host_asset_mapper[host["name"]] = asset
        return host

    def _filter_success_result(self, tp, result):
        result = GatherAccountsFilter(tp).run(self.method_id_meta_mapper, result)
        return result

    @staticmethod
    def _get_nested_info(data, *keys):
        for key in keys:
            data = data.get(key, {})
            if not data:
                break
        return data

    @classmethod
    def _get_gathered_info(cls, result):
        result = result or {}
        task_results = result.get("ok")
        if task_results is None:
            task_results = result
        if not isinstance(task_results, dict):
            return {}

        for task_result in reversed(list(task_results.values())):
            if not isinstance(task_result, dict):
                continue
            response = task_result.get("res", {})
            facts = response.get("ansible_facts", {})
            if "info" in facts:
                return facts["info"]

        # Keep compatibility with older/custom gather methods which still
        # expose the final value through a debug task.
        return cls._get_nested_info(result, "debug", "res", "info")

    def _collect_asset_account_info(self, asset, info):
        result = self._filter_success_result(asset.type, info)
        accounts = []

        for username, info in result.items():
            self.asset_usernames_mapper[str(asset.id)].add(username)

            d = {"asset": asset, "username": username, "remote_present": True, **info}
            accounts.append(d)
        self.asset_account_info[asset] = accounts

    def on_host_success(self, host, result):
        info = self._get_gathered_info(result)
        asset = self.host_asset_mapper.get(host)

        if asset and info:
            try:
                self._collect_asset_account_info(asset, info)
            except Exception as error:
                logger.exception(
                    'Collect gathered account result failed: host=%s',
                    host,
                )
                return super().on_host_error(
                    host, str(error), result
                )
            super().on_host_success(host, result)
        else:
            super().on_host_error(
                host, 'Gathered account result is empty', result
            )

    def prefetch_origin_account_usernames(self):
        """
        Fetch these in advance to avoid a SQL query every time
        :return:
        """
        assets = self.asset_usernames_mapper.keys()
        accounts = Account.objects.filter(asset__in=assets).values_list(
            "asset", "username"
        )

        for asset_id, username in accounts:
            self.ori_asset_usernames[str(asset_id)].add(username)

        ga_accounts = GatheredAccount.objects.filter(asset__in=assets)
        for account in ga_accounts:
            self.ori_gathered_usernames[str(account.asset_id)].add(account.username)
            key = "{}_{}".format(account.asset_id, account.username)
            self.ori_gathered_accounts_mapper[key] = account

    def update_gather_accounts_status(self, asset):
        """
        Remote accounts, gathered accounts, and accounts in the vault.
        Based on newly added accounts, mark the gathered account's status so admins pay attention.

        Remote account -> Gathered account -> Privileged account
        """
        remote_users = self.asset_usernames_mapper[str(asset.id)]
        ori_users = self.ori_asset_usernames[str(asset.id)]
        ori_ga_users = self.ori_gathered_usernames[str(asset.id)]

        queryset = GatheredAccount.objects.filter(asset=asset).exclude(
            status=ConfirmOrIgnore.ignored
        )

        # Remote accounts that outnumber gathered accounts
        # These are newly created, no status handling needed
        new_found_users = remote_users - ori_ga_users
        if new_found_users:
            self.summary["new_accounts"] += len(new_found_users)
            for username in new_found_users:
                self.result["new_accounts"].append(
                    {
                        "asset": str(asset),
                        "username": username,
                    }
                )

        # Gathered accounts that are no longer found on the remote side
        # Mark remote_present=False, flag as pending
        # Accounts that no longer exist on the remote asset are flagged pending and need admin intervention
        lost_users = ori_ga_users - remote_users
        if lost_users:
            queryset.filter(username__in=lost_users).update(
                status=ConfirmOrIgnore.pending, remote_present=False
            )
            self.summary["lost_accounts"] += len(lost_users)
            for username in lost_users:
                self.result["lost_accounts"].append(
                    {
                        "asset": str(asset),
                        "username": username,
                    }
                )
            risk_analyser = AnalyseAccountRisk(self.check_risk)
            risk_analyser.lost_accounts(asset, lost_users)

        # Gathered accounts that outnumber the account list — the account may have been
        # deleted, but its status is already confirmed at this point
        # Mark status as pending so an admin can confirm
        ga_added_users = ori_ga_users - ori_users
        if ga_added_users:
            queryset.filter(username__in=ga_added_users).update(status=ConfirmOrIgnore.pending)

        # Gathered accounts that are fewer than the account list
        # This doesn't seem to need comparing — that's just how it originally is

        # Remote accounts that are fewer than the account list
        # Create a gathered account, mark remote_present=False, status pending

        # Remote accounts that outnumber the account list
        # Normal case, no handling needed — remote accounts get created into gathered
        # accounts, which are then compared

        # Though this should probably also handle status — since it already exists,
        # the status should be confirmed
        (
            queryset.filter(username__in=ori_users)
            .exclude(status=ConfirmOrIgnore.confirmed)
            .update(status=ConfirmOrIgnore.confirmed)
        )

        # Accounts that exist on the remote side are marked as present
        (
            queryset.filter(username__in=remote_users, remote_present=False).update(
                remote_present=True
            )
        )

        # Accounts not found on the asset are marked as not present (and vice versa)
        (
            queryset.exclude(username__in=ori_users)
            .filter(present=True)
            .update(present=False)
        )
        (
            queryset.filter(username__in=ori_users)
            .filter(present=False)
            .update(present=True)
        )

    @bulk_create_decorator(GatheredAccount)
    def create_gathered_account(self, d):
        ga = GatheredAccount()
        for k, v in d.items():
            setattr(ga, k, v)

        return ga

    @bulk_update_decorator(GatheredAccount, update_fields=common_risk_items)
    def update_gathered_account(self, ori_account, d):
        diff = get_items_diff(ori_account, d)
        if not diff:
            return
        for k in diff:
            if k not in common_risk_items:
                continue
            v = d.get(k)
            setattr(ori_account, k, v)
        return ori_account

    def do_run(self, *args, **kwargs):
        super().do_run(*args, **kwargs)
        self.prefetch_origin_account_usernames()
        risk_analyser = AnalyseAccountRisk(self.check_risk)

        for asset, accounts_data in self.asset_account_info.items():
            ori_users = self.ori_asset_usernames[str(asset.id)]
            need_analyser_gather_account = []
            with tmp_to_org(asset.org_id):
                for d in accounts_data:
                    username = d["username"]
                    ori_account = self.ori_gathered_accounts_mapper.get(
                        "{}_{}".format(asset.id, username)
                    )
                    if not ori_account:
                        ga = self.create_gathered_account(d)
                    else:
                        ga = ori_account
                        self.update_gathered_account(ori_account, d)
                    ori_found = username in ori_users
                    need_analyser_gather_account.append((asset, ga, d, ori_found))
                # The order here must not be changed — risk has a foreign key to gathered_account's
                # primary key id, so gathered_account must be fully created before creating risk
                self.create_gathered_account.finish()
                self.update_gathered_account.finish()
                for analysis_data in need_analyser_gather_account:
                    risk_analyser.analyse_risk(*analysis_data)
                self.update_gather_accounts_status(asset)
                if not self.is_sync_account:
                    continue
                gathered_accounts = GatheredAccount.objects.filter(asset=asset)
                GatheredAccount.sync_accounts(gathered_accounts)
                GatheredAccount.objects.filter(
                    asset=asset, username__in=ori_users, present=False
                ).update(
                    present=True
                )
        _update_risk.finish()
        _create_risk.finish()

    def get_report_template(self):
        return "accounts/gather_account_report.html"
