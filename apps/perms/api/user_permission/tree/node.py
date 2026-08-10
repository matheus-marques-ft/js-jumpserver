from rest_framework.response import Response

from assets.api import SerializeToTreeNodeMixin
from common.utils import get_logger

from ..nodes import (
    UserAllPermedNodesApi,
    UserPermedNodeChildrenApi,
)

logger = get_logger(__name__)

__all__ = [
    'UserAllPermedNodesAsTreeApi',
    'UserPermedNodeChildrenAsTreeApi',
]


class NodeTreeMixin(SerializeToTreeNodeMixin):
    filter_queryset: callable
    get_queryset: callable

    def list(self, request, *args, **kwargs):
        nodes = self.filter_queryset(self.get_queryset())
        data = self.serialize_nodes(nodes, with_asset_amount=True)
        return Response(data)


class UserAllPermedNodesAsTreeApi(NodeTreeMixin, UserAllPermedNodesApi):
    """ The user's 'granted nodes' as a tree """
    pass


class UserPermedNodeChildrenAsTreeApi(NodeTreeMixin, UserPermedNodeChildrenApi):
    """ Tree of child nodes under a node granted to the user """
    pass


