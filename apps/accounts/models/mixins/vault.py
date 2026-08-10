from django.db import models
from django.db.models.signals import post_save
from django.utils.translation import gettext_lazy as _

from accounts.const import VaultTypeChoices
from common.db import fields

__all__ = ['VaultQuerySetMixin', 'VaultManagerMixin', 'VaultModelMixin']


VAULT_SAVED_SECRET_MARK = '# Secret-has-been-saved-to-vault #'


class VaultSecretField(fields.EncryptTextField):
    """Keep the OpenBao remote-secret marker readable in the local database."""

    @staticmethod
    def _should_store_marker_plaintext():
        from django.conf import settings

        return (
            settings.VAULT_ENABLED
            and settings.VAULT_BACKEND == VaultTypeChoices.openbao
        )

    def from_db_value(self, value, expression, connection, context=None):
        if value == VAULT_SAVED_SECRET_MARK:
            return value
        return super().from_db_value(value, expression, connection, context)

    def get_prep_value(self, value):
        if value == VAULT_SAVED_SECRET_MARK and self._should_store_marker_plaintext():
            return value
        return super().get_prep_value(value)


class VaultQuerySetMixin(models.QuerySet):

    def update(self, **kwargs):
        """
           1. Replace secret with _secret
           2. Trigger the post_save signal
        """
        if 'secret' in kwargs:
            kwargs.update({
                '_secret': kwargs.pop('secret')
            })
        rows = super().update(**kwargs)

        # Query separately to get the updated object
        ids = self.values_list('id', flat=True)
        objs = self.model.objects.filter(id__in=ids)
        for obj in objs:
            post_save.send(obj.__class__, instance=obj, created=False)
        return rows


class VaultManagerMixin(models.Manager):
    """ Trigger the post_save signal under bulk_create and bulk_update operations """

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False):
        objs = super().bulk_create(objs, batch_size=batch_size, ignore_conflicts=ignore_conflicts)
        for obj in objs:
            post_save.send(obj.__class__, instance=obj, created=True)
        return objs

    def bulk_update(self, objs, fields, batch_size=None):
        fields = ["_secret" if field == "secret" else field for field in fields]
        super().bulk_update(objs, fields, batch_size=batch_size)
        for obj in objs:
            post_save.send(obj.__class__, instance=obj, created=False)
        return objs


class VaultModelMixin(models.Model):
    _secret = VaultSecretField(blank=True, null=True, verbose_name=_('Secret'))
    is_sync_metadata = True

    class Meta:
        abstract = True

    # Cache the secret value, lazy-property cannot be used
    __secret = None

    @property
    def secret(self) -> str:
        if self.__secret:
            return self.__secret
        from accounts.backends import vault_client
        secret = vault_client.get(self)
        if not secret and not self.secret_has_save_to_vault:
            # If vault_client cannot get it and the secret has not been saved to the vault, get it from self._secret
            secret = self._secret
        self.__secret = secret
        return self.__secret

    @secret.setter
    def secret(self, value):
        """
        When saving, this is handled via the post_save signal listener:
        first saved to the db, then saved to the vault while deleting the local db _secret value
        """
        self._secret = value
        self.__secret = value

    _secret_save_to_vault_mark = VAULT_SAVED_SECRET_MARK

    def mark_secret_save_to_vault(self):
        self._secret = self._secret_save_to_vault_mark
        self.skip_history_when_saving = True
        # Avoid calling overridden `save()` on concrete models (e.g. AccountTemplate)
        # which may mutate `secret/_secret` again and cause post_save recursion.
        super(VaultModelMixin, self).save(update_fields=['_secret'])

    @property
    def secret_has_save_to_vault(self):
        return self._secret == self._secret_save_to_vault_mark

    def save(self, *args, **kwargs):
        """ Handle the _secret data via the post_save signal """
        update_fields = kwargs.get('update_fields')
        if update_fields and 'secret' in update_fields:
            update_fields.remove('secret')
            update_fields.append('_secret')
        return super().save(*args, **kwargs)
