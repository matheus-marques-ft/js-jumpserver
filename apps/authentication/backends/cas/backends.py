# -*- coding: utf-8 -*-
#

from django.conf import settings
from django_cas_ng.backends import CASBackend as _CASBackend

from common.utils import get_logger
from ..base import RedirectAuthBackend

__all__ = ['CASBackend']
logger = get_logger(__name__)


class CASBackend(RedirectAuthBackend, _CASBackend):
    backend = settings.AUTH_BACKEND_CAS

    @staticmethod
    def is_enabled():
        return settings.AUTH_CAS

    def authenticate(self, request, ticket, service):
        # Hack here: force the parent class to always take the CAS_CREATE_USER=True path, then call the custom_get_or_create method in authentication/mixins.py
        settings.CAS_CREATE_USER = True
        user = super().authenticate(request, ticket, service)
        if user is None:
            self.send_backend_auth_failed_signal(request=request)
        return user
