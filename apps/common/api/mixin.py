# -*- coding: utf-8 -*-
#
from collections import defaultdict
from contextlib import nullcontext
from itertools import chain
from typing import Callable

from django.conf import settings
from django.db import models
from django.db.models.signals import m2m_changed
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.settings import api_settings

from common.drf.filters import (
    IDSpmFilterBackend, CustomFilterBackend, IDInFilterBackend,
    IDNotFilterBackend, NotOrRelFilterBackend, LabelFilterBackend
)
from common.utils import get_logger, lazyproperty
from common.utils import is_uuid
from orgs.utils import tmp_to_org, tmp_to_root_org
from .action import RenderToJsonMixin
from .serializer import SerializerMixin

__all__ = [
    'CommonApiMixin', 'PaginatedResponseMixin', 'RelationMixin',
    'ExtraFilterFieldsMixin'
]

logger = get_logger(__name__)


class PaginatedResponseMixin:

    def get_paginated_response_from_queryset(self, queryset):
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class RelationMixin:
    m2m_field = None
    from_field = None
    to_field = None
    to_model = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        assert self.m2m_field is not None, '''
        `m2m_field` should not be `None`
        '''

        self.from_field = self.m2m_field.m2m_field_name()
        self.to_field = self.m2m_field.m2m_reverse_field_name()
        self.to_model = self.m2m_field.related_model
        self.through = getattr(self.m2m_field.model, self.m2m_field.attname).through

    def get_queryset(self):
        # Note: `get_queryset` is intercepted here without calling `super`
        queryset = self.through.objects.all()
        return queryset

    def send_m2m_changed_signal(self, instances, action):
        if not isinstance(instances, list):
            instances = [instances]

        from_to_mapper = defaultdict(list)

        for i in instances:
            to_id = getattr(i, self.to_field).id
            # TODO: optimize, should not query the database every time
            from_obj = getattr(i, self.from_field)
            from_to_mapper[from_obj].append(to_id)

        for from_obj, to_ids in from_to_mapper.items():
            m2m_changed.send(
                sender=self.through, instance=from_obj, action=action,
                reverse=False, model=self.to_model, pk_set=to_ids
            )

    def perform_create(self, serializer):
        instance = serializer.save()
        self.send_m2m_changed_signal(instance, 'post_add')

    def perform_destroy(self, instance):
        instance.delete()
        self.send_m2m_changed_signal(instance, 'post_remove')


class QuerySetMixin:
    action: str
    request: Request
    get_serializer_class: Callable
    get_queryset: Callable
    slug_field = 'name'

    def get_object(self):
        pk = self.kwargs.get(self.lookup_field)
        if not pk or is_uuid(pk) or pk.isdigit():
            return super().get_object()
        return self.get_queryset().get(**{self.slug_field: pk})

    def limit_queryset_if_no_page(self, queryset):
        if self.request.query_params.get('format') in ['csv', 'xlsx']:
            return queryset
        action = getattr(self, 'action', None)
        if action != 'list':
            return queryset

        # If the paginator has a limit set, don't restrict it
        if self.paginator and self.paginator.get_limit(self.request):
            return queryset

        # If the paginator has no limit set, don't restrict it
        if getattr(self, 'page_no_limit', False):
            return queryset

        if not settings.DEFAULT_PAGE_SIZE:
            return queryset

        return queryset[:settings.DEFAULT_PAGE_SIZE]

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        if not hasattr(self, 'action'):
            return queryset
        if self.action == 'metadata':
            queryset = queryset.none()
        queryset = self.limit_queryset_if_no_page(queryset)
        queryset = self.setup_eager_loading(queryset)
        return queryset

    def setup_eager_loading(self, queryset, is_paginated=False):
        is_export_request = self.request.query_params.get('format') in ['csv', 'xlsx']
        no_request_page = self.request.query_params.get('limit') is None
        # When not paginated, this path is usually skipped because it would consume an extra sql query; better to query once when paginating
        if not is_export_request and not is_paginated and not no_request_page:
            return queryset

        serializer_class = self.get_serializer_class()
        if not serializer_class:
            return queryset

        if hasattr(serializer_class, 'setup_eager_loading'):
            queryset = serializer_class.setup_eager_loading(queryset)

        if hasattr(serializer_class, 'setup_eager_labels'):
            queryset = serializer_class.setup_eager_labels(queryset)

        should_attach_counts = is_paginated or getattr(self, 'action', None) == 'list'
        if should_attach_counts and hasattr(serializer_class, 'attach_relation_counts'):
            queryset = serializer_class.attach_relation_counts(queryset)
        return queryset

    def paginate_queryset(self, queryset):
        page = super().paginate_queryset(queryset)
        model = getattr(queryset, 'model', None)
        if not model or hasattr(queryset, 'custom'):
            return page

        serializer_class = self.get_serializer_class()
        if page and serializer_class:
            # Must return ids, used for ordering
            queryset, ids = self._get_page_again(page, model)
            page = self.setup_eager_loading(queryset, is_paginated=True)
            page_mapper = {str(obj.id): obj for obj in page}
            page = [page_mapper.get(_id) for _id in ids if _id in page_mapper]
        return page

    def _get_page_again(self, page, model):
        """
        Since setup_eager_loading requires a queryset structure, it must be reconstructed
        """
        id_org_mapper = {str(obj.id): getattr(obj, 'org_id', None) for obj in page}
        ids = list(id_org_mapper.keys())
        org_ids = list(set(id_org_mapper.values()) - {None})

        if not org_ids:
            context = nullcontext()
        elif len(org_ids) == 1:
            context = tmp_to_org(org_ids[0])
        else:
            context = tmp_to_root_org()

        with context:
            page = model.objects.filter(id__in=ids)
        return page, ids


class ExtraFilterFieldsMixin:
    """
    Extra api filter
    """
    default_added_filters = (
        CustomFilterBackend, IDSpmFilterBackend, IDInFilterBackend,
        IDNotFilterBackend, LabelFilterBackend
    )
    filter_backends = api_settings.DEFAULT_FILTER_BACKENDS
    extra_filter_fields = []
    extra_filter_backends = []

    def set_compatible_fields(self):
        """
        Compatible with the old filter_fields
        """
        if not hasattr(self, 'filter_fields') and hasattr(self, 'filterset_fields'):
            self.filter_fields = self.filterset_fields

    def get_filter_backends(self):
        self.set_compatible_fields()
        if self.filter_backends != self.__class__.filter_backends:
            return self.filter_backends
        backends = list(chain(
            self.filter_backends,
            self.default_added_filters,
            self.extra_filter_backends,
        ))
        # This must be placed last
        backends.append(NotOrRelFilterBackend)
        return backends

    def filter_queryset(self, queryset):
        for backend in self.get_filter_backends():
            queryset = backend().filter_queryset(self.request, queryset, self)
        return queryset


class OrderingFielderFieldsMixin:
    """
    Extra api ordering
    """
    ordering_fields = None
    extra_ordering_fields = []

    @lazyproperty
    def ordering_fields(self):
        return self._get_ordering_fields()

    def _get_ordering_fields(self):
        if isinstance(self.__class__.ordering_fields, (list, tuple)):
            return self.__class__.ordering_fields

        try:
            valid_fields = self.get_valid_ordering_fields()
        except Exception as e:
            logger.debug('get_valid_ordering_fields error: %s, pass' % e)
            # Do NOT use it this way here, it will duplicate the logging; not sure why either
            # logging.debug('get_valid_ordering_fields error: %s' % e)
            valid_fields = []

        fields = list(chain(
            valid_fields,
            self.extra_ordering_fields
        ))
        return fields

    def get_valid_ordering_fields(self):
        if getattr(self, 'model', None):
            model = self.model
        elif getattr(self, 'queryset', None):
            model = self.queryset.model
        else:
            queryset = self.get_queryset()
            model = None if isinstance(queryset, list) else queryset.model

        if not model:
            return []

        excludes_fields = (
            models.UUIDField, models.Model, models.ForeignKey,
            models.FileField, models.JSONField, models.ManyToManyField,
            models.DurationField,
        )
        valid_fields = []
        for field in model._meta.fields:
            if isinstance(field, excludes_fields):
                continue
            valid_fields.append(field.name)
        return valid_fields


class CommonApiMixin(
    SerializerMixin, QuerySetMixin, ExtraFilterFieldsMixin,
    OrderingFielderFieldsMixin, RenderToJsonMixin, PaginatedResponseMixin
):
    def is_swagger_request(self):
        return getattr(self, 'swagger_fake_view', False) or \
            getattr(self, 'raw_action', '') == 'metadata'
