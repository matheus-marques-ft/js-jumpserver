import re

from django.apps import apps
from django.conf import settings
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.generators import SchemaGenerator


class CustomSchemaGenerator(SchemaGenerator):
    from_mcp = False

    def get_schema(self, request=None, public=False):
        self.from_mcp = request.query_params.get('mcp') or request.path.endswith('swagger.json')
        return super().get_schema(request, public)


class CustomAutoSchema(AutoSchema):
    def __init__(self, *args, **kwargs):
        self.from_mcp = kwargs.get('from_mcp', False)
        super().__init__(*args, **kwargs)

    def map_parsers(self):
        return ['application/json']

    def map_renderers(self, *args, **kwargs):
        return ['application/json']

    def get_tags(self):
        operation_keys = self._tokenize_path()
        if len(operation_keys) == 1:
            return []
        tags = ['_'.join(operation_keys[:2])]
        return tags
   
    def get_operation(self, path, *args, **kwargs):
        if path.endswith('render-to-json/'):
            return None
        # if not path.startswith('/api/v1/users'):
            # return None
        operation = super().get_operation(path, *args, **kwargs)
        if not operation:
            return operation

        if not operation.get('summary', ''):
            operation['summary'] = operation.get('operationId')

        return operation

    def get_operation_id(self):
        tokenized_path = self._tokenize_path()
        # replace dashes as they can be problematic later in code generation
        tokenized_path = [t.replace('-', '_') for t in tokenized_path]

        action = ''
        if hasattr(self.view, 'action'):
            action = self.view.action

        if not action:
            if self.method == 'GET' and self._is_list_view():
                action = 'list'
            else:
                action = self.method_mapping[self.method.lower()]

        if action == "bulk_destroy":
            action = "bulk_delete"

        if not tokenized_path:
            tokenized_path.append('root')

        if re.search(r'<drf_format_suffix\w*:\w+>', self.path_regex):
            tokenized_path.append('formatted')

        return '_'.join(tokenized_path + [action])

    def get_description(self):
        description = super().get_description()
        base_dir = str(settings.BASE_DIR)
        my_apps = [
            app.label for app in apps.get_app_configs()
            if app.module.__file__ and app.module.__file__.startswith(base_dir)
        ]
        view_app = str(self.view.__class__.__module__.split('.')[0])
        if view_app in my_apps:
            # View docstrings of internal apps are not shown in the docs
            return ''
        else:
            return description

    def get_filter_parameters(self):
        if not self.should_filter():
            return []

        fields = []
        if hasattr(self.view, 'get_filter_backends'):
            backends = self.view.get_filter_backends()
        elif hasattr(self.view, 'filter_backends'):
            backends = self.view.filter_backends
        else:
            backends = []
        for filter_backend in backends:
            fields += self.probe_inspectors(
                self.filter_inspectors, 'get_filter_parameters', filter_backend()
            ) or []
        return fields

    def get_auth(self):
        return [{'Bearer': []}]

    def get_operation_security(self):
        """
        Override the operation security config to uniformly use Bearer token
        """
        return [{'Bearer': []}]

    def get_components_security_schemes(self):
        """
        Override the security scheme definition to avoid authentication class parsing errors
        """
        return {
            'Bearer': {
                'type': 'http',
                'scheme': 'bearer',
                'bearerFormat': 'JWT',
                'description': 'JWT token for API authentication'
            }
        }

    @staticmethod
    def exclude_some_paths(path):
        # Paths can be processed here
        excludes = [
            '/report/', '/render-to-json/', '/suggestions/',
            'executions', 'automations', 'change-secret-records',
            'change-secret-dashboard', '/copy-to-assets/',
            '/move-to-assets/', 'dashboard', 'index', 'countries',
            '/resources/cache/', 'profile/mfa', 'profile/password',
            'profile/permissions', 'prometheus', 'constraints'
        ]
        for p in excludes:
            if path.find(p) >= 0:
                return True
        return False

    def exclude_some_app_model(self, path):
        parts = path.split('/')
        if len(parts) < 5:
            return False

        apps = []
        if self.from_mcp:
            apps = [
                'ops', 'tickets', 'authentication',
                'settings', 'xpack', 'terminal', 'rbac',
                'notifications', 'promethues', 'acls'
            ]

        app_name = parts[3]
        if app_name in apps:
            return True
        models = []
        model = parts[4]
        if self.from_mcp:
            models = [
                'users', 'user-groups', 'users-groups-relations', 'assets', 'hosts', 'devices', 'databases',
                'webs', 'clouds', 'gpts', 'ds', 'customs', 'platforms', 'nodes', 'zones', 'gateways',
                'protocol-settings', 'labels', 'virtual-accounts', 'gathered-accounts', 'account-templates',
                'account-template-secrets', 'account-backups', 'account-backup-executions',
                'change-secret-automations', 'change-secret-executions', 'change-secret-records',
                'gather-account-automations', 'gather-account-executions', 'push-account-automations',
                'push-account-executions', 'push-account-records', 'check-account-automations',
                'check-account-executions', 'account-risks', 'integration-apps', 'asset-permissions',
                'asset-permissions-users-relations', 'asset-permissions-user-groups-relations',
                'asset-permissions-assets-relations', 'asset-permissions-nodes-relations', 'terminal-status',
                'terminals', 'tasks', 'status', 'replay-storages', 'command-storages', 'session-sharing-records',
                'endpoints', 'endpoint-rules', 'applets', 'applet-hosts', 'applet-publications',
                'applet-host-deployments', 'virtual-apps', 'app-providers', 'virtual-app-publications',
                'celery-period-tasks', 'task-executions', 'adhocs', 'playbooks', 'variables', 'ftp-logs',
                'login-logs', 'operate-logs', 'password-change-logs', 'job-logs', 'jobs', 'user-sessions',
                'service-access-logs', 'chatai-prompts', 'super-connection-tokens', 'flows',
                'apply-assets', 'apply-nodes', 'login-acls', 'login-asset-acls', 'command-filter-acls',
                'clipboard-acls', 'command-groups', 'connect-method-acls', 'system-msg-subscriptions', 'roles', 'role-bindings',
                'system-roles', 'system-role-bindings', 'org-roles', 'org-role-bindings', 'content-types',
                'labeled-resources', 'account-backup-plans', 'account-check-engines', 'account-secrets',
                'change-secret', 'integration-applications', 'push-account', 'directories', 'connection-token',
                'groups', 'accounts', 'resource-types', 'favorite-assets', 'activities', 'platform-automation-methods',
            ]
        if model in models:
            return True
        return False

    def is_excluded(self):
        if self.exclude_some_paths(self.path):
            return True
        if self.exclude_some_app_model(self.path):
            return True
        return False

    def get_operation(self, path, *args, **kwargs):
        operation = super().get_operation(path, *args, **kwargs)
        if not operation:
            return operation

        operation_id = operation.get('operationId')
        if 'bulk' in operation_id:
            return None

        if not operation.get('summary', ''):
            operation['summary'] = operation.get('operationId')

        exclude_operations = [
            'orgs_orgs_read', 'orgs_orgs_update', 'orgs_orgs_delete', 
            'orgs_orgs_create', 'orgs_orgs_partial_update',
        ]
        if operation_id in exclude_operations:
            return None
        return operation

# Add OpenAPI extensions for custom fields
from drf_spectacular.extensions import OpenApiSerializerFieldExtension
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.plumbing import build_basic_type
from common.serializers.fields import ObjectRelatedField, LabeledChoiceField, BitChoicesField


class ObjectRelatedFieldExtension(OpenApiSerializerFieldExtension):
    """
    Provide an OpenAPI schema for ObjectRelatedField
    """
    target_class = ObjectRelatedField

    def map_serializer_field(self, auto_schema, direction):
        field = self.target

        # Get the field's basic info
        field_type = 'array' if field.many else 'object'

        if field_type == 'array':
            # If it's a many-to-many relationship
            return {
                'type': 'array',
                'items': self._get_openapi_item_schema(field),
                'description': getattr(field, 'help_text', ''),
                'title': getattr(field, 'label', ''),
            }
        else:
            # If it's a one-to-one relationship
            return {
                'type': 'object',
                'properties': self._get_openapi_properties_schema(field),
                'description': getattr(field, 'help_text', ''),
                'title': getattr(field, 'label', ''),
            }

    def _get_openapi_item_schema(self, field):
        """
        Get the OpenAPI schema for an array item
        """
        return self._get_openapi_object_schema(field)

    def _get_openapi_object_schema(self, field):
        """
        Get the OpenAPI schema for an object
        """
        properties = {}

        # Dynamically analyze the attribute types in attrs
        for attr in field.attrs:
            # Try to get field info from the queryset's model
            field_type = self._infer_field_type(field, attr)
            properties[attr] = {
                'type': field_type,
                'description': f'{attr} field'
            }
        
        return {
            'type': 'object',
            'properties': properties,
            'required': ['id'] if 'id' in field.attrs else []
        }

    def _infer_field_type(self, field, attr_name):
        """
        Intelligently infer the field type
        """
        try:
            # If there is a queryset, try to get field info from the model
            if hasattr(field, 'queryset') and field.queryset is not None:
                model = field.queryset.model
                if hasattr(model, '_meta') and hasattr(model._meta, 'fields'):
                    model_field = model._meta.get_field(attr_name)
                    if model_field:
                        return self._map_django_field_type(model_field)
        except Exception:
            pass

        # If there's no queryset or the field info can't be obtained, use heuristic rules
        return self._heuristic_field_type(attr_name)

    def _map_django_field_type(self, model_field):
        """
        Map a Django field type to an OpenAPI type
        """
        field_type = type(model_field).__name__

        # Integer types
        if 'Integer' in field_type or 'BigInteger' in field_type or 'SmallInteger' in field_type:
            return 'integer'
        # Float types
        elif 'Float' in field_type or 'Decimal' in field_type:
            return 'number'
        # Boolean type
        elif 'Boolean' in field_type:
            return 'boolean'
        # Date/time types
        elif 'DateTime' in field_type or 'Date' in field_type or 'Time' in field_type:
            return 'string'
        # File types
        elif 'File' in field_type or 'Image' in field_type:
            return 'string'
        # Other types default to string
        else:
            return 'string'

    def _heuristic_field_type(self, attr_name):
        """
        Heuristically infer the field type
        """
        # Heuristic rules based on the attribute name

        if attr_name in ['is_active', 'enabled', 'visible'] or attr_name.startswith('is_'):
            return 'boolean'
        elif attr_name in ['count', 'number', 'size', 'amount']:
            return 'integer'
        elif attr_name in ['price', 'rate', 'percentage']:
            return 'number'
        else:
            # Default to string type
            return 'string'

    def _get_openapi_properties_schema(self, field):
        """
        Get the OpenAPI schema for an object's properties
        """
        return self._get_openapi_object_schema(field)['properties']


class LabeledChoiceFieldExtension(OpenApiSerializerFieldExtension):
    """
    Provide an OpenAPI schema for LabeledChoiceField
    """
    target_class = LabeledChoiceField

    def map_serializer_field(self, auto_schema, direction):
        field = self.target
        
        if getattr(field, 'many', False):
            return {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'value': {'type': 'string'},
                        'label': {'type': 'string'}
                    }
                },
                'description': getattr(field, 'help_text', ''),
                'title': getattr(field, 'label', ''),
            }
        else:
            return {
                'type': 'object',
                'properties': {
                    'value': {'type': 'string'},
                    'label': {'type': 'string'}
                },
                'description': getattr(field, 'help_text', ''),
                'title': getattr(field, 'label', ''),
            }


class BitChoicesFieldExtension(OpenApiSerializerFieldExtension):
    """
    Provide an OpenAPI schema for BitChoicesField
    """
    target_class = BitChoicesField

    def map_serializer_field(self, auto_schema, direction):
        field = self.target
        
        return {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'value': {'type': 'string'},
                    'label': {'type': 'string'}
                }
            },
            'description': getattr(field, 'help_text', ''),
            'title': getattr(field, 'label', ''),
        }


class LabelRelatedFieldExtension(OpenApiSerializerFieldExtension):
    """
    Provide an OpenAPI schema for LabelRelatedField
    """
    target_class = 'common.serializers.fields.LabelRelatedField'

    def map_serializer_field(self, auto_schema, direction):
        field = self.target

        # LabelRelatedField returns an object containing id, name, value, color
        return {
            'type': 'object',
            'properties': {
                'id': {
                    'type': 'string',
                    'description': 'Label ID'
                },
                'name': {
                    'type': 'string',
                    'description': 'Label name'
                },
                'value': {
                    'type': 'string',
                    'description': 'Label value'
                },
                'color': {
                    'type': 'string',
                    'description': 'Label color'
                }
            },
            'required': ['id', 'name', 'value'],
            'description': getattr(field, 'help_text', 'Label information'),
            'title': getattr(field, 'label', 'Label'),
        }
