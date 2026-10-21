from django.core.cache import cache
from django.test import TestCase

from core.models import InteractiveUser, RoleRight, UserRole
from core.schema import update_or_create_role
from core.test_helpers import create_test_interactive_user, create_test_role

NEW_RIGHT_ID = 987654


class RoleRightsCacheTest(TestCase):
    """Role, role right and role assignment changes drop the cached rights of
    the users concerned and leave the rest of the cache alone."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.role = create_test_role(name="RightsCacheRole")
        self.other_role = create_test_role(name="RightsCacheOtherRole")
        self.member = create_test_interactive_user(username="RightsCacheMember", roles=[self.role.id]).i_user
        self.other = create_test_interactive_user(username="RightsCacheOther", roles=[self.other_role.id]).i_user
        cache.set_many({
            f"rights_{self.member.id}": [1], f"is_admin_{self.member.id}": False,
            f"rights_{self.other.id}": [2], f"is_admin_{self.other.id}": False,
            "unrelated": "kept",
        })

    def assert_member_dropped_other_kept(self):
        self.assertIsNone(cache.get(f"rights_{self.member.id}"))
        self.assertIsNone(cache.get(f"is_admin_{self.member.id}"))
        self.assertEqual(cache.get(f"rights_{self.other.id}"), [2])
        self.assertEqual(cache.get(f"is_admin_{self.other.id}"), False)
        self.assertEqual(cache.get("unrelated"), "kept")

    def test_saving_a_role_drops_only_its_members_rights(self):
        self.role.name = "RightsCacheRoleRenamed"
        self.role.save()
        self.assert_member_dropped_other_kept()

    def test_adding_a_role_right_drops_only_its_members_rights(self):
        RoleRight.objects.create(role=self.role, right_id=NEW_RIGHT_ID, audit_user_id=-1)
        self.assert_member_dropped_other_kept()

    def test_assigning_a_role_drops_only_that_users_rights(self):
        UserRole.objects.create(user=self.member, role=self.other_role, audit_user_id=-1)
        self.assert_member_dropped_other_kept()

    def test_members_get_the_rights_of_an_edited_role(self):
        update_or_create_role({"uuid": self.role.uuid, "rights_id": [NEW_RIGHT_ID]}, None)
        self.assertEqual(InteractiveUser.objects.get(id=self.member.id).rights, [NEW_RIGHT_ID])
        self.assertEqual(cache.get(f"rights_{self.other.id}"), [2])
        self.assertEqual(cache.get("unrelated"), "kept")
