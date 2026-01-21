"""PW SDK client wrapper for workflow operations."""

import os
from typing import Optional

from parallelworks_client import Client

from .models import RunInfo, SessionInfo, SubmitResponse, WorkflowInfo


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

    def submit_workflow(self, workflow_name: str, inputs: dict) -> tuple[RunInfo, Optional[str]]:
        """Submit a workflow run.

        Args:
            workflow_name: Name of the workflow to run.
            inputs: Input parameters for the workflow.

        Returns:
            Tuple of (RunInfo, redirect_url). redirect_url is the session URL for
            interactive session workflows, or None for batch workflows.
        """
        response = self._sync_client.post(
            f"/api/workflows/{workflow_name}/runs",
            json={"inputs": inputs},
        )
        response.raise_for_status()
        submit_response = SubmitResponse.model_validate(response.json())
        return submit_response.run, submit_response.redirect

    def get_run_status(self, workflow_name: str, run_number: int) -> RunInfo:
        """Get the status of a workflow run.

        WARNING: This endpoint (GET /api/workflows/{workflow}/runs/{runNumber})
        is not supported by the Parallel Works API - it returns 405 Method Not Allowed.

        For session workflows, use get_session_for_run() instead which queries
        the /api/sessions endpoint.

        Args:
            workflow_name: Name of the workflow.
            run_number: Run number to check.

        Returns:
            RunInfo with current run status.

        Raises:
            httpx.HTTPStatusError: Always raises 405 Method Not Allowed.
        """
        response = self._sync_client.get(f"/api/workflows/{workflow_name}/runs/{run_number}")
        response.raise_for_status()
        return RunInfo.model_validate(response.json())

    def cancel_run(self, workflow_name: str, run_number: int) -> None:
        """Cancel/delete a workflow run.

        Args:
            workflow_name: Name of the workflow.
            run_number: Run number to cancel.
        """
        response = self._sync_client.delete(f"/api/workflows/{workflow_name}/runs/{run_number}")
        response.raise_for_status()

    def get_sessions(self) -> list[SessionInfo]:
        """Get all active sessions.

        Returns:
            List of SessionInfo objects.
        """
        response = self._sync_client.get("/api/sessions")
        response.raise_for_status()
        data = response.json()
        return [SessionInfo.model_validate(s) for s in data]

    def get_session_for_run(
        self, workflow_name: str, run_number: int, debug: bool = False
    ) -> Optional[SessionInfo]:
        """Find the session associated with a workflow run.

        Args:
            workflow_name: Name of the workflow.
            run_number: Run number to find session for.
            debug: If True, print debug info about sessions.

        Returns:
            SessionInfo if found, None otherwise.
        """
        sessions = self.get_sessions()
        if debug:
            print(f"Looking for workflow={workflow_name}, run={run_number}")
            print(f"Found {len(sessions)} sessions")
        for session in sessions:
            if debug:
                print(f"  Session {session.id}: workflow_run={session.workflow_run}")
            if session.workflow_run and session.workflow_run.number == run_number:
                # Match on run number. If workflow_name is available, verify it matches.
                # The API sometimes returns workflow_name as None.
                if (
                    session.workflow_run.workflow_name is None
                    or session.workflow_run.workflow_name == workflow_name
                ):
                    return session
        return None
