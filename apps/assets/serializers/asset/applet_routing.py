# -*- coding: utf-8 -*-
#
from collections.abc import Mapping

from django.db.models import Model
from django.db.transaction import atomic
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.utils import get_logger

__all__ = ['AppletRoutingMixin']

logger = get_logger(__name__)


class AppletRoutingMixin(serializers.Serializer):
    """
    Flat applet routing fields for asset serializers.
    The routing lives in terminal.AppletHostRouting; terminal models are
    imported lazily here so apps/assets never imports terminal at module level.
    Routing is only written when at least one applet_* key is in the payload.
    """
    APPLET_ROUTING_FIELDS = ['applet_balance', 'applet_host', 'applet_excluded_hosts']

    applet_balance = serializers.BooleanField(
        required=False, label=_('Balance (any applet host)')
    )
    applet_host = serializers.UUIDField(
        required=False, allow_null=True, label=_('Applet host')
    )
    applet_excluded_hosts = serializers.ListField(
        child=serializers.UUIDField(), required=False, label=_('Excluded applet hosts')
    )

    # ---- read ----

    @staticmethod
    def _default_applet_routing():
        return {'applet_balance': True, 'applet_host': None, 'applet_excluded_hosts': []}

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data.update(self._applet_routing_representation(instance))
        return data

    def _applet_routing_representation(self, instance):
        defaults = self._default_applet_routing()
        try:
            from terminal.models.applet.routing import get_asset_routing
            routing = get_asset_routing(instance)
            if routing is None:
                return defaults
            return {
                'applet_balance': routing.balance,
                'applet_host': str(routing.host_id) if routing.host_id else None,
                'applet_excluded_hosts': [
                    str(i) for i in routing.excluded_hosts.values_list('id', flat=True)
                ],
            }
        except Exception as e:
            logger.warning(f'Read applet routing for asset {getattr(instance, "id", None)} failed: {e}')
            return defaults

    # ---- write ----

    def _applet_routing_keys(self):
        data = getattr(self, 'initial_data', None)
        if not isinstance(data, Mapping):
            return []
        return [k for k in self.APPLET_ROUTING_FIELDS if k in data]

    def _merge_applet_routing(self, payload, instance):
        """
        Effective (balance, host_id, excluded_ids) after applying the given keys
        over the current routing row (or the defaults when there is none).
        """
        balance, host_id, excluded_ids = True, None, []
        if isinstance(instance, Model):
            from terminal.models.applet.routing import get_asset_routing
            routing = get_asset_routing(instance)
            if routing is not None:
                balance = routing.balance
                host_id = routing.host_id
                excluded_ids = list(routing.excluded_hosts.values_list('id', flat=True))

        if 'applet_balance' in payload:
            balance = payload['applet_balance']
        if 'applet_host' in payload:
            host_id = payload['applet_host']
        if 'applet_excluded_hosts' in payload:
            excluded_ids = payload['applet_excluded_hosts'] or []

        host_id = str(host_id) if host_id else None
        excluded_ids = [str(i) for i in excluded_ids]
        return bool(balance), host_id, excluded_ids

    def validate(self, attrs):
        attrs = super().validate(attrs)
        keys = self._applet_routing_keys()
        # Only keys actually sent are kept (BooleanField gives False for missing html input)
        for k in self.APPLET_ROUTING_FIELDS:
            if k not in keys:
                attrs.pop(k, None)
        if not keys:
            return attrs

        payload = {k: attrs[k] for k in keys if k in attrs}
        balance, host_id, excluded_ids = self._merge_applet_routing(payload, self.instance)
        if not balance and not host_id:
            raise serializers.ValidationError(
                {'applet_host': _('Select an applet host or enable balancing')}
            )
        if host_id and host_id in excluded_ids:
            raise serializers.ValidationError(
                {'applet_excluded_hosts': _('The selected applet host cannot be excluded')}
            )
        return attrs

    def _pop_applet_routing(self, validated_data):
        return {k: validated_data.pop(k) for k in self.APPLET_ROUTING_FIELDS if k in validated_data}

    def _save_applet_routing(self, instance, payload):
        from terminal.models.applet.routing import save_asset_routing
        balance, host_id, excluded_ids = self._merge_applet_routing(payload, instance)
        try:
            save_asset_routing(instance, balance, host_id, excluded_ids)
        except ValueError as e:
            raise serializers.ValidationError({'applet_host': str(e)})

    def create(self, validated_data):
        payload = self._pop_applet_routing(validated_data)
        with atomic():
            instance = super().create(validated_data)
            if payload:
                self._save_applet_routing(instance, payload)
        return instance

    def update(self, instance, validated_data):
        payload = self._pop_applet_routing(validated_data)
        with atomic():
            instance = super().update(instance, validated_data)
            if payload:
                self._save_applet_routing(instance, payload)
        return instance
