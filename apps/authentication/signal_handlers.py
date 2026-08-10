from django.conf import settings
from django.contrib.auth import user_logged_in, BACKEND_SESSION_KEY
from django.core.cache import cache
from django.dispatch import receiver
from django_cas_ng.signals import cas_user_authenticated

from jumpserver.settings.auth import AUTHENTICATION_BACKENDS_THIRD_PARTY
from audits.models import UserSession
from common.sessions.cache import user_session_manager
from .signals import post_auth_failed, backend_auth_failed

from .backends.oauth2_provider.signal_handlers import *


@receiver(user_logged_in)
def on_user_auth_login_success(sender, user, request, **kwargs):
    # Invalidate perms cache
    user.expire_rbac_perms_cache()

    # MFA is enabled and hasn't been verified yet; it can be verified globally, the middleware can globally manage MFA for third-party auth such as OIDC
    if settings.SECURITY_MFA_AUTH_ENABLED_FOR_THIRD_PARTY \
            and user.mfa_enabled \
            and not request.session.get('auth_mfa'):
        request.session['auth_mfa_required'] = 1
    auth_backend = request.session.get('auth_backend', request.session.get(BACKEND_SESSION_KEY))
    if not request.session.get("auth_third_party_done") and \
            auth_backend in AUTHENTICATION_BACKENDS_THIRD_PARTY:
        request.session['auth_third_party_required'] = 1

    user_session_id = request.session.get('user_session_id')
    UserSession.objects.filter(id=user_session_id).update(key=request.session.session_key)
    # Single sign-on, automatically logged out when exceeded
    if settings.USER_LOGIN_SINGLE_MACHINE_ENABLED:
        lock_key = 'single_machine_login_' + str(user.id)
        session_key = cache.get(lock_key)
        if session_key and session_key != request.session.session_key:
            user_session_manager.remove(session_key)
            UserSession.objects.filter(key=session_key).delete()
        cache.set(lock_key, request.session.session_key, None)

    lang = request.COOKIES.get('django_language')
    if lang:
        user.lang = lang


@receiver(backend_auth_failed)
def on_user_login_failed(sender, username, request, reason, backend, **kwargs):
    request.session['auth_backend'] = backend
    post_auth_failed.send(
        sender, username=username, request=request, reason=reason,
        reason_code=kwargs.get('reason_code') or '',
        reason_params=kwargs.get('reason_params') or {},
    )
