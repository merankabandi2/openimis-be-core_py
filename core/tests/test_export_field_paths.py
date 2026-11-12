import ast
import csv
import io
import shutil
import tempfile
from types import SimpleNamespace

import graphene
from django.contrib.auth.models import Group, Permission
from django.db import transaction
from django.db.models import F
from django.test import TestCase, override_settings

from core.models import (
    ExportableQueryModel,
    InteractiveUser,
    MutationLog,
    Role,
    RoleMutation,
    RoleRight,
    TechnicalUser,
    User,
    UserMutation,
    UserRole,
)
from core.gql.export_mixin import ExportableQueryMixin
from core.models.base_mutation import ExportFieldNotAllowed, _account_models
from core.schema import MutationLogGQLType, OrderedDjangoFilterConnectionField
from core.test_helpers import create_test_interactive_user
from location.models import Location


class MutationLogExportQuery(ExportableQueryMixin, graphene.ObjectType):
    exportable_fields = ["mutation_logs"]
    mutation_logs = OrderedDjangoFilterConnectionField(MutationLogGQLType)

    def resolve_mutation_logs(self, info, **kwargs):
        return MutationLog.objects.all()


def _key(path):
    return f"core.export.field_not_allowed: {path}"


class ExportFieldPathTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._media_root = tempfile.mkdtemp()
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        cls.user = create_test_interactive_user(username="exportFieldPathUser")
        cls.technical_user = TechnicalUser(username="exportFieldPathTech")
        cls.technical_user.save()
        cls.mutation_log = MutationLog.objects.create(
            json_content="{}",
            user=cls.user,
            client_mutation_label="export-path-label",
            json_ext={"head": {"first_name": "Ada"}, "size": 3, "in": "first-level"},
        )
        cls.user_mutation = UserMutation.objects.create(
            core_user=cls.user, mutation=cls.mutation_log
        )
        cls.role = Role.objects.create(
            name="ExportPathRole", is_system=0, is_blocked=False, audit_user_id=-1
        )
        cls.role_right = RoleRight.objects.create(
            role=cls.role, right_id=999001, audit_user_id=-1
        )
        cls.role_mutation = RoleMutation.objects.create(
            role=cls.role, mutation=cls.mutation_log
        )
        cls.user_role = UserRole.objects.create(
            user=cls.user.i_user, role=cls.role, audit_user_id=-1
        )
        cls.group = Group.objects.create(name="ExportPathGroup")
        cls.group.permissions.add(Permission.objects.order_by("id").first())
        cls.region = Location.objects.create(
            code="XPR1", type="R", name="Export Region",
            validity_from="2019-06-01", audit_user_id=-1,
        )
        cls.district = Location.objects.create(
            code="XPD1", type="D", name="Export District", parent=cls.region,
            validity_from="2019-06-01", audit_user_id=-1,
        )
        cls.municipality = Location.objects.create(
            code="XPW1", type="W", name="Export Municipality", parent=cls.district,
            validity_from="2019-06-01", audit_user_id=-1,
        )

    def _export(self, qs, values):
        return ExportableQueryModel.create_csv_export(qs, values, self.user, column_names={})

    def _rows(self, export):
        export.content.open("rb")
        try:
            text = export.content.read().decode("utf-8")
        finally:
            export.content.close()
        return list(csv.DictReader(io.StringIO(text)))

    def assertRefused(self, qs, path, values=None):
        exports_before = ExportableQueryModel.objects.count()
        try:
            with transaction.atomic():
                self._export(qs, [path] if values is None else values)
        except ExportFieldNotAllowed as exc:
            self.assertEqual(str(exc), _key(path))
        except Exception as exc:
            self.fail(f"{path!r} raised {type(exc).__name__} instead of being refused: {exc}")
        else:
            self.fail(f"{path!r} was exported")
        self.assertEqual(ExportableQueryModel.objects.count(), exports_before)

    def _graphql_export(self, arguments, variables=None):
        schema = graphene.Schema(query=MutationLogExportQuery)
        declaration = "($fields: [String])" if variables else ""
        return schema.execute(
            f"query{declaration} {{ mutationLogsExport({arguments}) }}",
            context=SimpleNamespace(user=self.user),
            variables=variables,
        )

    def test_account_models_are_the_user_models_and_their_history(self):
        self.assertEqual(
            {model._meta.label for model in _account_models()},
            {
                "core.User",
                "core.HistoricalUser",
                "core.InteractiveUser",
                "core.HistoricalInteractiveUser",
                "core.TechnicalUser",
            },
        )

    # Refused paths

    def test_hop_from_user_model_into_account_is_refused(self):
        qs = MutationLog.objects.filter(id=self.mutation_log.id)
        self.assertRefused(qs, "user__i_user__password")
        self.assertRefused(qs, "user__i_user__private_key")
        self.assertRefused(qs, "user__i_user__login_name")
        self.assertRefused(qs, "user__t_user__password")
        qs = UserMutation.objects.filter(id=self.user_mutation.id)
        self.assertRefused(qs, "core_user__i_user__password")
        self.assertRefused(qs, "core_user__t_user__username")

    def test_relation_to_user_model_as_last_segment_is_refused(self):
        self.assertRefused(MutationLog.objects.filter(id=self.mutation_log.id), "user")
        self.assertRefused(UserMutation.objects.filter(id=self.user_mutation.id), "core_user")

    def test_attname_of_relation_to_user_model_is_refused(self):
        self.assertRefused(MutationLog.objects.filter(id=self.mutation_log.id), "user_id")

    def test_user_model_fields_other_than_username_are_refused(self):
        qs = MutationLog.objects.filter(id=self.mutation_log.id)
        self.assertRefused(qs, "user__id")
        self.assertRefused(qs, "user__pk")
        self.assertRefused(qs, "user__version")
        self.assertRefused(qs, "user__password")
        self.assertRefused(
            UserMutation.objects.filter(id=self.user_mutation.id), "core_user__is_superuser"
        )

    def test_hop_or_lookup_after_user_model_username_is_refused(self):
        qs = MutationLog.objects.filter(id=self.mutation_log.id)
        self.assertRefused(qs, "user__username__lower")
        self.assertRefused(qs, "user__groups__name")
        self.assertRefused(
            UserMutation.objects.filter(id=self.user_mutation.id), "core_user__username__lower"
        )

    def test_relation_to_interactive_user_is_refused(self):
        qs = UserRole.objects.filter(id=self.user_role.id)
        self.assertRefused(qs, "user")
        self.assertRefused(qs, "user__password")
        self.assertRefused(qs, "user__login_name")

    def test_account_model_as_root_is_refused(self):
        self.assertRefused(User.objects.filter(id=self.user.id), "id")
        self.assertRefused(User.objects.filter(id=self.user.id), "i_user__password")
        self.assertRefused(User.objects.filter(id=self.user.id), "username__lower")
        self.assertRefused(User.history.model.objects.all(), "username")
        interactive = InteractiveUser.objects.filter(id=self.user.i_user.id)
        self.assertRefused(interactive, "login_name")
        self.assertRefused(interactive, "password")
        technical = TechnicalUser.objects.filter(id=self.technical_user.id)
        self.assertRefused(technical, "username")
        self.assertRefused(technical, "password")

    def test_forward_many_to_many_is_refused(self):
        qs = Group.objects.filter(id=self.group.id)
        self.assertRefused(qs, "permissions")
        self.assertRefused(qs, "permissions__codename")

    def test_reverse_relation_is_refused(self):
        qs = MutationLog.objects.filter(id=self.mutation_log.id)
        self.assertRefused(qs, "roles__role__name")
        self.assertRefused(Role.objects.filter(id=self.role.id), "rights__right_id")

    def test_forward_then_reverse_relation_is_refused(self):
        qs = RoleMutation.objects.filter(id=self.role_mutation.id)
        self.assertRefused(qs, "role__rights__right_id")
        self.assertRefused(qs, "mutation__users__core_user__username")

    def test_unknown_field_is_refused(self):
        qs = MutationLog.objects.filter(id=self.mutation_log.id)
        self.assertRefused(qs, "no_such_field")
        self.assertRefused(qs, "")

    def test_lookup_after_scalar_is_refused(self):
        qs = MutationLog.objects.filter(id=self.mutation_log.id)
        self.assertRefused(qs, "request_date_time__year")
        self.assertRefused(qs, "client_mutation_label__lower")
        self.assertRefused(qs, "client_mutation_label____x")

    def test_lookup_name_below_first_json_key_is_refused(self):
        qs = MutationLog.objects.filter(id=self.mutation_log.id)
        self.assertRefused(qs, "json_ext__head__in")
        self.assertRefused(qs, "json_ext__head__isnull")
        self.assertRefused(qs, "json_ext__head__first_name__exact")
        self.assertRefused(
            RoleMutation.objects.filter(id=self.role_mutation.id),
            "mutation__json_ext__head__regex",
        )

    def test_empty_field_list_is_refused(self):
        self.assertRefused(MutationLog.objects.filter(id=self.mutation_log.id), "", values=[])

    def test_one_refused_path_refuses_the_whole_export(self):
        qs = MutationLog.objects.filter(id=self.mutation_log.id)
        self.assertRefused(
            qs,
            "user__i_user__password",
            values=["client_mutation_label", "user__i_user__password"],
        )

    def test_graphql_export_reports_refused_path_as_error(self):
        result = self._graphql_export('fields: ["user.iUser.password"], fieldsColumns: "{}"')
        self.assertIsNone(result.data["mutationLogsExport"])
        self.assertEqual([str(error) for error in result.errors], [_key("user__i_user__password")])

    def test_graphql_export_without_fields_is_refused(self):
        result = self._graphql_export('fieldsColumns: "{}"')
        self.assertIsNone(result.data["mutationLogsExport"])
        self.assertEqual([str(error) for error in result.errors], [_key("")])

    def test_graphql_export_with_null_field_is_refused(self):
        result = self._graphql_export(
            'fields: $fields, fieldsColumns: "{}"', variables={"fields": ["id", None]}
        )
        self.assertIsNone(result.data["mutationLogsExport"])
        self.assertEqual([str(error) for error in result.errors], [_key("")])

    # Accepted paths

    def test_local_fields_are_exported(self):
        qs = MutationLog.objects.filter(id=self.mutation_log.id)
        rows = self._rows(self._export(qs, ["id", "pk", "client_mutation_label", "status"]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], str(self.mutation_log.id))
        self.assertEqual(rows[0]["pk"], str(self.mutation_log.id))
        self.assertEqual(rows[0]["client_mutation_label"], "export-path-label")
        self.assertEqual(rows[0]["status"], str(MutationLog.RECEIVED))

    def test_forward_relation_to_non_account_model_is_exported(self):
        qs = RoleRight.objects.filter(id=self.role_right.id)
        rows = self._rows(self._export(qs, ["right_id", "role", "role_id", "role__name"]))
        self.assertEqual(rows[0]["right_id"], "999001")
        self.assertEqual(rows[0]["role"], str(self.role.id))
        self.assertEqual(rows[0]["role_id"], str(self.role.id))
        self.assertEqual(rows[0]["role__name"], "ExportPathRole")

    def test_nested_forward_relations_are_exported(self):
        qs = Location.objects.filter(id=self.municipality.id)
        rows = self._rows(self._export(qs, ["code", "parent__code", "parent__parent__code"]))
        self.assertEqual(rows[0]["code"], "XPW1")
        self.assertEqual(rows[0]["parent__code"], "XPD1")
        self.assertEqual(rows[0]["parent__parent__code"], "XPR1")

    def test_forward_relation_then_local_field_is_exported(self):
        qs = RoleMutation.objects.filter(id=self.role_mutation.id)
        rows = self._rows(self._export(qs, ["mutation__client_mutation_label"]))
        self.assertEqual(rows[0]["mutation__client_mutation_label"], "export-path-label")

    def test_json_keys_are_exported(self):
        qs = MutationLog.objects.filter(id=self.mutation_log.id)
        rows = self._rows(
            self._export(qs, ["json_ext", "json_ext__size", "json_ext__head__first_name"])
        )
        self.assertEqual(
            ast.literal_eval(rows[0]["json_ext"]),
            {"head": {"first_name": "Ada"}, "size": 3, "in": "first-level"},
        )
        self.assertEqual(rows[0]["json_ext__size"], "3")
        self.assertEqual(rows[0]["json_ext__head__first_name"], "Ada")

    def test_first_level_json_key_named_like_a_lookup_is_exported(self):
        qs = MutationLog.objects.filter(id=self.mutation_log.id)
        rows = self._rows(self._export(qs, ["json_ext__in"]))
        self.assertEqual(rows[0]["json_ext__in"], "first-level")

    def test_json_keys_after_forward_relation_are_exported(self):
        qs = RoleMutation.objects.filter(id=self.role_mutation.id)
        rows = self._rows(self._export(qs, ["mutation__json_ext__head__first_name"]))
        self.assertEqual(rows[0]["mutation__json_ext__head__first_name"], "Ada")

    def test_user_model_username_is_exported(self):
        qs = MutationLog.objects.filter(id=self.mutation_log.id)
        rows = self._rows(self._export(qs, ["client_mutation_label", "user__username"]))
        self.assertEqual(rows[0]["user__username"], self.user.username)
        qs = UserMutation.objects.filter(id=self.user_mutation.id)
        rows = self._rows(self._export(qs, ["core_user__username"]))
        self.assertEqual(rows[0]["core_user__username"], self.user.username)

    def test_user_model_root_username_is_exported(self):
        rows = self._rows(self._export(User.objects.filter(id=self.user.id), ["username"]))
        self.assertEqual([row["username"] for row in rows], [self.user.username])

    def test_queryset_annotation_is_exported(self):
        qs = MutationLog.objects.filter(id=self.mutation_log.id).annotate(
            label=F("client_mutation_label")
        )
        rows = self._rows(self._export(qs, ["label"]))
        self.assertEqual(rows[0]["label"], "export-path-label")
        self.assertRefused(qs, "label__lower")
