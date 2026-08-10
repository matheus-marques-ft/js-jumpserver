# -*- coding: utf-8 -*-
#
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from common.serializers import CommonModelSerializer, \
    CommonBulkModelSerializer
from common.validators import ProjectUniqueValidator
from ..utils import get_current_org_id_for_serializer

__all__ = [
    "OrgResourceSerializerMixin", "BulkOrgResourceModelSerializer",
    "OrgResourceModelSerializerMixin",
]


class OrgResourceSerializerMixin(serializers.Serializer):
    """
    When bulk-operating on resources via the API, automatically set the required org_id attribute of each resource to current_org_id
    (also prepares for the Model's unique_together validation in serializer.is_valid())
    Since a HiddenField cannot be read, org_id cannot be retrieved when the API fetches asset info,
    but coco needs the asset's org_id field, so it was changed to a CharField type
    """
    org_id = serializers.ReadOnlyField(default=get_current_org_id_for_serializer, label=_("Organization"))
    org_name = serializers.CharField(label=_("Org name"), read_only=True)
    add_org_fields = True

    def get_validators(self):
        _validators = super().get_validators()
        validators = []

        for v in _validators:
            if isinstance(v, UniqueTogetherValidator) and "org_id" in v.fields:
                v = ProjectUniqueValidator(v.queryset, v.fields)
            validators.append(v)
        return validators

    def get_field_names(self, declared_fields, info):
        fields = super().get_field_names(declared_fields, info)
        if self.add_org_fields:
            fields.extend(["org_id", "org_name"])
        return fields


class OrgResourceModelSerializerMixin(OrgResourceSerializerMixin, CommonModelSerializer):
    pass


class BulkOrgResourceModelSerializer(OrgResourceSerializerMixin, CommonBulkModelSerializer):
    pass
