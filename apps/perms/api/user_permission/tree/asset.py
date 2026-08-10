from django.conf import settings
from rest_framework.response import Response

from assets.api import SerializeToTreeNodeMixin
from assets.models import Asset
from common.utils import get_logger
from ..mixin import RebuildTreeMixin
from ..assets import UserAllPermedAssetsApi

logger = get_logger(__name__)

__all__ = [
    'UserAllPermedAssetsAsTreeApi',
    'UserUngroupAssetsAsTreeApi',
]


class AssetTreeMixin(RebuildTreeMixin, SerializeToTreeNodeMixin):
    """ Serialize assets into a tree node structure and return them """
    filter_queryset: callable
    get_queryset: callable

    search_fields = ('name', 'address', 'comment')

    def list(self, request, *args, **kwargs):
        assets = self.filter_queryset(self.get_queryset())
        if request.query_params.get('search'):
            """ Limit the number of results, since imprecise search conditions can return a large amount of meaningless data """
            assets = assets[:999]
        data = self.serialize_assets(assets, 'root')
        return Response(data=data)


class UserAllPermedAssetsAsTreeApi(AssetTreeMixin, UserAllPermedAssetsApi):
    """ The user's 'directly granted assets' as a tree """
    pass


class UserUngroupAssetsAsTreeApi(UserAllPermedAssetsAsTreeApi):
    """ The user's 'ungrouped node assets (directly granted assets)' as a tree """

    def get_assets(self):
        if settings.PERM_SINGLE_ASSET_TO_UNGROUP_NODE:
            return super().get_assets()
        return Asset.objects.none()
