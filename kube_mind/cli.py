import sys
import typer
from typing import Optional
from rich.console import Console

from kube_mind.state import StateManager
from kube_mind import output

_SUBCOMMANDS = {"diff", "status", "history", "undo", "monitor"}

app = typer.Typer(
    name="kube-mind",
    help="Natural language Kubernetes infrastructure CLI.\n\nProvision, configure, monitor, and modify a GKE cluster in plain English.",
    rich_markup_mode="rich",
)
console = Console()
state = StateManager()


def _run_agent(intent: str, dry_run: bool = False) -> None:
    from kube_mind.agent.graph import plan, execute

    console.print(f"[bold cyan]→[/bold cyan] [italic]{intent}[/italic]")

    with console.status("[dim]Planning...[/dim]"):
        ops = plan(intent, state._data)

    output.print_plan(ops)

    if not ops:
        console.print("[dim]Nothing to do — cluster already matches intent.[/dim]")
        return

    if dry_run:
        console.print("[dim]--dry-run: no changes applied.[/dim]")
        return

    if not output.confirm("Apply these changes?"):
        console.print("[dim]Aborted.[/dim]")
        return

    with console.status("[dim]Executing...[/dim]"):
        execute(ops, state._data)

    state.record(intent, ops, "success")
    state.save()
    console.print("[green]Done.[/green]")


def cli() -> None:
    """Entry point. Routes natural language to the agent; named subcommands go to Typer."""
    args = sys.argv[1:]
    first = args[0] if args else None

    if first is None:
        app(standalone_mode=False)
        console.print(typer.main.get_command(app).get_help(typer.Context(typer.main.get_command(app))))
        return

    if first not in _SUBCOMMANDS and not first.startswith("-"):
        intent = first
        dry_run = "--dry-run" in args or "-n" in args
        state.load()
        _run_agent(intent, dry_run=dry_run)
    else:
        app()


@app.command()
def diff(
    intent: str = typer.Argument(..., help="Desired state in plain English"),
):
    """Show what WOULD change without executing — like terraform plan."""
    state.load()
    # Placeholder: the planner will populate desired_state once built
    desired_state: dict = {"node_pools": []}
    delta = state.diff(desired_state)
    output.print_diff(delta)
    console.print("[dim]Run without 'diff' to apply.[/dim]")


@app.command()
def status():
    """Print a summary of the current cluster: node pools, workloads, resource usage."""
    state.load()
    output.print_status(state.cluster, state.workloads)


@app.command()
def history():
    """Show a log of every past operation: timestamp, intent, what the agent did, outcome."""
    state.load()
    output.print_history(state.history)


@app.command()
def undo():
    """Roll back the last operation by inverting the most recent change."""
    state.load()
    last = state.last_operation()
    if last is None:
        console.print("[yellow]Nothing to undo — history is empty.[/yellow]")
        raise typer.Exit()

    console.print(f"[bold]Last operation:[/bold] {last['input']} ({last['ts'][:19].replace('T', ' ')})")
    if not output.confirm("Undo this operation?"):
        console.print("[dim]Aborted.[/dim]")
        raise typer.Exit()

    # Placeholder — agent will execute inverse ops in Step 6
    console.print("[yellow]Undo execution not yet implemented (Step 6).[/yellow]")


@app.command()
def monitor(
    interval: int = typer.Option(60, "--interval", "-i", help="Polling interval in seconds"),
    slack_webhook: Optional[str] = typer.Option(None, "--slack", help="Slack webhook URL for anomaly alerts"),
):
    """Start a background daemon that polls Prometheus for anomalies."""
    state.load()
    cluster_name = state.cluster.get("name", "unknown")
    console.print(f"[bold cyan]→[/bold cyan] Monitoring cluster [bold]{cluster_name}[/bold] every {interval}s")
    console.print("[dim]Press Ctrl+C to stop.[/dim]")

    # Placeholder — Prometheus polling added in Step 10
    console.print("[yellow]Monitor daemon not yet implemented (Step 10).[/yellow]")
