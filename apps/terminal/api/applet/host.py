import uuid

from django.conf import settings
from django.db import transaction
from rest_framework import status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from assets.models import Asset
from common.api import JMSBulkModelViewSet
from common.permissions import IsServiceAccount, IsValidUser
from common.utils import get_logger
from orgs.utils import tmp_to_builtin_org, tmp_to_org, get_current_org_id
from terminal.models import Applet, AppletHost, AppletHostDeployment
from terminal.models.applet.routing import get_asset_routing, filter_hosts_by_routing
from terminal.serializers import (
    AppletHostSerializer, AppletHostDeploymentSerializer,
    AppletHostStartupSerializer, AppletSetupSerializer
)
from terminal.tasks import (
    run_applet_host_deployment,
    run_applet_host_deployment_install_applet,
    run_applet_host_deployment_uninstall_applet
)

__all__ = ['AppletHostViewSet', 'AppletHostDeploymentViewSet']

logger = get_logger(__name__)


class AppletHostViewSet(JMSBulkModelViewSet):
    serializer_class = AppletHostSerializer
    queryset = AppletHost.objects.all()
    filterset_fields = ['name', 'address']
    search_fields = ['name', 'address']
    rbac_perms = {
        'generate_accounts': 'terminal.change_applethost',
    }

    def dispatch(self, request, *args, **kwargs):
        # Keep the caller's org so the asset lookup in `allowed` stays org-scoped
        self._request_org_id = get_current_org_id()
        with tmp_to_builtin_org(system=1):
            return super().dispatch(request, *args, **kwargs)

    def get_permissions(self):
        if self.action == 'startup':
            return [IsServiceAccount()]
        if self.action == 'allowed':
            return [IsValidUser()]
        return super().get_permissions()

    @action(methods=['get'], detail=False)
    def allowed(self, request, *args, **kwargs):
        asset_id = request.query_params.get('asset', '')
        applet_name = request.query_params.get('applet', '')
        if not asset_id or not applet_name:
            return Response(
                {'msg': 'asset and applet are required'}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            uuid.UUID(asset_id)
        except ValueError:
            return Response({'msg': 'invalid asset id'}, status=status.HTTP_400_BAD_REQUEST)

        # Not found and not allowed both return [] so existence is not leaked
        with tmp_to_org(self._request_org_id):
            asset = Asset.objects.filter(id=asset_id).first()
        applet = Applet.objects.filter(name=applet_name, is_active=True).first()
        if not asset or not applet:
            return Response([])

        hosts = applet.filter_available_hosts()
        if not hosts:
            return Response([])
        hosts = list(hosts)
        try:
            from acls.models import ConnectMethodACL
            hosts = ConnectMethodACL.filter_applet_hosts(request.user, applet.name, hosts)
        except Exception as e:
            logger.error("Filter applet hosts by connect method ACL failed: {}".format(e))
            return Response([])

        routing = get_asset_routing(asset)
        if routing is not None:
            hosts = filter_hosts_by_routing(routing, hosts)
        else:
            only_values = list(
                asset.get_labels().filter(name__in=['AppletHostOnly', '仅发布机'])
                .values_list('value', flat=True)
            )
            if only_values:
                hosts = [h for h in hosts if h.name in only_values]

        if not settings.DEBUG_DEV:
            hosts = [h for h in hosts if h.load != 'offline']
        return Response([{'id': str(h.id), 'name': h.name, 'load': h.load} for h in hosts])

    @action(methods=['post'], detail=True, serializer_class=AppletHostStartupSerializer)
    def startup(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance.check_terminal_binding(request)
        return Response({'msg': 'ok'})

    @action(methods=['put'], detail=True, url_path='generate-accounts')
    def generate_accounts(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.generate_accounts()
        return Response({'msg': 'ok'})


class AppletHostDeploymentViewSet(viewsets.ModelViewSet):
    serializer_class = AppletHostDeploymentSerializer
    queryset = AppletHostDeployment.objects.all()
    filterset_fields = ['host', ]
    rbac_perms = (
        ('applets', 'terminal.view_applethostdeployment'),
        ('uninstall', 'terminal.change_applethost'),
    )

    @staticmethod
    def start_deploy(instance, install_applets):
        run_applet_host_deployment.apply_async(
            (instance.id, install_applets,),
            task_id=str(instance.id)
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data
        install_applets = validated_data.pop('install_applets', True)
        instance = serializer.save()
        instance.save_task(instance.id)
        transaction.on_commit(lambda: self.start_deploy(instance, install_applets))
        return Response({'task': str(instance.id)}, status=201)

    @action(methods=['post'], detail=False)
    def applets(self, request, *args, **kwargs):
        hosts = request.data.get('hosts', [])
        applet_id = request.data.get('applet_id', '')
        model = self.get_queryset().model
        hosts_qs = AppletHost.objects.filter(id__in=hosts)
        if not hosts_qs.exists():
            return Response(status=status.HTTP_404_NOT_FOUND)

        objs = [model(host=host) for host in hosts_qs]
        applet_host_deployments = model.objects.bulk_create(objs)
        applet_host_deployment_ids = [str(obj.id) for obj in applet_host_deployments]
        task_id = uuid.uuid4()
        model.objects.filter(id__in=applet_host_deployment_ids).update(task=task_id)
        transaction.on_commit(
            lambda: self.start_install_applet(applet_host_deployment_ids, applet_id, str(task_id))
        )
        return Response({'task': str(task_id)}, status=201)

    @action(methods=['post'], detail=False)
    def uninstall(self, request, *args, **kwargs):
        serializer = AppletSetupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data
        hosts = validated_data.pop('hosts', [])
        applet_id = validated_data.pop('applet_id', '')
        hosts_qs = AppletHost.objects.filter(id__in=hosts)
        if not hosts_qs.exists():
            return Response(status=status.HTTP_404_NOT_FOUND)
        task_id = uuid.uuid4()
        objs = [AppletHostDeployment(host=host, task=task_id) for host in hosts_qs]
        applet_host_deployments = AppletHostDeployment.objects.bulk_create(objs)
        applet_host_deployment_ids = [str(obj.id) for obj in applet_host_deployments]
        transaction.on_commit(
            lambda: self.start_uninstall_applet(applet_host_deployment_ids, applet_id, str(task_id))
        )
        return Response({'task': str(task_id)}, status=201)

    @staticmethod
    def start_install_applet(applet_host_deployment_ids, applet_id, task_id):
        run_applet_host_deployment_install_applet.apply_async(
            (applet_host_deployment_ids, applet_id),
            task_id=str(task_id)
        )

    @staticmethod
    def start_uninstall_applet(applet_host_deployment_ids, applet_id, task_id):
        run_applet_host_deployment_uninstall_applet.apply_async(
            (applet_host_deployment_ids, applet_id),
            task_id=str(task_id)
        )
