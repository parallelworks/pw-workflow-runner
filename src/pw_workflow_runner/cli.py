"""CLI commands for PW Workflow Runner."""

import json
import sys
from pathlib import Path
from typing import Optional

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from .client import PWClient, PWClientError
from .executor import ExecutionResult, ExecutionTimeout, WorkflowExecutor
from .models import RunInfo

# Load .env file if present
load_dotenv()

console = Console()


def print_error(message: str):
    """Print error message to stderr."""
    console.print(f"[red]Error:[/red] {message}", style="red")


def print_success(message: str):
    """Print success message."""
    console.print(f"[green]{message}[/green]")


def print_status_update(run_info: RunInfo, elapsed: float):
    """Print status update during polling."""
    console.print(f"  Status: [cyan]{run_info.status}[/cyan] ({elapsed:.0f}s)")


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx):
    """PW Workflow Runner - Execute workflows on the Parallel Works ACTIVATE platform.

    Run without arguments for interactive mode.
    """
    if ctx.invoked_subcommand is None:
        # No subcommand - run interactive mode
        from .interactive import run_interactive

        run_interactive()


@main.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def list_workflows(as_json: bool):
    """List available workflows in the PW account."""
    try:
        with PWClient() as client:
            workflows = client.list_workflows()

            if as_json:
                data = [w.model_dump(by_alias=True) for w in workflows]
                click.echo(json.dumps(data, indent=2, default=str))
                return

            if not workflows:
                console.print("No workflows found.")
                return

            table = Table(title="Available Workflows")
            table.add_column("Name", style="cyan")
            table.add_column("Display Name")
            table.add_column("Type")
            table.add_column("Description")

            for w in workflows:
                table.add_row(
                    w.name,
                    w.display_name or "-",
                    w.type,
                    (w.description[:50] + "...") if w.description and len(w.description) > 50 else (w.description or "-"),
                )

            console.print(table)
            console.print(f"\nTotal: {len(workflows)} workflow(s)")

    except PWClientError as e:
        print_error(str(e))
        sys.exit(1)
    except Exception as e:
        print_error(f"Failed to list workflows: {e}")
        sys.exit(1)


@main.command("run")
@click.argument("workflow_name")
@click.option("--input", "-i", "input_file", type=click.Path(exists=True), help="JSON input file")
@click.option("--param", "-p", "params", multiple=True, help="Input parameter as key=value")
@click.option("--timeout", "-t", type=float, default=3600, help="Timeout in seconds (default: 3600)")
@click.option("--no-wait", is_flag=True, help="Submit and exit without waiting for completion")
@click.option("--json", "as_json", is_flag=True, help="Output result as JSON")
def run_workflow(
    workflow_name: str,
    input_file: Optional[str],
    params: tuple,
    timeout: float,
    no_wait: bool,
    as_json: bool,
):
    """Run a workflow with inputs.

    Examples:

        pw-workflow-runner run hello-world --input inputs/hello-world.json

        pw-workflow-runner run hello-world -p "hello.message=test"
    """
    # Build inputs
    inputs = {}

    if input_file:
        with open(input_file) as f:
            inputs = json.load(f)

    # Apply param overrides
    for param in params:
        if "=" not in param:
            print_error(f"Invalid param format: {param}. Use key=value or key.subkey=value")
            sys.exit(1)

        key, value = param.split("=", 1)

        # Try to parse value as JSON, fallback to string
        try:
            parsed_value = json.loads(value)
        except json.JSONDecodeError:
            parsed_value = value

        # Handle nested keys like "hello.message"
        _set_nested(inputs, key.split("."), parsed_value)

    if not inputs and not input_file:
        print_error("No inputs provided. Use --input FILE or -p key=value")
        sys.exit(1)

    try:
        with PWClient() as client:
            executor = WorkflowExecutor(client, timeout=timeout)

            if not as_json:
                console.print(f"Submitting workflow: [cyan]{workflow_name}[/cyan]")

            result = executor.execute(
                workflow_name=workflow_name,
                inputs=inputs,
                on_status=None if as_json else print_status_update,
                wait=not no_wait,
            )

            _print_result(result, as_json)

            sys.exit(0 if result.success else 1)

    except PWClientError as e:
        print_error(str(e))
        sys.exit(1)
    except ExecutionTimeout as e:
        print_error(str(e))
        sys.exit(1)
    except Exception as e:
        print_error(f"Failed to run workflow: {e}")
        sys.exit(1)


@main.command("status")
@click.argument("workflow_name")
@click.argument("run_number", type=int)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def check_status(workflow_name: str, run_number: int, as_json: bool):
    """Check the status of a workflow run.

    Example:

        pw-workflow-runner status hello-world 42
    """
    try:
        with PWClient() as client:
            run_info = client.get_run_status(workflow_name, run_number)

            if as_json:
                click.echo(json.dumps(run_info.model_dump(by_alias=True), indent=2, default=str))
                return

            console.print(f"Workflow: [cyan]{run_info.workflow_name}[/cyan]")
            console.print(f"Run: #{run_info.number}")
            console.print(f"Status: [{'green' if run_info.status.lower() == 'completed' else 'yellow'}]{run_info.status}[/]")
            console.print(f"Created: {run_info.created_at}")
            if run_info.completed_at:
                console.print(f"Completed: {run_info.completed_at}")

    except PWClientError as e:
        print_error(str(e))
        sys.exit(1)
    except Exception as e:
        print_error(f"Failed to get status: {e}")
        sys.exit(1)


def _set_nested(d: dict, keys: list[str], value):
    """Set a nested dictionary value given a list of keys."""
    for key in keys[:-1]:
        d = d.setdefault(key, {})
    d[keys[-1]] = value


def _print_result(result: ExecutionResult, as_json: bool):
    """Print execution result."""
    if as_json:
        data = {
            "workflow_name": result.workflow_name,
            "run_number": result.run_number,
            "status": result.status,
            "started_at": result.started_at.isoformat(),
            "completed_at": result.completed_at.isoformat() if result.completed_at else None,
            "duration_seconds": result.duration_seconds,
            "success": result.success,
        }
        click.echo(json.dumps(data, indent=2))
        return

    console.print()
    if result.success:
        print_success(f"Workflow completed successfully")
    else:
        print_error(f"Workflow {result.status}")

    console.print(f"  Run: #{result.run_number}")
    if result.duration_seconds:
        console.print(f"  Duration: {result.duration_seconds:.1f}s")


if __name__ == "__main__":
    main()
