# ~*~ coding: utf-8 ~*~
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.serializers.fields import LabeledChoiceField
from common.utils import pretty_string, is_uuid, get_logger
from terminal.const import RiskLevelChoices
from terminal.models import Command

logger = get_logger(__name__)
__all__ = ['SessionCommandSerializer', 'InsecureCommandAlertSerializer']


class SimpleSessionCommandSerializer(serializers.ModelSerializer):
    """ Simple Session command serializer class, used to extract common fields """
    user = serializers.CharField(label=_("User"))  # Limited to 64 characters, see validate_user
    asset = serializers.CharField(max_length=128, label=_("Asset"))
    input = serializers.CharField(label=_("Command"))
    session = serializers.CharField(max_length=36, label=_("Session"))
    risk_level = LabeledChoiceField(
        choices=RiskLevelChoices.choices,
        required=False, label=_("Risk level"),
    )
    org_id = serializers.CharField(
        max_length=36, required=False, default='', allow_null=True, allow_blank=True
    )

    class Meta:
        # Inherit ModelSerializer to fix the swagger issue where risk_level type is object
        model = Command
        fields = ['user', 'asset', 'input', 'session', 'risk_level', 'org_id']

    def validate_user(self, value):
        if len(value) > 64:
            value = value[:32] + value[-32:]
        return value


class InsecureCommandAlertSerializer(SimpleSessionCommandSerializer):
    cmd_filter_acl = serializers.CharField(
        max_length=36, required=False, label=_("Command Filter ACL")
    )
    cmd_group = serializers.CharField(
        max_length=36, required=True, label=_("Command Group")
    )

    class Meta(SimpleSessionCommandSerializer.Meta):
        fields = SimpleSessionCommandSerializer.Meta.fields + [
            'cmd_filter_acl', 'cmd_group', 'timestamp'
        ]

    def validate(self, attrs):
        if not is_uuid(attrs['cmd_filter_acl']):
            raise serializers.ValidationError(
                _("Invalid command filter ACL id")
            )
        if not is_uuid(attrs['cmd_group']):
            raise serializers.ValidationError(
                _("Invalid command group id")
            )
        if not is_uuid(attrs['session']):
            raise serializers.ValidationError(
                _("Invalid session id")
            )
        return super().validate(attrs)


class SessionCommandSerializerMixin(serializers.Serializer):
    """Use this class as the base Command Log Serializer class, for serialization"""
    id = serializers.UUIDField(read_only=True)
    # Limited to 64 characters; can't be migrated directly to 128 characters since the command table has a large amount of data
    account = serializers.CharField(label=_("Account"))
    output = serializers.CharField(allow_blank=True, label=_("Output"))
    timestamp = serializers.IntegerField(label=_('Timestamp'))
    timestamp_display = serializers.DateTimeField(read_only=True, label=_('Datetime'))
    remote_addr = serializers.CharField(read_only=True, label=_('Remote Address'))

    def validate_account(self, value):
        if len(value) > 64:
            value = pretty_string(value, 64)
        return value


class SessionCommandSerializer(SessionCommandSerializerMixin, SimpleSessionCommandSerializer):
    """ Field ordering serializer class """

    class Meta(SimpleSessionCommandSerializer.Meta):
        fields = SimpleSessionCommandSerializer.Meta.fields + [
            'id', 'account', 'output', 'timestamp', 'timestamp_display', 'remote_addr'
        ]
