# Contributing to PW Workflow Runner

We welcome contributions to the PW Workflow Runner!

## Development Setup

```bash
# Clone the repository
git clone https://github.com/parallelworks/pw-workflow-runner.git
cd pw-workflow-runner

# Install dependencies
uv venv && uv pip install -e ".[dev]"

# Run linter
ruff check src/

# Run type checker
mypy src/
```

## Making Changes

1. Create a feature branch from main
2. Make your changes
3. Run linter and type checker
4. Submit a pull request
