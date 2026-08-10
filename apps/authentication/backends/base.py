from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from common.utils import get_logger
from users.models import User
from authentication.signals import backend_auth_failed
from authentication.errors import reason_choices, reason_user_invalid

UserModel = get_user_model()
logger = get_logger(__file__)


class JMSBaseAuthBackend:

    @staticmethod
    def is_enabled():
        return True

    def has_perm(self, user_obj, perm, obj=None):
        return False

    def user_can_authenticate(self, user):
        """
        Reject users with is_valid=False. Custom user models that don't have
        that attribute are allowed.
        """
        # After a third-party user finishes authenticating, the subsequent get_user retrieval logic should also check whether the user is valid
        is_valid = getattr(user, 'is_valid', None)
        if not is_valid:
            logger.info("User %s is not valid", getattr(user, "username", "<unknown>"))
            return False
        return True

    # allow user to authenticate
    def username_allow_authenticate(self, username):
        return self.allow_authenticate(username=username)

    def user_allow_authenticate(self, user):
        return self.allow_authenticate(user=user)

    def allow_authenticate(self, user=None, username=None):
        if user:
            allowed_backend_paths = user.get_allowed_auth_backend_paths()
        else:
            allowed_backend_paths = User.get_user_allowed_auth_backend_paths(username)
        if allowed_backend_paths is None:
            # A special value of None means no restriction
            return True
        backend_name = self.__class__.__name__
        allowed_backend_names = [path.split('.')[-1] for path in allowed_backend_paths]
        allow = backend_name in allowed_backend_names
        if not allow:
            info = 'User {} skip authentication backend {}, because it not in {}'
            info = info.format(username, backend_name, ','.join(allowed_backend_names))
            logger.info(info)
        return allow

    def get_user(self, user_id):
        """ After a third-party user authenticates successfully, this backend method is called to retrieve the user when assigning request.user """
        try:
            user = UserModel._default_manager.get(pk=user_id)
        except UserModel.DoesNotExist:
            return None
        return user if self.user_can_authenticate(user) else None


class JMSModelBackend(JMSBaseAuthBackend, ModelBackend):
     def user_can_authenticate(self, user):
        return True


class RedirectAuthBackend(JMSBaseAuthBackend):
    backend = None

    def send_backend_auth_failed_signal(self, request, username=None, reason=None):
        default_reason = reason_choices.get(reason_user_invalid, reason)
        reason_code = reason_user_invalid if reason is None else ''
        if reason in reason_choices:
            reason_code = reason
        backend_auth_failed.send(
            sender=self.__class__, username=username, request=request,
            reason=default_reason, backend=self.backend, reason_code=reason_code
        )
