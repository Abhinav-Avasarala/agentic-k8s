import sys
import typer
from pathlib import Path
from typing import Optional, List
from rich.console import Console
from rich.table import Table
from rich import box
from dotenv import load_dotenv

load_dotenv()

from kube_mind.state import StateManager
from kube_mind import output

_SUBCOMMANDS = {"diff", "status", "history", "undo", "monitor", "init", "eval"}

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
    from kube_mind.tools.prometheus_tools import VerifyError

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

    # Pure verify ops (health checks, diagnostics) need no confirmation — they are read-only.
    all_verify = all(op.get("type") == "verify" for op in ops)

    if not all_verify:
        # Risk-aware confirmation gate for ops that mutate the cluster
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

    console.print("[dim]Executing...[/dim]")
    try:
        verify_results, enriched_ops = execute(ops, state._data)
    except VerifyError as e:
        state.record(intent, ops, "failed")
        state.save()
        console.print(f"[red bold]Verify failed:[/red bold] {e}")
        return

    if verify_results:
        output.print_verify_results(verify_results)

    state.record(intent, enriched_ops, "success")
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
    import copy
    from kube_mind.agent.graph import plan
    from kube_mind.guardrails.guard import check_intent, check_ops, GuardrailsError

    state.load()
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
        console.print(f"[red bold]Blocked:[/red bold] {e}")
        return

    # Simulate gcloud ops against current cluster state to derive desired node pools
    desired_pools: dict = {p["name"]: copy.deepcopy(p) for p in state.cluster.get("node_pools", [])}
    for op in ops:
        if op["type"] != "gcloud":
            continue
        action, params = op["action"], op["params"]
        if action == "create_node_pool":
            desired_pools[params["name"]] = {
                "name": params["name"],
                "machine": params.get("machine", "e2-medium"),
                "count": params.get("count", 1),
            }
        elif action == "delete_node_pool":
            desired_pools.pop(params["name"], None)
        elif action == "resize_node_pool":
            if params["name"] in desired_pools:
                desired_pools[params["name"]] = {**desired_pools[params["name"]], "count": params["count"]}

    delta = state.diff({"node_pools": list(desired_pools.values())})
    output.print_diff(delta)

    # Show kubectl / workload changes that don't appear in the node pool diff
    kubectl_ops = [op for op in ops if op["type"] == "kubectl"]
    if kubectl_ops:
        console.print()
        console.print("[dim]Workload changes (applied but not shown in diff above):[/dim]")
        for op in kubectl_ops:
            params = op.get("params", {})
            summary = ", ".join(f"{k}={v}" for k, v in params.items())
            console.print(f"  [cyan]{op['action']}[/cyan]  {summary}")

    console.print()
    console.print("[dim]Run without 'diff' to apply.[/dim]")


def _setup_monitoring() -> None:
    from kube_mind.tools.monitoring_tools import install_prometheus, start_port_forward, MonitoringError

    console.print()
    with console.status("[dim]Installing Prometheus monitoring stack (this takes ~2 min)...[/dim]"):
        try:
            installed = install_prometheus()
        except MonitoringError as e:
            console.print(f"[yellow]Monitoring setup skipped:[/yellow] {e}")
            return

    if installed:
        console.print("[green]Prometheus installed.[/green]")
    else:
        console.print("[dim]Prometheus already installed — skipping.[/dim]")

    with console.status("[dim]Starting port-forward to Prometheus...[/dim]"):
        try:
            start_port_forward()
        except MonitoringError as e:
            console.print(f"[yellow]Port-forward failed:[/yellow] {e}")
            return

    console.print("[green]Prometheus ready at http://localhost:9090[/green]")
    console.print("[dim]Try: kube-mind \"something feels slow\"[/dim]")


@app.command()
def init(
    project: str = typer.Option(..., "--project", "-p", help="GCP project ID"),
    zone: str = typer.Option(..., "--zone", "-z", help="GCP zone, e.g. us-east1-b"),
    cluster: str = typer.Option(..., "--cluster", "-c", help="GKE cluster name"),
    monitoring: bool = typer.Option(True, "--monitoring/--no-monitoring", help="Install Prometheus monitoring stack"),
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

    if monitoring:
        _setup_monitoring()


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


def _invert_op(op: dict) -> Optional[dict]:
    """Return the inverse of op, or None if it cannot be auto-inverted."""
    action = op["action"]
    params = op["params"]
    before = op.get("before", {})

    if action == "create_node_pool":
        return {"type": "gcloud", "action": "delete_node_pool", "params": {"name": params["name"]}}
    if action == "delete_node_pool" and before:
        return {"type": "gcloud", "action": "create_node_pool", "params": before}
    if action == "resize_node_pool" and "count" in before:
        return {"type": "gcloud", "action": "resize_node_pool", "params": {**params, "count": before["count"]}}
    if action == "scale_deployment" and "replicas" in before:
        return {"type": "kubectl", "action": "scale_deployment", "params": {**params, "replicas": before["replicas"]}}
    if action == "cordon_node":
        return {"type": "kubectl", "action": "uncordon_node", "params": params}
    if action == "drain_node":
        return {"type": "kubectl", "action": "uncordon_node", "params": params}
    if action == "taint_node":
        return {"type": "kubectl", "action": "untaint_node", "params": params}
    return None


@app.command()
def undo():
    """Roll back the last operation by inverting the most recent change."""
    state.load()
    last = state.last_operation()
    if last is None:
        console.print("[yellow]Nothing to undo — history is empty.[/yellow]")
        raise typer.Exit()

    console.print(f"[bold]Last operation:[/bold] {last['input']} ({last['ts'][:19].replace('T', ' ')})")

    mutable_ops = [op for op in last["ops"] if op.get("type") in ("gcloud", "kubectl")]
    inverse_ops: list = []
    skipped: list = []

    for op in reversed(mutable_ops):
        inv = _invert_op(op)
        if inv:
            inverse_ops.append(inv)
        else:
            skipped.append(op)

    if not inverse_ops:
        console.print("[yellow]Nothing to undo — no invertible ops found.[/yellow]")
        if skipped:
            console.print("[dim]Skipped (cannot auto-invert):[/dim]")
            for op in skipped:
                console.print(f"  [dim]• {op['action']}[/dim]")
        raise typer.Exit()

    output.print_plan(inverse_ops)

    if skipped:
        console.print(f"[yellow]⚠  {len(skipped)} op(s) cannot be auto-inverted and will be skipped:[/yellow]")
        for op in skipped:
            console.print(f"   [dim]• {op['action']}[/dim]")

    if not output.confirm("Apply undo?"):
        console.print("[dim]Aborted.[/dim]")
        raise typer.Exit()

    from kube_mind.agent.graph import execute
    from kube_mind.tools.prometheus_tools import VerifyError

    console.print("[dim]Executing undo...[/dim]")
    try:
        execute(inverse_ops, state._data)
    except VerifyError as e:
        console.print(f"[red bold]Undo failed:[/red bold] {e}")
        return

    state.record(f"undo: {last['input']}", inverse_ops, "success")
    state.save()
    console.print("[green]Undone.[/green]")


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


@app.command(name="eval")
def eval_models(
    model: Optional[List[str]] = typer.Option(None, "--model", "-m", help="Model(s) to evaluate, e.g. gpt-4o, claude-sonnet-4-6"),
    test_cases: Path = typer.Option(Path("eval/test_cases.json"), "--test-cases", "-t", help="Path to test cases JSON"),
    tag: Optional[List[str]] = typer.Option(None, "--tag", help="Filter to cases with this tag (repeatable)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print each case result as it runs"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Save full results to JSON file"),
):
    """Evaluate planner quality across models using labelled test cases."""
    import sys as _sys
    _project_root = str(Path(__file__).parent.parent)
    if _project_root not in _sys.path:
        _sys.path.insert(0, _project_root)
    from eval.models import get_adapter, estimate_cost
    from eval.runner import load_cases, run_eval

    models = model or ["gpt-4o"]

    try:
        cases = load_cases(test_cases, filter_tags=list(tag) if tag else None)
    except FileNotFoundError:
        console.print(f"[red]Test cases file not found:[/red] {test_cases}")
        raise typer.Exit(1)

    console.print(f"[bold]Running eval:[/bold] {len(cases)} cases  ×  {len(models)} model(s)\n")

    adapters = []
    for m in models:
        try:
            adapters.append(get_adapter(m))
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)

    all_results = run_eval(adapters, cases, verbose=verbose)

    # --- Per-model detailed table ---
    for model_name, results in all_results.items():
        table = Table(
            title=f"Eval — {model_name}  ({len(results)} cases)",
            box=box.ROUNDED, show_lines=False,
        )
        table.add_column("Case", style="white", min_width=28)
        table.add_column("Tags", style="dim", min_width=16)
        table.add_column("F1",  justify="right", width=6)
        table.add_column("P",   justify="right", width=6)
        table.add_column("R",   justify="right", width=6)
        table.add_column("ms",  justify="right", width=6)
        table.add_column("Tokens", justify="right", width=8)

        for r in results:
            tick = "[green]✓[/green]" if r.passed else "[red]✗[/red]"
            label = f"{tick} {r.case_id}"
            tags_str = ", ".join(r.tags[:3])
            err = f"  [red dim]{r.error[:40]}[/red dim]" if r.error else ""
            table.add_row(
                label + err,
                tags_str,
                f"{r.f1:.2f}",
                f"{r.precision:.2f}",
                f"{r.recall:.2f}",
                str(r.latency_ms),
                str(r.prompt_tokens + r.completion_tokens),
            )

        console.print(table)

        # Failures detail
        failures = [r for r in results if not r.passed]
        if failures:
            console.print("[dim]Failures:[/dim]")
            for r in failures:
                if r.error:
                    console.print(f"  [red]✗[/red] {r.case_id}: [red]{r.error[:80]}[/red]")
                else:
                    missed = [f"{o['action']}({list(o.get('params',{}).values())})" for o in r.missed_ops]
                    extra  = [f"{o['action']}({list(o.get('params',{}).values())})" for o in r.extra_ops]
                    if missed:
                        console.print(f"  [red]✗[/red] {r.case_id}  missed: [yellow]{', '.join(missed)}[/yellow]")
                    if extra:
                        console.print(f"       extra:  [dim]{', '.join(extra)}[/dim]")

        passed = sum(1 for r in results if r.passed)
        avg_f1 = sum(r.f1 for r in results) / len(results)
        avg_ms = sum(r.latency_ms for r in results) / len(results)
        total_prompt = sum(r.prompt_tokens for r in results)
        total_comp   = sum(r.completion_tokens for r in results)
        cost = estimate_cost(model_name, total_prompt, total_comp)
        console.print(
            f"\n  [bold]{passed}/{len(results)} passed[/bold] ({100*passed//len(results)}%)  "
            f"avg F1: [cyan]{avg_f1:.2f}[/cyan]  "
            f"avg latency: [cyan]{avg_ms:.0f}ms[/cyan]  "
            f"est. cost: [cyan]~${cost:.3f}[/cyan]\n"
        )

    # --- Multi-model comparison summary ---
    if len(all_results) > 1:
        cmp = Table(title="Comparison", box=box.SIMPLE_HEAVY)
        cmp.add_column("Model",      style="bold", min_width=28)
        cmp.add_column("Passed",     justify="right")
        cmp.add_column("Avg F1",     justify="right")
        cmp.add_column("Avg ms",     justify="right")
        cmp.add_column("Est. cost",  justify="right")

        for model_name, results in all_results.items():
            passed   = sum(1 for r in results if r.passed)
            avg_f1   = sum(r.f1 for r in results) / len(results)
            avg_ms   = sum(r.latency_ms for r in results) / len(results)
            cost     = estimate_cost(
                model_name,
                sum(r.prompt_tokens for r in results),
                sum(r.completion_tokens for r in results),
            )
            cmp.add_row(
                model_name,
                f"{passed}/{len(results)}",
                f"{avg_f1:.2f}",
                f"{avg_ms:.0f}ms",
                f"~${cost:.3f}",
            )
        console.print(cmp)

    # --- Optional JSON dump ---
    if output:
        import json
        from dataclasses import asdict
        dump = {
            m: [
                {**{k: v for k, v in vars(r).items() if not k.startswith("_")}}
                for r in res
            ]
            for m, res in all_results.items()
        }
        output.write_text(json.dumps(dump, indent=2))
        console.print(f"[dim]Results saved to {output}[/dim]")
