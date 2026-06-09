from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box
from typing import Any

console = Console()


def _params_summary(op: dict[str, Any]) -> str:
    """Human-readable params for the plan table — hides raw PromQL for verify ops."""
    params = op.get("params", {})
    if op.get("action") == "prometheus_query":
        return params.get("label") or params.get("description") or op.get("description", "")
    return ", ".join(f"{k}={v}" for k, v in params.items())


def print_plan(ops: list[dict[str, Any]], risks: list | None = None) -> None:
    from kube_mind.guardrails.guard import RiskLevel

    table = Table(title="Planned Operations", box=box.ROUNDED, show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Type", style="cyan")
    table.add_column("Action", style="bold")
    table.add_column("Params", style="white")
    table.add_column("Risk", width=10)

    for i, op in enumerate(ops, 1):
        params = _params_summary(op)
        level = risks[i - 1][1] if risks else RiskLevel.SAFE

        if level == RiskLevel.CONFIRM_BY_NAME:
            risk_cell = Text("⛔ HIGH", style="bold red")
        elif level == RiskLevel.WARN:
            risk_cell = Text("⚠  WARN", style="bold yellow")
        else:
            risk_cell = Text("-", style="dim")

        table.add_row(str(i), op.get("type", ""), op.get("action", ""), params, risk_cell)

    console.print(table)

    if risks:
        for _, level, reason in risks:
            if reason:
                marker = "[red]⛔[/red]" if level == RiskLevel.CONFIRM_BY_NAME else "[yellow]⚠[/yellow]"
                console.print(f"  {marker} {reason}")


def print_diff(delta: dict[str, Any]) -> None:
    adds = delta.get("adds", [])
    updates = delta.get("updates", [])
    deletes = delta.get("deletes", [])

    if not any([adds, updates, deletes]):
        console.print("[green]No changes. Cluster already matches desired state.[/green]")
        return

    lines = []
    for item in adds:
        lines.append(Text(f"  + add {item['type']}: {item['name']}", style="green"))
    for item in updates:
        lines.append(Text(f"  ~ update {item['type']}: {item['name']}", style="yellow"))
    for item in deletes:
        lines.append(Text(f"  - delete {item['type']}: {item['name']}", style="red"))

    console.print(Panel(
        "\n".join(str(l) for l in lines),
        title="[bold]Diff — what would change[/bold]",
        border_style="blue",
    ))
    console.print(f"[dim]{len(adds)} add(s), {len(updates)} update(s), {len(deletes)} delete(s)[/dim]")


def print_status(cluster: dict[str, Any], workloads: list[dict[str, Any]]) -> None:
    if not cluster.get("name"):
        console.print(Panel("[yellow]No cluster found in state. Run kube-mind with an intent to provision one.[/yellow]", title="Status"))
        return

    cluster_status = cluster.get("status", "")
    status_label = f"  [{cluster_status}]" if cluster_status else ""
    title = f"Cluster: {cluster['name']} | {cluster.get('zone', '?')}{status_label}"

    table = Table(title=title, box=box.SIMPLE_HEAVY)
    table.add_column("Node Pool", style="cyan")
    table.add_column("Machine", style="white")
    table.add_column("Count", justify="right")
    table.add_column("Status", style="green")
    table.add_column("GPU", style="yellow")

    for pool in cluster.get("node_pools", []):
        pool_status = pool.get("status", "-")
        table.add_row(
            pool.get("name", ""),
            pool.get("machine", ""),
            str(pool.get("count", "")),
            pool_status,
            pool.get("gpu", "-"),
        )

    console.print(table)

    if workloads:
        wl_table = Table(title="Workloads", box=box.SIMPLE_HEAVY)
        wl_table.add_column("Name", style="cyan")
        wl_table.add_column("Replicas", justify="right")
        wl_table.add_column("CPU")
        wl_table.add_column("Memory")

        for w in workloads:
            wl_table.add_row(
                w.get("name", ""),
                str(w.get("replicas", "")),
                w.get("cpu", "-"),
                w.get("memory", "-"),
            )
        console.print(wl_table)


def print_history(history: list[dict[str, Any]]) -> None:
    if not history:
        console.print("[dim]No operations recorded yet.[/dim]")
        return

    table = Table(title="Operation History", box=box.ROUNDED, show_lines=True)
    table.add_column("Time", style="dim")
    table.add_column("Intent", style="white")
    table.add_column("Ops", justify="right")
    table.add_column("Outcome", style="cyan")

    for entry in reversed(history):
        table.add_row(
            entry.get("ts", "")[:19].replace("T", " "),
            entry.get("input", ""),
            str(len(entry.get("ops", []))),
            entry.get("outcome", ""),
        )

    console.print(table)


def confirm(prompt: str) -> bool:
    return console.input(f"[yellow]{prompt}[/yellow] [bold]\\[y/n][/bold] ").strip().lower() == "y"


def print_verify_results(results: list) -> None:
    """Render (op, result_dict) pairs returned by execute()."""
    structural = [(op, r) for op, r in results if op.get("action") != "prometheus_query"]
    prom = [(op, r) for op, r in results if op.get("action") == "prometheus_query"]

    for op, r in structural:
        action = op.get("action", "")
        if action == "check_nodes_ready":
            console.print(f"  [green]✓[/green]  Pool '[bold]{r['pool']}[/bold]': {r['ready']}/{r['total']} nodes ready")
        elif action == "check_deployment_ready":
            console.print(f"  [green]✓[/green]  Deployment '[bold]{r['deployment']}[/bold]': {r['ready']}/{r['desired']} replicas ready")
        elif action == "check_pod_scheduled":
            console.print(f"  [green]✓[/green]  Deployment '[bold]{r['deployment']}[/bold]': {r['scheduled']}/{r['desired']} pods scheduled")

    if not prom:
        return

    health_table = Table(title="Cluster Health", box=box.SIMPLE_HEAVY)
    health_table.add_column("Metric", style="bold", min_width=18)
    health_table.add_column("Value", justify="right", min_width=10)
    health_table.add_column("Status", justify="left", min_width=12)

    for op, r in prom:
        label = r.get("label") or op.get("params", {}).get("label", "metric")
        warn_above = r.get("warn_above")
        crit_above = r.get("crit_above")
        metric_results = r.get("results", [])

        if not metric_results:
            health_table.add_row(label, "[dim]0[/dim]", "[green]✓  OK[/green]")
            continue

        first_metric = metric_results[0].get("metric", {})

        # Scalar result (no labels) — aggregated queries land here
        if not first_metric:
            val = float(metric_results[0]["value"][1])
            unit = r.get("unit", "%")

            if unit == "%":
                val_str = f"{val:.1f}%"
            elif unit == "millicores":
                val_str = f"{val:.0f}m"
            elif unit == "MB":
                val_str = f"{val / 1_048_576:.1f} MB"
            elif unit in ("pods", "restarts"):
                val_str = f"{int(val)}"
            else:
                val_str = f"{val:.2f}"

            if crit_above is not None and val > crit_above:
                status = "[bold red]✗  CRITICAL[/bold red]"
            elif warn_above is not None and val > warn_above:
                status = "[bold yellow]⚠  WARNING[/bold yellow]"
            else:
                status = "[green]✓  OK[/green]"
            health_table.add_row(label, val_str, status)

        # Pod phase results (grouped by namespace + phase)
        elif "phase" in first_metric:
            # Aggregate by namespace
            ns_phases: dict[str, dict[str, int]] = {}
            for entry in metric_results:
                ns  = entry["metric"].get("namespace", "default")
                ph  = entry["metric"].get("phase", "Unknown")
                cnt = int(float(entry["value"][1]))
                ns_phases.setdefault(ns, {})[ph] = cnt

            any_failed  = any(d.get("Failed", 0) or d.get("Unknown", 0) for d in ns_phases.values())
            any_pending = any(d.get("Pending", 0) for d in ns_phases.values())

            if any_failed:
                overall = "[bold red]✗  CRITICAL[/bold red]"
            elif any_pending:
                overall = "[bold yellow]⚠  WARNING[/bold yellow]"
            else:
                overall = "[green]✓  OK[/green]"

            # First row: label + overall status
            first_ns = True
            for ns, phases in sorted(ns_phases.items()):
                running = phases.get("Running", 0)
                pending = phases.get("Pending", 0)
                failed  = phases.get("Failed", 0)

                parts = [f"{running} Running"]
                if pending: parts.append(f"[yellow]{pending} Pending[/yellow]")
                if failed:  parts.append(f"[red]{failed} Failed[/red]")

                row_label  = label if first_ns else ""
                row_status = overall if first_ns else ""
                health_table.add_row(row_label, f"[dim]{ns}[/dim]  " + ", ".join(parts), row_status)
                first_ns = False

        # Anything else — just show row count
        else:
            health_table.add_row(label, f"{len(metric_results)} series", "[dim]—[/dim]")

    console.print()
    console.print(health_table)
