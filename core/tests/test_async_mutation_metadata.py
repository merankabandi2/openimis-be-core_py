"""Queuing an async mutation completes cleanly, without a metadata error."""
import json
import uuid
from unittest.mock import patch

import core
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase, BaseTestContext
from core.test_helpers import create_test_interactive_user

CREATE_ROLE = """
mutation ($input: CreateRoleMutationInput!) {
  createRole(input: $input) { clientMutationId internalId }
}
"""


class AsyncMutationMetadataTest(openIMISGraphQLTestCase):
    def test_queued_mutation_assembles_its_metadata(self):
        # `messages` was only assigned on the synchronous branch, so every
        # queued mutation logged "could not assemble metadata" (UnboundLocalError).
        user = create_test_interactive_user(username="asyncmeta")
        token = BaseTestContext(user=user).get_jwt()
        variables = {"input": {"name": f"Async{uuid.uuid4().hex[:8]}", "isSystem": False,
                               "isBlocked": False, "clientMutationId": str(uuid.uuid4())}}
        with patch.object(core, "async_mutations", True), \
                patch("core.schema.openimis_mutation_async") as task, \
                self.assertNoLogs("core.schema", level="WARNING"):
            response = self.query(CREATE_ROLE, variables=variables,
                                  headers={"HTTP_AUTHORIZATION": f"Bearer {token}"})
        self.assertResponseNoErrors(response)
        self.assertTrue(task.delay.called)


class AsyncMutationLogContentTest(openIMISGraphQLTestCase):
    def test_content_changed_by_the_task_is_not_overwritten(self):
        # The queued task (or a receiver) may rewrite the stored input, for
        # example to drop personal data; the request must not restore it.
        from core.models import MutationLog
        user = create_test_interactive_user(username="asynccontent")
        token = BaseTestContext(user=user).get_jwt()
        name = f"Async{uuid.uuid4().hex[:8]}"
        variables = {"input": {"name": name, "isSystem": False,
                               "isBlocked": False, "clientMutationId": str(uuid.uuid4())}}

        def rewrite_content(mutation_id, *args, **kwargs):
            MutationLog.objects.filter(id=mutation_id).update(
                json_content='{"redacted": true}')

        with patch.object(core, "async_mutations", True), \
                patch("core.schema.openimis_mutation_async") as task:
            task.delay.side_effect = rewrite_content
            response = self.query(CREATE_ROLE, variables=variables,
                                  headers={"HTTP_AUTHORIZATION": f"Bearer {token}"})
        self.assertResponseNoErrors(response)
        internal_id = response.json()["data"]["createRole"]["internalId"]
        stored = json.loads(MutationLog.objects.get(id=internal_id).json_content)
        self.assertTrue(stored.get("redacted"))
        self.assertNotIn("name", stored)
