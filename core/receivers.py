from django.dispatch import receiver
import sys
from django.apps import apps
from django.db.models.signals import post_save, post_delete
from contextlib import suppress
from core.models.user import Officer, Role, UserRole
from django.core.cache import cache


@receiver([post_save, post_delete], sender=Officer)
def _post_save_eo_receiver(sender, instance, **kwargs):
    with suppress(AttributeError):
        cache.delete(f"user_eo_{instance.code}")
        cache.delete(f"rights_{instance.code}")


def invalidate_role_rights(role_id):
    """Drop the cached rights and admin flag of every user holding the role.

    Assignments are read without a validity filter: dropping the keys of a
    user whose assignment has expired is harmless, keeping a stale one is not.
    """
    user_ids = set(UserRole.objects.filter(role_id=role_id).values_list("user_id", flat=True))
    cache.delete_many([f"{prefix}{user_id}" for user_id in user_ids for prefix in ("rights_", "is_admin_")])


@receiver([post_save, post_delete], sender=Role)
def _post_save_rolerights_receiver(sender, instance, **kwargs):
    with suppress(AttributeError):
        invalidate_role_rights(instance.pk)


if "claim" in sys.modules:
    ClaimAdmin = apps.get_model("core", "ClaimAdmin")

    @receiver([post_save, post_delete], sender=ClaimAdmin)
    def _post_save_ca_receiver(sender, instance, **kwargs):
        with suppress(AttributeError):
            cache.delete(f"user_ca_{instance.code}")
            cache.delete(f"rights_{instance.code}")
