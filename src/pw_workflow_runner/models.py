"""Pydantic models for PW API responses."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class WorkflowInfo(BaseModel):
    """Workflow item from GET /api/workflows."""

    id: str
    name: str
    type: str
    favorite: bool
    display_name: Optional[str] = Field(None, alias="displayName")
    description: Optional[str] = None
    slug: Optional[str] = None
    user: Optional[str] = None
    tags: Optional[list[str]] = None
    directory: Optional[str] = None
    app: Optional[bool] = None

    class Config:
        populate_by_name = True


class RunInfo(BaseModel):
    """Job/run response from workflow submission or status check."""

    id: str
    number: int
    status: str
    workflow_name: str = Field(alias="workflowName")
    workflow_id: str = Field(alias="workflowId")
    workflow_display_name: str = Field(alias="workflowDisplayName")
    user: str
    created_at: datetime = Field(alias="createdAt")
    completed_at: Optional[datetime] = Field(None, alias="completedAt")
    variables: Optional[list[dict[str, Any]]] = None
    executed_jobs: Optional[list[dict[str, Any]]] = Field(None, alias="executedJobs")

    class Config:
        populate_by_name = True


class SubmitResponse(BaseModel):
    """Response from POST /api/workflows/{workflow}/runs."""

    run: RunInfo
    redirect: Optional[str] = None
