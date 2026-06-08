import sys
import typer
from typing import Optional
from rich.console import Console
from dotenv import load_dotenv

load_dotenv()

from kube_mind.state import StateManager
from kube_mind import output

_SUBCOMMANDS = {"diff", "status", "history", "undo", "monitor", "init"}

app = typer.Typer(
    name="kube-mind",
    help="Natural language Kubernetes infrastructure CLI.\n\nProvision, configure, monitor, and modify a GKE cluster in plain English.",
    rich_markup_mode="rich",
)
console = Console()
state = StateManager()


def _run_agent(intent: str, dry_run: bool = False) -> None:
    from kube_mind.agent.graph import plan, execute
    from kube_mind.guardrails.guard import check_intent, check_ops, risk_check, RiskLevel, GuardrailsError

    console.print(f"[bold cyan]→[/bold cyan] [italic]{intent}[/italic]")

    with console.status("[dim]Checking safety...[/dim]"):
        try:
            check_intent(intent)
        except GuardrailsError as e:
            console.print(f"[red bold]Blocked:[/red bold] {e}")
            return

    with console.status("[dim]Planning...[/dim]"):
        ops = plan(intent, state._data)

    try:
        check_ops(ops)
    except GuardrailsError as e:
        output.print_plan(ops)
        console.print(f"[red bold]Blocked:[/red bold] {e}")
        return

    risks = risk_check(ops)
    output.print_plan(ops, risks)

    if not ops:
        console.print("[dim]Nothing to do — cluster already matches intent.[/dim]")
        return

    if dry_run:
        console.print("[dim]--dry-run: no changes applied.[/dim]")
        return

    # Risk-aware confirmation gate
    high_risk = [(op, msg) for op, level, msg in risks if level == RiskLevel.CONFIRM_BY_NAME]
    has_warn   = any(level == RiskLevel.WARN for _, level, _ in risks)

    if high_risk:
        console.print()
        console.print("[red bold]This plan contains high-risk operations.[/red bold] Type the resource name to confirm each one.")
        for op, msg in high_risk:
            resource = op.get("params", {}).get("name") or op.get("params", {}).get("node", "")
            console.print(f"\n  [red]⛔ {op['action']}[/red] — {msg}")
            typed = console.input(f"     Type [bold]{resource}[/bold] to confirm: ").strip()
            if typed != resource:
                console.print("[dim]Confirmation did not match — aborted.[/dim]")
                return
        if not output.confirm("All risks confirmed. Apply these changes?"):
            console.print("[dim]Aborted.[/dim]")
            return
    elif has_warn:
        console.print()
        for _, level, msg in risks:
            if level == RiskLevel.WARN:
                console.print(f"[yellow]⚠  Warning:[/yellow] {msg}")
        if not output.confirm("Apply these changes?"):
            console.print("[dim]Aborted.[/dim]")
            return
    else:
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
def init(
    project: str = typer.Option(..., "--project", "-p", help="GCP project ID"),
    zone: str = typer.Option(..., "--zone", "-z", help="GCP zone, e.g. us-east1-b"),
    cluster: str = typer.Option(..., "--cluster", "-c", help="GKE cluster name"),
):
    """Connect kube-mind to an existing GKE cluster and seed local state."""
    from kube_mind.tools.gcloud_tools import get_live_cluster_status

    with console.status(f"[dim]Looking up cluster '{cluster}' in {project}/{zone}...[/dim]"):
        try:
            live = get_live_cluster_status(project, zone, cluster)
        except RuntimeError as e:
            console.print(f"[red]GKE API error:[/red] {e}")
            raise typer.Exit(1)

    if live is None:
        console.print(
            f"[red]Cluster '{cluster}' not found in project '{project}' / zone '{zone}'.[/red]"
        )
        console.print("[dim]Double-check the name, project, and zone.[/dim]")
        raise typer.Exit(1)

    state.load()
    state._data["cluster"] = live
    state.save()

    console.print(f"[green]Initialized.[/green] Synced cluster [bold]{cluster}[/bold] to {state.path}")
    output.print_status(live, state.workloads)


@app.command()
def status():
    """Print live cluster status from GKE, then sync local state.json."""
    state.load()
    cluster = state.cluster

    if not cluster.get("name"):
        output.print_status(cluster, state.workloads)
        return

    with console.status("[dim]Fetching live cluster status from GKE...[/dim]"):
        try:
            from kube_mind.tools.gcloud_tools import get_live_cluster_status
            live = get_live_cluster_status(
                cluster["project"], cluster["zone"], cluster["name"]
            )
        except RuntimeError as e:
            console.print(f"[yellow]Warning:[/yellow] {e}")
            console.print("[dim]Showing cached local state instead.[/dim]")
            output.print_status(cluster, state.workloads)
            return

    if live is None:
        console.print(
            f"[red bold]Cluster '{cluster['name']}' not found in GCP.[/red bold]"
        )
        console.print(
            "[dim]It was likely deleted outside kube-mind. "
            "Local state.json still shows the old info.[/dim]"
        )
        if output.confirm("Clear local cluster state?"):
            state._data["cluster"] = {
                "name": None,
                "zone": cluster.get("zone"),
                "project": cluster.get("project"),
                "node_pools": [],
            }
            state.save()
            console.print("[dim]Local state cleared.[/dim]")
        return

    # Sync live node pool data back into state.json
    state._data["cluster"]["node_pools"] = live["node_pools"]
    state._data["cluster"]["status"] = live.get("status")
    state.save()

    output.print_status(live, state.workloads)


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
