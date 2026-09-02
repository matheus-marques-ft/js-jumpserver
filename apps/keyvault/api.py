from django.utils.translation import gettext_lazy as _
from rest_framework.serializers import ValidationError

from authentication.backends.drf import SignatureAuthentication
from authentication.permissions import ConfirmType, UserConfirmation
from orgs.mixins.api import OrgBulkModelViewSet
from rbac.permissions import RBACPermission
from . import serializers
from .models import Secret

__all__ = ['SecretViewSet', 'SecretValueViewSet']


class SecretViewSet(OrgBulkModelViewSet):
    model = Secret
    filterset_fields = ('id', 'name', 'source', 'is_active')
    search_fields = ('name', 'source')
    serializer_classes = {
        'default': serializers.SecretSerializer,
    }

    def perform_create(self, serializer):
        clone_from = self.request.query_params.get('clone_from')
        source = None
        if clone_from:
            source = Secret.objects.filter(pk=clone_from).first()
            if source is None:
                raise ValidationError({'clone_from': _('Invalid secret')})
            if not self.request.user.has_perm('keyvault.view_secretvalue'):
                self.permission_denied(self.request)

        instance = serializer.save()
        if source:
            instance.value = source.value
            instance.save(update_fields=['value'])


class SecretValueViewSet(OrgBulkModelViewSet):
    model = Secret
    http_method_names = ['get', 'head', 'options']
    filterset_fields = ('id', 'name', 'source', 'is_active')
    search_fields = ('name', 'source')
    serializer_classes = {
        'default': serializers.SecretValueSerializer,
    }
    rbac_perms = {
        'list': 'keyvault.view_secretvalue',
        'retrieve': 'keyvault.view_secretvalue',
    }

    def get_permissions(self):
        classes = [RBACPermission]
        # A conta de serviço (AccessKey/HTTP Signature, sem sessão de navegador) nunca
        # consegue satisfazer a reconfirmação de MFA baseada em sessão - só usuários
        # interativos (Lina) passam por essa checagem, igual Account.secret hoje.
        if not isinstance(self.request.successful_authenticator, SignatureAuthentication):
            classes.append(UserConfirmation.require(ConfirmType.MFA))
        return [cls() for cls in classes]
