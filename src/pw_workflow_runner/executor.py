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
            # Session workflows are successful when running
            # (session_url may be None for SSH tunnel use cases)
            return self.status.lower() == "running"
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
        max_poll_interval: float = 10.0,
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
        1. Session appears in /api/sessions
        2. Session status is "running" or similar active state
        3. Session URL is accessible (returns 200)

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

            # Poll session status via /api/sessions endpoint
            session_info = self.client.get_session_for_run(workflow_name, run_number)

            if session_info:
                current_status = session_info.status or "unknown"

                # Notify callback if status changed
                if on_status and current_status != last_status:
                    # Create a minimal RunInfo for the callback
                    from .models import RunInfo
                    run_info = RunInfo(
                        id=session_info.id,
                        number=run_number,
                        status=current_status,
                        workflow_name=workflow_name,
                        workflow_id="",
                        workflow_display_name=workflow_name,
                        user=session_info.user or "",
                        created_at=started_at,
                    )
                    on_status(run_info, elapsed)
                last_status = current_status

                # Check for failure states
                if current_status.lower() in SESSION_TERMINAL_STATUSES:
                    completed_at = datetime.utcnow()
                    duration = (completed_at - started_at).total_seconds()

                    return ExecutionResult(
                        workflow_name=workflow_name,
                        run_number=run_number,
                        status=current_status,
                        workflow_type=WorkflowType.SESSION,
                        started_at=started_at,
                        completed_at=completed_at,
                        duration_seconds=duration,
                        run_info=None,
                        error_message=f"Session failed with status: {current_status}",
                    )

                # Use session URL from session info if available, fallback to redirect_url
                actual_session_url = session_info.external_href or session_info.url or session_url

                # Check if session is ready (status is "running")
                # For SSH tunnel use cases, we don't need to validate the URL is HTTP-accessible
                if current_status.lower() == SESSION_READY_STATUS:
                    ready_at = datetime.utcnow()
                    duration = (ready_at - started_at).total_seconds()

                    return ExecutionResult(
                        workflow_name=workflow_name,
                        run_number=run_number,
                        status=current_status,
                        workflow_type=WorkflowType.SESSION,
                        started_at=started_at,
                        completed_at=ready_at,
                        duration_seconds=duration,
                        run_info=None,
                        session_url=actual_session_url,
                    )
            else:
                # Session not yet created, notify callback with starting status
                if on_status and last_status != "starting":
                    from .models import RunInfo
                    run_info = RunInfo(
                        id="",
                        number=run_number,
                        status="starting",
                        workflow_name=workflow_name,
                        workflow_id="",
                        workflow_display_name=workflow_name,
                        user="",
                        created_at=started_at,
                    )
                    on_status(run_info, elapsed)
                last_status = "starting"

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
