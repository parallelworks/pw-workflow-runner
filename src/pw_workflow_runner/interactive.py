"""Interactive mode for workflow execution."""

import json
import sys
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from .client import PWClient, PWClientError
from .executor import ExecutionResult, ExecutionTimeout, WorkflowExecutor
from .models import RunInfo, WorkflowInfo

console = Console()


def run_interactive():
    """Run the interactive workflow execution flow."""
    console.print("\n[bold cyan]PW Workflow Runner[/bold cyan] - Interactive Mode\n")

    try:
        with PWClient() as client:
            # Step 1: List and select workflow
            workflow = _select_workflow(client)
            if not workflow:
                return

            console.print(f"\nSelected: [cyan]{workflow.name}[/cyan]")
            if workflow.description:
                console.print(f"Description: {workflow.description}")

            # Step 2: Get inputs
            inputs = _get_inputs()
            if inputs is None:
                console.print("Cancelled.")
                return

            # Step 3: Confirm and execute
            console.print("\n[bold]Ready to execute:[/bold]")
            console.print(f"  Workflow: [cyan]{workflow.name}[/cyan]")
            console.print(f"  Inputs: {len(inputs)} parameter(s)")

            if not Confirm.ask("\nProceed?", default=True):
                console.print("Cancelled.")
                return

            # Step 4: Execute
            console.print()
            _execute_workflow(client, workflow.name, inputs)

    except PWClientError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n\nCancelled.")
        sys.exit(0)


def _select_workflow(client: PWClient) -> WorkflowInfo | None:
    """Display workflow list and let user select one."""
    console.print("Fetching workflows...")
    workflows = client.list_workflows()

    if not workflows:
        console.print("[yellow]No workflows found in this account.[/yellow]")
        return None

    # Display table
    table = Table(title="Available Workflows")
    table.add_column("#", style="dim")
    table.add_column("Name", style="cyan")
    table.add_column("Display Name")
    table.add_column("Type")

    for i, w in enumerate(workflows, 1):
        table.add_row(str(i), w.name, w.display_name or "-", w.type)

    console.print(table)
    console.print()

    # Get selection
    while True:
        selection = Prompt.ask(
            "Select workflow number (or 'q' to quit)",
            default="1",
        )

        if selection.lower() == "q":
            return None

        try:
            idx = int(selection) - 1
            if 0 <= idx < len(workflows):
                return workflows[idx]
            console.print(f"[yellow]Please enter a number between 1 and {len(workflows)}[/yellow]")
        except ValueError:
            console.print("[yellow]Please enter a valid number[/yellow]")


def _get_inputs() -> dict | None:
    """Get workflow inputs from user."""
    console.print("\n[bold]How do you want to provide inputs?[/bold]")
    console.print("  1. Load from JSON file")
    console.print("  2. Enter manually (basic key=value)")
    console.print("  3. Run with empty inputs")

    choice = Prompt.ask("Select", choices=["1", "2", "3", "q"], default="1")

    if choice == "q":
        return None

    if choice == "1":
        return _load_inputs_from_file()
    elif choice == "2":
        return _enter_inputs_manually()
    else:
        return {}


def _load_inputs_from_file() -> dict | None:
    """Load inputs from a JSON file."""
    while True:
        file_path = Prompt.ask("Input file path (or 'q' to go back)")

        if file_path.lower() == "q":
            return _get_inputs()  # Go back to input method selection

        path = Path(file_path).expanduser()

        if not path.exists():
            console.print(f"[yellow]File not found: {path}[/yellow]")
            continue

        try:
            with open(path) as f:
                inputs = json.load(f)
            console.print(f"[green]Loaded {len(inputs)} top-level parameter(s)[/green]")
            return inputs
        except json.JSONDecodeError as e:
            console.print(f"[red]Invalid JSON: {e}[/red]")
        except Exception as e:
            console.print(f"[red]Error reading file: {e}[/red]")


def _enter_inputs_manually() -> dict:
    """Enter inputs manually as key=value pairs."""
    console.print("\nEnter inputs as key=value (empty line to finish):")
    console.print("  Use dot notation for nested: [dim]hello.message=test[/dim]")

    inputs = {}

    while True:
        line = Prompt.ask("", default="")

        if not line:
            break

        if "=" not in line:
            console.print("[yellow]Format: key=value[/yellow]")
            continue

        key, value = line.split("=", 1)

        # Try to parse as JSON
        try:
            parsed_value = json.loads(value)
        except json.JSONDecodeError:
            parsed_value = value

        # Handle nested keys
        _set_nested(inputs, key.strip().split("."), parsed_value)
        console.print(f"  [dim]Set {key}[/dim]")

    return inputs


def _set_nested(d: dict, keys: list[str], value):
    """Set a nested dictionary value."""
    for key in keys[:-1]:
        d = d.setdefault(key, {})
    d[keys[-1]] = value


def _execute_workflow(client: PWClient, workflow_name: str, inputs: dict):
    """Execute the workflow and show progress."""
    executor = WorkflowExecutor(client)

    console.print(f"Submitting [cyan]{workflow_name}[/cyan]...")

    def on_status(run_info: RunInfo, elapsed: float):
        status_color = "green" if run_info.status.lower() == "completed" else "cyan"
        console.print(f"  Status: [{status_color}]{run_info.status}[/] ({elapsed:.0f}s)")

    try:
        result = executor.execute(
            workflow_name=workflow_name,
            inputs=inputs,
            on_status=on_status,
        )

        _print_result(result)

    except ExecutionTimeout as e:
        console.print(f"\n[red]Timeout:[/red] {e}")
        sys.exit(1)


def _print_result(result: ExecutionResult):
    """Print the final execution result."""
    console.print()

    if result.success:
        console.print("[bold green]Workflow completed successfully[/bold green]")
    else:
        console.print(f"[bold red]Workflow {result.status}[/bold red]")

    console.print(f"  Run: #{result.run_number}")
    if result.duration_seconds:
        console.print(f"  Duration: {result.duration_seconds:.1f}s")
