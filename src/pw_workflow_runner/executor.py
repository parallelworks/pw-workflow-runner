"""Workflow execution and status polling."""

import random
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from .client import PWClient
from .models import RunInfo


class ExecutionTimeout(Exception):
    """Raised when workflow execution times out."""

    pass


@dataclass
class ExecutionResult:
    """Result of a workflow execution."""

    workflow_name: str
    run_number: int
    status: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    error_message: Optional[str] = None
    run_info: Optional[RunInfo] = None

    @property
    def success(self) -> bool:
        """Check if the execution completed successfully."""
        return self.status.lower() == "completed"


# Terminal statuses that indicate the workflow has finished
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "error"}


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
        on_status: Optional[Callable[[RunInfo, float], None]] = None,
        wait: bool = True,
    ) -> ExecutionResult:
        """Execute a workflow and optionally wait for completion.

        Args:
            workflow_name: Name of the workflow to run.
            inputs: Input parameters for the workflow.
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
        run_info = self.client.submit_workflow(workflow_name, inputs)

        if not wait:
            return ExecutionResult(
                workflow_name=workflow_name,
                run_number=run_info.number,
                status=run_info.status,
                started_at=started_at,
                run_info=run_info,
            )

        # Poll until completion
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
        """Poll for workflow completion with exponential backoff.

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
            if run_info.status.lower() in TERMINAL_STATUSES:
                completed_at = datetime.utcnow()
                duration = (completed_at - started_at).total_seconds()

                return ExecutionResult(
                    workflow_name=workflow_name,
                    run_number=run_number,
                    status=run_info.status,
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

    def check_status(self, workflow_name: str, run_number: int) -> RunInfo:
        """Check the status of an existing run.

        Args:
            workflow_name: Name of the workflow.
            run_number: Run number to check.

        Returns:
            RunInfo with current status.
        """
        return self.client.get_run_status(workflow_name, run_number)
