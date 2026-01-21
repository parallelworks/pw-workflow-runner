# PW Workflow Runner

## Overview

A Python-based **workflow executor** for the Parallel Works ACTIVATE platform that can:
- **Discover** workflows available in a PW account
- **Execute** them remotely with JSON input payloads
- **Monitor** completion and report results
- **Interactive mode** when run without arguments

**Key principle**: Workflows live in the PW account. This tool just runs them with JSON inputs.

## Installation

```bash
cd pw-workflow-runner
uv venv && uv pip install -e .
export PW_API_KEY=your_api_key
```

## Directory Structure

```
pw-workflow-runner/
├── pyproject.toml
├── .env.example
├── src/
│   └── pw_workflow_runner/
│       ├── __init__.py
│       ├── __main__.py               # Entry point
│       ├── client.py                 # PW SDK wrapper
│       ├── models.py                 # Pydantic models
│       ├── executor.py               # Workflow execution + polling
│       ├── interactive.py            # Interactive mode
│       └── cli.py                    # Click CLI
└── inputs/                           # JSON input payloads
    └── hello-world.json              # Example input for hello-world workflow
```

## Key Components

### 1. PW Client (`client.py`)

```python
class PWClient:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("PW_API_KEY")

    def list_workflows(self) -> list[WorkflowInfo]:
        """GET /api/workflows"""

    def get_workflow(self, workflow_name: str) -> WorkflowInfo:
        """GET /api/workflows/{workflow}"""

    def submit_workflow(self, workflow_name: str, inputs: dict) -> RunInfo:
        """POST /api/workflows/{workflow}/runs"""

    def get_run_status(self, workflow_name: str, run_number: int) -> RunInfo:
        """GET /api/workflows/{workflow}/runs/{runNumber}"""
```

### 2. Executor (`executor.py`)

```python
@dataclass
class ExecutionResult:
    workflow_name: str
    run_number: int
    status: str          # completed, failed, cancelled, timeout
    started_at: datetime
    completed_at: Optional[datetime]
    duration_seconds: Optional[float]
    error_message: Optional[str]

class WorkflowExecutor:
    def __init__(self, client: PWClient, timeout: float = 3600):
        # Polling: 5s initial, 60s max, 1.5x backoff

    def execute(self, workflow_name: str, inputs: dict, on_status: Callable = None) -> ExecutionResult:
        """Submit workflow and poll until completion."""
```

### 3. Interactive Mode (`interactive.py`)

When run with no arguments:

```
$ pw-workflow-runner

Available workflows:
  1. hello-world
  2. data-processor
  3. simulation-runner

Select workflow [1]: 1

How do you want to provide inputs?
  1. Load from JSON file
  2. Enter manually

Select [1]: 1
Input file path: inputs/hello-world.json

Submitting hello-world...
Run #47 started
Status: running... (15s)
Status: completed (42s)

✓ Workflow completed successfully
```

Uses `rich` for nice terminal output.

### 4. CLI (`cli.py`)

```bash
# INTERACTIVE (no args)
pw-workflow-runner

# LIST WORKFLOWS
pw-workflow-runner list
pw-workflow-runner list --json

# RUN WITH JSON INPUT FILE
pw-workflow-runner run hello-world --input inputs/hello-world.json

# RUN WITH INLINE INPUTS
pw-workflow-runner run hello-world -i "hello.message=test"

# RUN WITHOUT WAITING
pw-workflow-runner run hello-world --input inputs/hello-world.json --no-wait

# CHECK STATUS
pw-workflow-runner status hello-world 47
```

## Input Payload Format

JSON files with workflow parameters. Example `inputs/hello-world.json`:

```json
{
  "v3": true,
  "workflow_dir": "",
  "submit_to_scheduler": true,
  "slurm": {
    "v3": true,
    "is_disabled": true,
    "cpus_per_task": 1,
    "partition": "batch"
  },
  "pbs": {
    "v3": true,
    "is_disabled": true
  },
  "hello": {
    "v3": true,
    "message": "Test message"
  },
  "resource": {
    "id": "6894b3f8d9dedd97d7c3d536",
    "type": "existing",
    "provider": "existing",
    "ip": "gpu.parallel.works",
    "user": "Matthew.Shaxted",
    "namespace": "Matthew.Shaxted",
    "name": "a30gpuserver"
  }
}
```

## Dependencies

```toml
[project]
name = "pw-workflow-runner"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = [
    "parallelworks-client @ file:///home/mattshax/pw-sdk/python",
    "click>=8.0.0",
    "pydantic>=2.0.0",
    "rich>=13.0.0",
]

[project.scripts]
pw-workflow-runner = "pw_workflow_runner.cli:main"
```

## Environment Variables

```bash
# Required
PW_API_KEY=pwt_xxxxx.xxxxx

# Optional
PW_TIMEOUT=3600
PW_LOG_LEVEL=INFO
```

## Implementation Sequence

1. **Project setup** - pyproject.toml, directory structure
2. **Models** - Pydantic models for WorkflowInfo, RunInfo
3. **Client** - PWClient wrapper around PW SDK
4. **Executor** - WorkflowExecutor with polling
5. **CLI** - Click commands: list, run, status
6. **Interactive** - Interactive mode with rich prompts
7. **Test** - Run hello-world on a30gpuserver

## Verification

```bash
# Install
cd pw-workflow-runner
pip install -e .
export PW_API_KEY=your_key

# List workflows
pw-workflow-runner list

# Run hello-world
pw-workflow-runner run hello-world --input inputs/hello-world.json

# Or interactive
pw-workflow-runner
```

## Key SDK Files

| File | Purpose |
|------|---------|
| `~/pw-sdk/python/parallelworks_client/auth.py` | Client to wrap |
| `~/pw-sdk/python/examples/list_resources.py` | Usage pattern |
| `~/pw-sdk/openapi-3.0.json` | API spec |

## Future: Testing Layer

Once the core executor works, add a testing layer:
- Test suite configs (YAML) that reference workflows + inputs
- Expected status validation
- JUnit XML output
- GitHub Actions for nightly runs
