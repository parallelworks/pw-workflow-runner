"""PW SDK client wrapper for workflow operations."""

import os
from typing import Optional

from parallelworks_client import Client

from .models import RunInfo, SubmitResponse, WorkflowInfo


class PWClientError(Exception):
    """Error from PW API."""

    pass


class PWClient:
    """Client wrapper for PW workflow operations."""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize client with API key.

        Args:
            api_key: PW API key. If not provided, reads from PW_API_KEY env var.
        """
        self.api_key = api_key or os.environ.get("PW_API_KEY")
        if not self.api_key:
            raise PWClientError(
                "PW_API_KEY is required. Set it as an environment variable or pass it to PWClient."
            )
        self._sync_client = None

    def __enter__(self) -> "PWClient":
        self._context = Client.from_credential(self.api_key).sync()
        self._sync_client = self._context.__enter__()
        return self

    def __exit__(self, *args):
        if self._context:
            self._context.__exit__(*args)

    def list_workflows(self) -> list[WorkflowInfo]:
        """List all workflows available in the account.

        Returns:
            List of WorkflowInfo objects.
        """
        response = self._sync_client.get("/api/workflows")
        response.raise_for_status()
        data = response.json()
        return [WorkflowInfo.model_validate(w) for w in data]

    def get_workflow(self, workflow_name: str) -> WorkflowInfo:
        """Get details of a specific workflow.

        Args:
            workflow_name: Name of the workflow.

        Returns:
            WorkflowInfo object.
        """
        response = self._sync_client.get(f"/api/workflows/{workflow_name}")
        response.raise_for_status()
        return WorkflowInfo.model_validate(response.json())

    def submit_workflow(self, workflow_name: str, inputs: dict) -> RunInfo:
        """Submit a workflow run.

        Args:
            workflow_name: Name of the workflow to run.
            inputs: Input parameters for the workflow.

        Returns:
            RunInfo with the submitted run details.
        """
        response = self._sync_client.post(
            f"/api/workflows/{workflow_name}/runs",
            json={"inputs": inputs},
        )
        response.raise_for_status()
        submit_response = SubmitResponse.model_validate(response.json())
        return submit_response.run

    def get_run_status(self, workflow_name: str, run_number: int) -> RunInfo:
        """Get the status of a workflow run.

        Args:
            workflow_name: Name of the workflow.
            run_number: Run number to check.

        Returns:
            RunInfo with current run status.
        """
        response = self._sync_client.get(f"/api/workflows/{workflow_name}/runs/{run_number}")
        response.raise_for_status()
        return RunInfo.model_validate(response.json())
