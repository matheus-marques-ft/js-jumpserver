from django.core.exceptions import PermissionDenied

from authentication.backends.base import JMSBaseAuthBackend


class RBACBackend(JMSBaseAuthBackend):
    """ Only performs permission validation """
    @staticmethod
    def is_enabled():
        return True

    def authenticate(self):
        return None

    def username_allow_authenticate(self, username):
        return False

    def has_perm(self, user_obj, perm, obj=None):
        # Scanners tend to be sensitive about '*', so it's changed to 'none' here,
        # even though this '*' is actually a marker we defined ourselves
        if perm == 'none':
            return True
        if not user_obj.is_active or not perm:
            raise PermissionDenied()
        if isinstance(perm, str):
            perm_set = set(i.strip() for i in perm.split('|'))
        elif isinstance(perm, (list, tuple, set)):
            perm_set = set(perm)
        else:
            raise ValueError('perm must be str, list, tuple or set')
        has_perm = bool(perm_set & set(user_obj.perms))
        if not has_perm:
            raise PermissionDenied()
        return has_perm

    # def has_module_perms(self, user_obj, app_label):
    #     return True
