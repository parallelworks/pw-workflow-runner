"""CLI commands for PW Workflow Runner."""

import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from .client import PWClient, PWClientError
from .executor import ExecutionResult, ExecutionTimeout, WorkflowExecutor
from .models import RunInfo, WorkflowType

# Load .env file if present
load_dotenv()

console = Console()


def _start_ssh_tunnel(
    user: str,
    local_port: int,
    remote_port: int,
) -> subprocess.Popen:
    """Start an SSH tunnel to the workspace using the pw CLI.

    Args:
        user: PW username for SSH connection.
        local_port: Local port to forward.
        remote_port: Remote port on the workspace.

    Returns:
        Popen process for the SSH tunnel.
    """
    # Check if pw CLI is available
    if not shutil.which("pw"):
        raise RuntimeError(
            "pw CLI not found. Install it from https://parallelworks.com/docs/cli/pw"
        )

    cmd = [
        "ssh",
        "-L", f"{local_port}:localhost:{remote_port}",
        "-o", "ProxyCommand=pw ssh --proxy-command %h",
        "-N",  # Don't execute remote command
        f"{user}@workspace",
    ]

    # Start tunnel in background
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    return process


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
@click.option(
    "--type",
    "workflow_type",
    type=click.Choice(["batch", "session"], case_sensitive=False),
    default="batch",
    help="Workflow type: batch (runs to completion) or session (interactive, stays running)",
)
@click.option("--timeout", "-t", type=float, default=3600, help="Timeout in seconds (default: 3600)")
@click.option("--no-wait", is_flag=True, help="Submit and exit without waiting for completion")
@click.option("--json", "as_json", is_flag=True, help="Output result as JSON")
@click.option(
    "--tunnel",
    is_flag=True,
    help="Create SSH tunnel to session (requires pw CLI). Only for session workflows.",
)
@click.option(
    "--local-port",
    type=int,
    default=None,
    help="Local port for SSH tunnel (default: auto-detect from session)",
)
def run_workflow(
    workflow_name: str,
    input_file: Optional[str],
    params: tuple,
    workflow_type: str,
    timeout: float,
    no_wait: bool,
    as_json: bool,
    tunnel: bool,
    local_port: Optional[int],
):
    """Run a workflow with inputs.

    Examples:

        # Batch workflow (runs to completion)
        pw-workflow-runner run my-batch-job --input inputs/job.json

        # Interactive session workflow (stays running)
        pw-workflow-runner run helloworld --input inputs/helloworld.json --type session

        # Session with SSH tunnel for local access
        pw-workflow-runner run helloworld --input inputs/helloworld.json --type session --tunnel
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

    # Convert workflow type string to enum
    wf_type = WorkflowType.SESSION if workflow_type.lower() == "session" else WorkflowType.BATCH

    # Tunnel only makes sense for session workflows
    if tunnel and wf_type != WorkflowType.SESSION:
        print_error("--tunnel can only be used with --type session")
        sys.exit(1)

    # Get username from inputs for tunnel
    user = inputs.get("resource", {}).get("user", os.environ.get("USER", ""))

    try:
        with PWClient() as client:
            executor = WorkflowExecutor(client, timeout=timeout)

            if not as_json:
                type_label = "session" if wf_type == WorkflowType.SESSION else "batch"
                console.print(f"Submitting {type_label} workflow: [cyan]{workflow_name}[/cyan]")

            result = executor.execute(
                workflow_name=workflow_name,
                inputs=inputs,
                workflow_type=wf_type,
                on_status=None if as_json else print_status_update,
                wait=not no_wait,
            )

            # For session workflows with tunnel, get the session port
            session_port = None
            if tunnel and result.success and wf_type == WorkflowType.SESSION:
                session_info = client.get_session_for_run(workflow_name, result.run_number)
                if session_info and session_info.local_port:
                    session_port = session_info.local_port
                else:
                    print_error("Could not detect session port. Session may not be ready yet.")
                    sys.exit(1)

            # Use user-specified port or auto-detected session port
            tunnel_port = local_port if local_port is not None else session_port

            _print_result(result, as_json, tunnel, tunnel_port)

            # Start tunnel if requested and session is ready
            if tunnel and result.success and wf_type == WorkflowType.SESSION and session_port:
                _run_tunnel(user, tunnel_port, session_port)

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


def _print_result(
    result: ExecutionResult,
    as_json: bool,
    tunnel: bool = False,
    local_port: Optional[int] = None,
):
    """Print execution result."""
    if as_json:
        data = {
            "workflow_name": result.workflow_name,
            "run_number": result.run_number,
            "status": result.status,
            "workflow_type": result.workflow_type.value,
            "started_at": result.started_at.isoformat(),
            "completed_at": result.completed_at.isoformat() if result.completed_at else None,
            "duration_seconds": result.duration_seconds,
            "success": result.success,
            "session_url": result.session_url,
        }
        if tunnel and result.success and local_port:
            data["local_url"] = f"http://localhost:{local_port}"
        click.echo(json.dumps(data, indent=2))
        return

    console.print()
    if result.success:
        if result.workflow_type == WorkflowType.SESSION:
            print_success("Session is ready!")
            if result.session_url:
                console.print(f"  Session URL: [link={result.session_url}]{result.session_url}[/link]")
            if tunnel and local_port:
                console.print(f"  Local URL: [link=http://localhost:{local_port}]http://localhost:{local_port}[/link]")
        else:
            print_success("Workflow completed successfully")
    else:
        print_error(f"Workflow {result.status}")

    console.print(f"  Run: #{result.run_number}")
    if result.duration_seconds:
        console.print(f"  Duration: {result.duration_seconds:.1f}s")


def _run_tunnel(user: str, local_port: int, remote_port: int):
    """Run SSH tunnel and wait for user interrupt.

    Args:
        user: PW username for SSH connection.
        local_port: Local port to forward.
        remote_port: Remote port on the workspace.
    """
    console.print()
    console.print("[cyan]Starting SSH tunnel...[/cyan]")
    console.print(f"  Forwarding localhost:{local_port} -> workspace:{remote_port}")
    console.print()

    try:
        tunnel_process = _start_ssh_tunnel(user, local_port, remote_port)

        # Give it a moment to connect
        import time
        time.sleep(2)

        # Check if tunnel started successfully
        if tunnel_process.poll() is not None:
            # Process already exited - there was an error
            _, stderr = tunnel_process.communicate()
            print_error(f"SSH tunnel failed to start: {stderr.decode().strip()}")
            return

        console.print("[green]Tunnel established![/green]")
        console.print(f"  Access your session at: [link=http://localhost:{local_port}]http://localhost:{local_port}[/link]")
        console.print()
        console.print("[dim]Press Ctrl+C to close the tunnel and exit[/dim]")

        # Wait for the tunnel process or user interrupt
        def handle_sigint(signum, frame):
            console.print("\n[yellow]Closing tunnel...[/yellow]")
            tunnel_process.terminate()
            tunnel_process.wait()
            console.print("[green]Tunnel closed.[/green]")
            sys.exit(0)

        signal.signal(signal.SIGINT, handle_sigint)

        # Keep the main process alive while tunnel runs
        tunnel_process.wait()

    except RuntimeError as e:
        print_error(str(e))


if __name__ == "__main__":
    main()
