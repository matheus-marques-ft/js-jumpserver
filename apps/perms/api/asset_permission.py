# -*- coding: utf-8 -*-
#

from orgs.mixins.api import OrgBulkModelViewSet
from perms import serializers
from perms.filters import AssetPermissionFilter
from perms.models import AssetPermission

__all__ = ['AssetPermissionViewSet']


class AssetPermissionViewSet(OrgBulkModelViewSet):
    """
    API for listing, creating, updating and deleting asset permissions
    """
    model = AssetPermission
    serializer_classes = {
        'default': serializers.AssetPermissionSerializer,
        'list': serializers.AssetPermissionListSerializer,
    }
    filterset_class = AssetPermissionFilter
    search_fields = ('name',)
