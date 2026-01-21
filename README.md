# PW Workflow Runner

A command-line tool for executing workflows on the Parallel Works ACTIVATE platform.

## Features

- **List workflows** available in your PW account
- **Execute workflows** with JSON input payloads
- **Two workflow types**:
  - **Batch** - runs to completion, ends in "completed" state
  - **Session** - interactive sessions that stay "running" and provide a session URL
- **Monitor execution** with live status updates and exponential backoff polling
- **SSH tunneling** - connect to session workflows from your local machine via the `pw` CLI
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

Two workflow types are supported:

- **batch** (default) - Waits for workflow to complete with "completed" status
- **session** - Interactive sessions that stay "running" and provide a session URL

```bash
# Batch workflow (default) - runs to completion
pw-workflow-runner run my-batch-job --input inputs/job.json

# Interactive session workflow - stays running with session URL
pw-workflow-runner run helloworld --input inputs/helloworld.json --type session

# With inline parameters
pw-workflow-runner run helloworld -p "hello.message=test" --type session

# Combine file and overrides
pw-workflow-runner run helloworld --input inputs/helloworld.json -p "hello.message=override" --type session

# Submit without waiting for completion
pw-workflow-runner run helloworld --input inputs/helloworld.json --no-wait

# Custom timeout (default: 3600s)
pw-workflow-runner run helloworld --input inputs/helloworld.json --timeout 600 --type session

# Output as JSON
pw-workflow-runner run helloworld --input inputs/helloworld.json --json --type session
```

### SSH Tunnel for Session Workflows

For session workflows, you can create an SSH tunnel to access the session directly from your local machine without using the PW web interface. This requires the [pw CLI](https://parallelworks.com/docs/cli/pw) to be installed.

The session port is automatically detected from the PW sessions API.

```bash
# Start session with SSH tunnel (port auto-detected from session)
pw-workflow-runner run helloworld --input inputs/helloworld.json --type session --tunnel

# Override local port if the auto-detected port is already in use
pw-workflow-runner run helloworld --input inputs/helloworld.json --type session --tunnel --local-port 9000
```

When the session is ready, the tunnel will be established and you can access it at `http://localhost:<port>`. Press `Ctrl+C` to close the tunnel and exit.

### Check Run Status

```bash
pw-workflow-runner status helloworld 42
pw-workflow-runner status helloworld 42 --json
```

## Input Files

Input files are JSON payloads matching the workflow's expected parameters. These can be exported directly from the PW platform.

Example `inputs/helloworld.json` (interactive session workflow):

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
    └── helloworld.json         # Example input for session workflow
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
