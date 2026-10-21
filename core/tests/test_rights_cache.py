from django.core.cache import cache
from django.test import TestCase

from core.test_helpers import create_test_interactive_user, create_test_role


class RoleRightsCacheTest(TestCase):
    """Saving a role drops the cached rights of the users holding it and
    leaves the rest of the cache alone."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.role = create_test_role(name="RightsCacheRole")
        other_role = create_test_role(name="RightsCacheOtherRole")
        self.member = create_test_interactive_user(username="RightsCacheMember", roles=[self.role.id]).i_user
        self.other = create_test_interactive_user(username="RightsCacheOther", roles=[other_role.id]).i_user
        cache.set_many({
            f"rights_{self.member.id}": [1], f"is_admin_{self.member.id}": False,
            f"rights_{self.other.id}": [2], f"is_admin_{self.other.id}": False,
            "unrelated": "kept",
        })

    def test_saving_a_role_drops_only_its_members_rights(self):
        self.role.name = "RightsCacheRoleRenamed"
        self.role.save()
        self.assertIsNone(cache.get(f"rights_{self.member.id}"))
        self.assertIsNone(cache.get(f"is_admin_{self.member.id}"))
        self.assertEqual(cache.get(f"rights_{self.other.id}"), [2])
        self.assertEqual(cache.get(f"is_admin_{self.other.id}"), False)
        self.assertEqual(cache.get("unrelated"), "kept")
