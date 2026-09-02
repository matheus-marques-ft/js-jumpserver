from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.serializers import SecretReadableCheckMixin, SecretReadableMixin
from common.serializers.fields import EncryptedField
from orgs.mixins.serializers import BulkOrgResourceModelSerializer
from .models import Secret

__all__ = ['SecretSerializer', 'SecretValueSerializer']


class SecretSerializer(BulkOrgResourceModelSerializer):
    is_expired = serializers.BooleanField(read_only=True)
    value = EncryptedField(
        label=_('Value'), required=False, allow_blank=True, allow_null=True, max_length=40960,
    )

    class Meta:
        model = Secret
        fields = [
            'id', 'source', 'name', 'value', 'expiration_date', 'is_active', 'is_expired',
            'created_by', 'date_created', 'date_updated', 'comment',
        ]
        # is_expired is a declared field (see above), not a model field - DRF uses declared
        # fields as-is and never consults read_only_fields/extra_kwargs for them, so listing
        # it here would be inert; its read_only=True kwarg above is what actually applies.
        read_only_fields = ['created_by', 'date_created', 'date_updated']


class SecretValueSerializer(SecretReadableCheckMixin, SecretReadableMixin, SecretSerializer):
    class Meta(SecretSerializer.Meta):
        extra_kwargs = {'value': {'write_only': False}}
        secret_fields = ['value']
