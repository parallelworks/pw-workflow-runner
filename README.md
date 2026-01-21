# PW Workflow Runner

A command-line tool for executing workflows on the Parallel Works ACTIVATE platform.

## Features

- **List workflows** available in your PW account
- **Execute workflows** with JSON input payloads
- **Monitor execution** with live status updates and exponential backoff polling
- **Interactive mode** - guided workflow selection and execution when run without arguments

## Installation

```bash
cd pw-workflow-runner

# Install with uv
uv venv && uv pip install -e .

# Activate the virtual environment
source .venv/bin/activate
```

## Configuration

Set your PW API key as an environment variable:

```bash
export PW_API_KEY=pwt_xxxxx.xxxxx
```

Or create a `.env` file (see `.env.example`).

## Quick Start

```bash
# After installation, with venv activated:
pw-workflow-runner list

# Or without activating venv, use uv run:
uv run pw-workflow-runner list
```

## Usage

### Interactive Mode

Run without arguments for a guided experience:

```bash
pw-workflow-runner
```

This will:
1. List available workflows and prompt you to select one
2. Ask how you want to provide inputs (JSON file or manual entry)
3. Execute the workflow and show live status updates

### List Workflows

```bash
# Table format
pw-workflow-runner list

# JSON format
pw-workflow-runner list --json
```

### Run a Workflow

```bash
# With JSON input file
pw-workflow-runner run hello-world --input inputs/hello-world.json

# With inline parameters
pw-workflow-runner run hello-world -p "hello.message=test"

# Combine file and overrides
pw-workflow-runner run hello-world --input inputs/hello-world.json -p "hello.message=override"

# Submit without waiting for completion
pw-workflow-runner run hello-world --input inputs/hello-world.json --no-wait

# Custom timeout (default: 3600s)
pw-workflow-runner run hello-world --input inputs/hello-world.json --timeout 600

# Output as JSON
pw-workflow-runner run hello-world --input inputs/hello-world.json --json
```

### Check Run Status

```bash
pw-workflow-runner status hello-world 42
pw-workflow-runner status hello-world 42 --json
```

## Input Files

Input files are JSON payloads matching the workflow's expected parameters. These can be exported directly from the PW platform.

Example `inputs/hello-world.json`:

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
  "hello": {
    "v3": true,
    "message": "Hello from the CLI"
  },
  "resource": {
    "id": "your-resource-id",
    "name": "your-resource-name",
    "type": "existing",
    "provider": "existing"
  }
}
```

## Project Structure

```
pw-workflow-runner/
├── pyproject.toml
├── .env.example
├── src/
│   └── pw_workflow_runner/
│       ├── __init__.py
│       ├── __main__.py         # Entry point
│       ├── client.py           # PW SDK wrapper
│       ├── models.py           # Pydantic models
│       ├── executor.py         # Workflow execution + polling
│       ├── interactive.py      # Interactive mode
│       └── cli.py              # CLI commands
└── inputs/
    └── hello-world.json        # Example input
```

## Development

```bash
# Install with dev dependencies
uv pip install -e ".[dev]"

# Run linter
ruff check src/

# Run type checker
mypy src/
```

## License

MIT
