#!/usr/bin/env python
# -*- coding: utf-8 -*-
#

from rest_framework_bulk.routes import BulkRouter

from . import api

app_name = 'keyvault'

router = BulkRouter()
router.register(r'secrets', api.SecretViewSet, 'secret')
router.register(r'secret-values', api.SecretValueViewSet, 'secret-value')

urlpatterns = [
]

urlpatterns += router.urls
