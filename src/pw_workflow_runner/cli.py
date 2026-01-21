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

from parallelworks_client import extract_platform_host

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
    debug: bool = False,
) -> subprocess.Popen:
    """Start an SSH tunnel to the workspace using the pw CLI.

    Args:
        user: PW username for SSH connection.
        local_port: Local port to forward.
        remote_port: Remote port on the workspace.
        debug: If True, print the SSH command being run.

    Returns:
        Popen process for the SSH tunnel.
    """
    # Check if pw CLI is available
    if not shutil.which("pw"):
        raise RuntimeError(
            "pw CLI not found. Install it from https://parallelworks.com/docs/cli/pw"
        )

    # Build the SSH command as a shell string
    # Using shell=True ensures the pw CLI gets the proper shell environment
    cmd_str = (
        f'ssh -i ~/.ssh/pwcli '
        f'-L {local_port}:localhost:{remote_port} '
        f'-o "ProxyCommand=pw ssh --proxy-command %h" '
        f'-o StrictHostKeyChecking=no '
        f'-o UserKnownHostsFile=/dev/null '
        f'-N {user}@workspace'
    )

    # Set up environment with PW_PLATFORM_HOST extracted from API key
    env = os.environ.copy()
    api_key = env.get("PW_API_KEY", "")
    if api_key:
        try:
            platform_host = extract_platform_host(api_key)
            env["PW_PLATFORM_HOST"] = platform_host
            if debug:
                console.print(f"[dim]Extracted platform host: {platform_host}[/dim]")
        except Exception as e:
            if debug:
                console.print(f"[yellow]Warning: Could not extract host from API key: {e}[/yellow]")

    if debug:
        console.print(f"[dim]Running: {cmd_str}[/dim]")
        if api_key:
            console.print(f"[dim]PW_API_KEY is set (length: {len(api_key)})[/dim]")
        else:
            console.print("[yellow]Warning: PW_API_KEY is not set[/yellow]")

    # Start tunnel using shell=True with bash and pass environment with PW_PLATFORM_HOST
    # Use start_new_session=True to create a new process group for clean termination
    process = subprocess.Popen(
        cmd_str,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        executable="/bin/bash",
        env=env,
        start_new_session=True,
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
@click.option(
    "--cancel-after",
    type=int,
    default=None,
    help="Automatically cancel the workflow after N seconds (useful for testing)",
)
@click.option("--debug", is_flag=True, help="Show debug info (SSH commands, etc.)")
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
    cancel_after: Optional[int],
    debug: bool,
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
                _run_tunnel(user, tunnel_port, session_port, cancel_after, client, workflow_name, result.run_number, debug)
            elif cancel_after and result.success:
                # No tunnel, but cancel-after requested - wait and then cancel
                console.print(f"\n[yellow]Will cancel workflow in {cancel_after} seconds...[/yellow]")
                import time
                time.sleep(cancel_after)
                console.print(f"\n[yellow]Cancelling workflow {workflow_name} run #{result.run_number}...[/yellow]")
                client.cancel_run(workflow_name, result.run_number)
                console.print("[green]Workflow cancelled.[/green]")

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
@click.option("--debug", is_flag=True, help="Show debug info about session matching")
def check_status(workflow_name: str, run_number: int, as_json: bool, debug: bool):
    """Check the status of a session workflow run.

    Note: This command queries the /api/sessions endpoint to find the session
    associated with the workflow run. It only works for session workflows.

    Example:

        pw-workflow-runner status hello-world 42
    """
    try:
        with PWClient() as client:
            # Use sessions endpoint to find the session for this run
            session_info = client.get_session_for_run(workflow_name, run_number, debug=debug)

            if session_info is None:
                print_error(f"No session found for {workflow_name} run #{run_number}")
                sys.exit(1)

            if as_json:
                click.echo(json.dumps(session_info.model_dump(by_alias=True), indent=2, default=str))
                return

            # Show comprehensive status information
            status = session_info.status or "unknown"
            status_color = "green" if status.lower() == "running" else "yellow"

            console.print(f"\n[bold]Workflow Run Status[/bold]")
            console.print(f"  Workflow:    [cyan]{workflow_name}[/cyan]")
            console.print(f"  Run:         #{run_number}")
            console.print(f"  Status:      [{status_color}]{status}[/{status_color}]")

            console.print(f"\n[bold]Session Details[/bold]")
            console.print(f"  Session ID:  {session_info.id}")
            if session_info.name:
                console.print(f"  Name:        {session_info.name}")
            if session_info.slug:
                console.print(f"  Slug:        {session_info.slug}")
            if session_info.type:
                console.print(f"  Type:        {session_info.type}")
            if session_info.user:
                console.print(f"  User:        {session_info.user}")

            console.print(f"\n[bold]Connection Info[/bold]")
            if session_info.external_href:
                console.print(f"  URL:         {session_info.external_href}")
            if session_info.url:
                console.print(f"  Internal:    {session_info.url}")
            if session_info.domain_name:
                console.print(f"  Domain:      {session_info.domain_name}")
            if session_info.remote_host:
                console.print(f"  Remote Host: {session_info.remote_host}")
            if session_info.remote_port:
                console.print(f"  Remote Port: {session_info.remote_port}")
            if session_info.local_port:
                console.print(f"  Local Port:  {session_info.local_port}")

            console.print()

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


def _run_tunnel(
    user: str,
    local_port: int,
    remote_port: int,
    cancel_after: Optional[int] = None,
    client: Optional["PWClient"] = None,
    workflow_name: Optional[str] = None,
    run_number: Optional[int] = None,
    debug: bool = False,
):
    """Run SSH tunnel and wait for user interrupt or cancel timeout.

    Args:
        user: PW username for SSH connection.
        local_port: Local port to forward.
        remote_port: Remote port on the workspace.
        cancel_after: If set, cancel the workflow after this many seconds.
        client: PWClient instance (required if cancel_after is set).
        workflow_name: Workflow name (required if cancel_after is set).
        run_number: Run number (required if cancel_after is set).
        debug: If True, print debug info.
    """
    console.print()
    console.print("[cyan]Starting SSH tunnel...[/cyan]")
    console.print(f"  Forwarding localhost:{local_port} -> workspace:{remote_port}")
    console.print()

    try:
        tunnel_process = _start_ssh_tunnel(user, local_port, remote_port, debug=debug)

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

        if cancel_after:
            console.print(f"[yellow]Will cancel workflow in {cancel_after} seconds...[/yellow]")
        else:
            console.print("[dim]Press Ctrl+C to close the tunnel and exit[/dim]")

        # Handle cleanup
        def cleanup(cancel_workflow: bool = False):
            console.print("\n[yellow]Closing tunnel...[/yellow]")
            # Kill the entire process group (needed when shell=True)
            try:
                pgid = os.getpgid(tunnel_process.pid)
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                # Process already dead
                pass
            try:
                tunnel_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Force kill if it doesn't respond
                try:
                    os.killpg(os.getpgid(tunnel_process.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
            console.print("[green]Tunnel closed.[/green]")

            if cancel_workflow and client and workflow_name and run_number:
                console.print(f"[yellow]Cancelling workflow {workflow_name} run #{run_number}...[/yellow]")
                try:
                    client.cancel_run(workflow_name, run_number)
                    console.print("[green]Workflow cancelled.[/green]")
                except Exception as e:
                    print_error(f"Failed to cancel workflow: {e}")

        # Wait for the tunnel process or user interrupt
        def handle_sigint(signum, frame):
            cleanup(cancel_workflow=False)
            sys.exit(0)

        signal.signal(signal.SIGINT, handle_sigint)

        if cancel_after:
            # Wait for cancel_after seconds, then cancel
            start_time = time.time()
            while time.time() - start_time < cancel_after:
                if tunnel_process.poll() is not None:
                    # Tunnel died
                    break
                time.sleep(1)
            cleanup(cancel_workflow=True)
        else:
            # Keep the main process alive while tunnel runs
            tunnel_process.wait()

    except RuntimeError as e:
        print_error(str(e))


if __name__ == "__main__":
    main()
