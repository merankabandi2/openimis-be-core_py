"""Rights checks on one user object do not repeat the core user lookup."""
import re

from django.db import connection
from django.test import TestCase

from core.models import InteractiveUser, User
from core.test_helpers import create_test_interactive_user


def count_core_user_selects(callable_):
    seen = []

    def tracer(execute, sql, params, many, context):
        if re.search(r'FROM "core_User"', sql):
            seen.append(sql)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(tracer):
        callable_()
    return len(seen)


class SuperuserLookupTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.login = create_test_interactive_user(username="supq", custom_props={"is_superuser": False}).login_name

    def test_repeated_checks_run_the_core_user_select_once(self):
        i_user = InteractiveUser.objects.get(login_name=self.login)

        def checks():
            for _ in range(8):
                i_user.is_superuser

        self.assertEqual(count_core_user_selects(checks), 1)

    def test_has_perm_on_one_user_runs_the_core_user_select_once(self):
        user = User.objects.get(username=self.login)

        def checks():
            for _ in range(8):
                user.has_perm("101001")

        self.assertEqual(count_core_user_selects(checks), 1)

    def test_freshly_loaded_user_sees_a_changed_flag(self):
        before = InteractiveUser.objects.get(login_name=self.login)
        self.assertFalse(before.is_superuser)
        User.objects.filter(username=self.login).update(is_superuser=True)
        self.assertTrue(InteractiveUser.objects.get(login_name=self.login).is_superuser)

    def test_refreshed_instance_sees_a_changed_flag(self):
        i_user = InteractiveUser.objects.get(login_name=self.login)
        self.assertFalse(i_user.is_superuser)
        User.objects.filter(username=self.login).update(is_superuser=True)
        i_user.refresh_from_db()
        self.assertTrue(i_user.is_superuser)

    def test_setter_is_visible_on_the_same_instance(self):
        i_user = InteractiveUser.objects.get(login_name=self.login)
        self.assertFalse(i_user.is_superuser)
        i_user.is_superuser = True
        self.assertTrue(i_user.is_superuser)
        self.assertTrue(User.objects.get(username=self.login).is_superuser)
