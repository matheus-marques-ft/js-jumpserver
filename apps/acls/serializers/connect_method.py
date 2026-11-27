from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.serializers.mixin import CommonBulkModelSerializer
from .base import BaseUserAssetAccountACLSerializer as BaseSerializer
from ..const import ActionChoices
from ..models import ConnectMethodACL

__all__ = ["ConnectMethodACLSerializer"]


class ConnectMethodACLSerializer(BaseSerializer, CommonBulkModelSerializer):
    applet_hosts = serializers.ListField(
        child=serializers.UUIDField(), required=False, label=_('Applet hosts'),
        help_text=_(
            'Applet hosts this rule allows (accept) or denies (reject) for the selected '
            'connect methods. Leave empty to apply the rule to the whole connect method.'
        )
    )

    @staticmethod
    def validate_applet_hosts(value):
        # O campo do modelo e JSON: guarda strings, nao objetos UUID.
        return [str(i) for i in value]

    class Meta(BaseSerializer.Meta):
        model = ConnectMethodACL
        fields = [
            i for i in BaseSerializer.Meta.fields + ['connect_methods', 'applet_hosts']
            if i not in ['assets', 'accounts', 'org_id']
        ]
        action_choices_exclude = BaseSerializer.Meta.action_choices_exclude + [
            ActionChoices.review,
            ActionChoices.notice,
            ActionChoices.face_verify,
            ActionChoices.face_online,
            ActionChoices.change_secret
        ]
