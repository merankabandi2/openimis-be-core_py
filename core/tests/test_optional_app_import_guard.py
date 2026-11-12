import importlib
import sys
import types
from unittest import mock

from django.apps import apps
from django.test import SimpleTestCase

from core.gql import max_length_constraints


class OptionalAppImportGuardTestCase(SimpleTestCase):
    """`insuree` may be importable without being an installed app."""

    def setUp(self):
        self.addCleanup(importlib.reload, max_length_constraints)

    def _reload_with_importable_but_uninstalled_insuree(self):
        def models_import_outside_installed_apps(*args, **kwargs):
            raise RuntimeError(
                "Model class insuree.models.Gender doesn't declare an explicit "
                "app_label and isn't in an application in INSTALLED_APPS."
            )

        fake_package = types.ModuleType("insuree")
        fake_package.__path__ = []
        fake_models = mock.MagicMock()
        type(fake_models).Insuree = mock.PropertyMock(side_effect=models_import_outside_installed_apps)
        modules = {"insuree": fake_package, "insuree.models": fake_models}
        real_is_installed = apps.is_installed

        def is_installed(name):
            return False if name == "insuree" else real_is_installed(name)

        with mock.patch.dict(sys.modules, modules), \
                mock.patch.object(apps, "is_installed", side_effect=is_installed):
            return importlib.reload(max_length_constraints)

    def test_uninstalled_insuree_does_not_break_import(self):
        module = self._reload_with_importable_but_uninstalled_insuree()

        self.assertIsNone(module.Insuree)
        self.assertNotIn("insuree", module.MAX_LENGTH_CONSTRAINTS_FIELDS)

    def test_installed_insuree_adds_its_constraints(self):
        if not apps.is_installed("insuree"):
            self.skipTest("insuree is not installed")

        module = importlib.reload(max_length_constraints)

        self.assertIsNotNone(module.Insuree)
        self.assertIn("insuree", module.MAX_LENGTH_CONSTRAINTS_FIELDS)
