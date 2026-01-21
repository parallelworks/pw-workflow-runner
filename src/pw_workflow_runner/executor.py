"""Workflow execution and status polling."""

import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

import httpx

from .client import PWClient
from .models import RunInfo, WorkflowType


class ExecutionTimeout(Exception):
    """Raised when workflow execution times out."""

    pass


class SessionValidationError(Exception):
    """Raised when session URL validation fails."""

    pass


@dataclass
class ExecutionResult:
    """Result of a workflow execution."""

    workflow_name: str
    run_number: int
    status: str
    workflow_type: WorkflowType
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    error_message: Optional[str] = None
    run_info: Optional[RunInfo] = None
    session_url: Optional[str] = None

    @property
    def success(self) -> bool:
        """Check if the execution completed successfully."""
        if self.workflow_type == WorkflowType.SESSION:
            # Session workflows are successful when running with accessible URL
            return self.status.lower() == "running" and self.session_url is not None
        else:
            # Batch workflows are successful when completed
            return self.status.lower() == "completed"


# Terminal statuses for batch workflows
BATCH_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "error"}

# For session workflows, "running" is the target state
SESSION_READY_STATUS = "running"
SESSION_TERMINAL_STATUSES = {"failed", "cancelled", "error"}


class WorkflowExecutor:
    """Executes workflows and monitors their completion."""

    def __init__(
        self,
        client: PWClient,
        timeout: float = 3600,
        initial_poll_interval: float = 5.0,
        max_poll_interval: float = 60.0,
        backoff_factor: float = 1.5,
    ):
        """Initialize executor.

        Args:
            client: PWClient instance for API calls.
            timeout: Maximum time to wait for completion (seconds).
            initial_poll_interval: Initial polling interval (seconds).
            max_poll_interval: Maximum polling interval (seconds).
            backoff_factor: Multiplier for exponential backoff.
        """
        self.client = client
        self.timeout = timeout
        self.initial_poll_interval = initial_poll_interval
        self.max_poll_interval = max_poll_interval
        self.backoff_factor = backoff_factor

    def execute(
        self,
        workflow_name: str,
        inputs: dict,
        workflow_type: WorkflowType = WorkflowType.BATCH,
        session_name: Optional[str] = None,
        on_status: Optional[Callable[[RunInfo, float], None]] = None,
        wait: bool = True,
    ) -> ExecutionResult:
        """Execute a workflow and optionally wait for completion.

        Args:
            workflow_name: Name of the workflow to run.
            inputs: Input parameters for the workflow.
            workflow_type: Type of workflow (batch or session).
            session_name: For session workflows, the session name to validate.
            on_status: Optional callback called on each status poll.
                       Receives (RunInfo, elapsed_seconds).
            wait: If True, poll until completion. If False, return immediately.

        Returns:
            ExecutionResult with the final status.

        Raises:
            ExecutionTimeout: If wait=True and timeout is exceeded.
        """
        started_at = datetime.utcnow()

        # Submit the workflow
        run_info, redirect_url = self.client.submit_workflow(workflow_name, inputs)

        if not wait:
            return ExecutionResult(
                workflow_name=workflow_name,
                run_number=run_info.number,
                status=run_info.status,
                workflow_type=workflow_type,
                started_at=started_at,
                run_info=run_info,
                session_url=redirect_url,
            )

        # Poll based on workflow type
        if workflow_type == WorkflowType.SESSION:
            return self._poll_session_ready(
                workflow_name=workflow_name,
                run_number=run_info.number,
                started_at=started_at,
                session_name=session_name,
                redirect_url=redirect_url,
                on_status=on_status,
            )
        else:
            return self._poll_until_complete(
                workflow_name=workflow_name,
                run_number=run_info.number,
                started_at=started_at,
                on_status=on_status,
            )

    def _poll_until_complete(
        self,
        workflow_name: str,
        run_number: int,
        started_at: datetime,
        on_status: Optional[Callable[[RunInfo, float], None]] = None,
    ) -> ExecutionResult:
        """Poll for batch workflow completion with exponential backoff.

        Args:
            workflow_name: Name of the workflow.
            run_number: Run number to poll.
            started_at: When execution started.
            on_status: Optional callback for status updates.

        Returns:
            ExecutionResult with final status.

        Raises:
            ExecutionTimeout: If timeout exceeded.
        """
        interval = self.initial_poll_interval
        last_status = None

        while True:
            elapsed = (datetime.utcnow() - started_at).total_seconds()

            if elapsed > self.timeout:
                raise ExecutionTimeout(
                    f"Workflow {workflow_name} run #{run_number} timed out after {elapsed:.1f}s"
                )

            # Get current status
            run_info = self.client.get_run_status(workflow_name, run_number)

            # Notify callback if status changed or first poll
            if on_status and run_info.status != last_status:
                on_status(run_info, elapsed)
            last_status = run_info.status

            # Check if terminal
            if run_info.status.lower() in BATCH_TERMINAL_STATUSES:
                completed_at = datetime.utcnow()
                duration = (completed_at - started_at).total_seconds()

                return ExecutionResult(
                    workflow_name=workflow_name,
                    run_number=run_number,
                    status=run_info.status,
                    workflow_type=WorkflowType.BATCH,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration,
                    run_info=run_info,
                    error_message=None if run_info.status.lower() == "completed" else run_info.status,
                )

            # Wait before next poll with jitter
            jitter = 1 + (random.random() - 0.5) * 0.2  # +/- 10% jitter
            sleep_time = min(interval * jitter, self.max_poll_interval)
            time.sleep(sleep_time)

            # Increase interval for next iteration
            interval = min(interval * self.backoff_factor, self.max_poll_interval)

    def _poll_session_ready(
        self,
        workflow_name: str,
        run_number: int,
        started_at: datetime,
        session_name: Optional[str] = None,
        redirect_url: Optional[str] = None,
        on_status: Optional[Callable[[RunInfo, float], None]] = None,
    ) -> ExecutionResult:
        """Poll for session workflow to be ready.

        Session workflows are ready when:
        1. Status is "running"
        2. Session URL is accessible (returns 200)

        Args:
            workflow_name: Name of the workflow.
            run_number: Run number to poll.
            started_at: When execution started.
            session_name: Session name for URL construction.
            redirect_url: Redirect URL from submit response.
            on_status: Optional callback for status updates.

        Returns:
            ExecutionResult with session URL.

        Raises:
            ExecutionTimeout: If timeout exceeded.
        """
        interval = self.initial_poll_interval
        last_status = None
        session_url = redirect_url

        while True:
            elapsed = (datetime.utcnow() - started_at).total_seconds()

            if elapsed > self.timeout:
                raise ExecutionTimeout(
                    f"Session {workflow_name} run #{run_number} timed out after {elapsed:.1f}s"
                )

            # Get current status
            run_info = self.client.get_run_status(workflow_name, run_number)

            # Notify callback if status changed or first poll
            if on_status and run_info.status != last_status:
                on_status(run_info, elapsed)
            last_status = run_info.status

            # Check for failure
            if run_info.status.lower() in SESSION_TERMINAL_STATUSES:
                completed_at = datetime.utcnow()
                duration = (completed_at - started_at).total_seconds()

                return ExecutionResult(
                    workflow_name=workflow_name,
                    run_number=run_number,
                    status=run_info.status,
                    workflow_type=WorkflowType.SESSION,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration,
                    run_info=run_info,
                    error_message=f"Session failed with status: {run_info.status}",
                )

            # Check if running and session is accessible
            if run_info.status.lower() == SESSION_READY_STATUS:
                # Try to validate session URL if we have one
                if session_url and self._validate_session_url(session_url):
                    ready_at = datetime.utcnow()
                    duration = (ready_at - started_at).total_seconds()

                    return ExecutionResult(
                        workflow_name=workflow_name,
                        run_number=run_number,
                        status=run_info.status,
                        workflow_type=WorkflowType.SESSION,
                        started_at=started_at,
                        completed_at=ready_at,
                        duration_seconds=duration,
                        run_info=run_info,
                        session_url=session_url,
                    )

            # Wait before next poll with jitter
            jitter = 1 + (random.random() - 0.5) * 0.2
            sleep_time = min(interval * jitter, self.max_poll_interval)
            time.sleep(sleep_time)

            interval = min(interval * self.backoff_factor, self.max_poll_interval)

    def _validate_session_url(self, url: str) -> bool:
        """Check if session URL is accessible.

        Args:
            url: Session URL to validate.

        Returns:
            True if URL returns 200, False otherwise.
        """
        try:
            # Use the API key for authentication
            headers = {"Authorization": f"Basic {self.client.api_key}"}
            response = httpx.get(url, headers=headers, timeout=10, follow_redirects=True)
            return response.status_code == 200
        except Exception:
            return False

    def check_status(self, workflow_name: str, run_number: int) -> RunInfo:
        """Check the status of an existing run.

        Args:
            workflow_name: Name of the workflow.
            run_number: Run number to check.

        Returns:
            RunInfo with current status.
        """
        return self.client.get_run_status(workflow_name, run_number)
