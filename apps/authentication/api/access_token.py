from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _

from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.status import HTTP_204_NO_CONTENT, HTTP_404_NOT_FOUND

from oauth2_provider.models import get_access_token_model

from common.api import JMSModelViewSet
from rbac.permissions import RBACPermission
from ..serializers import AccessTokenSerializer


AccessToken = get_access_token_model()


class AccessTokenViewSet(JMSModelViewSet):
    """
    OAuth2 Access Token management viewset
    Users can only view and revoke their own access tokens
    """
    serializer_class = AccessTokenSerializer
    permission_classes = [RBACPermission]
    http_method_names = ['get', 'options', 'delete']
    rbac_perms = {
        'revoke': 'oauth2_provider.delete_accesstoken',
    }

    def get_queryset(self):
        """Only return the current user's access tokens, ordered by creation time descending"""
        return AccessToken.objects.filter(user=self.request.user).order_by('-created')

    @action(methods=['DELETE'], detail=True, url_path='revoke')
    def revoke(self, request, *args, **kwargs):
        """
        Revoke the access token and its associated refresh token
        Returns 404 if the token does not exist or does not belong to the current user
        """
        token = get_object_or_404(
            AccessToken.objects.filter(user=request.user),
            id=kwargs['pk']
        )
        # Prefer revoking the refresh token, which will automatically revoke the associated access token
        token_to_revoke = token.refresh_token if token.refresh_token else token
        token_to_revoke.revoke()
        return Response(status=HTTP_204_NO_CONTENT)
