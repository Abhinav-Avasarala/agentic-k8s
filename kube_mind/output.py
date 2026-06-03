from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box
from typing import Any

console = Console()


def print_plan(ops: list[dict[str, Any]]) -> None:
    table = Table(title="Planned Operations", box=box.ROUNDED, show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Type", style="cyan")
    table.add_column("Action", style="bold")
    table.add_column("Params", style="white")

    for i, op in enumerate(ops, 1):
        params = ", ".join(f"{k}={v}" for k, v in op.get("params", {}).items())
        table.add_row(str(i), op.get("type", ""), op.get("action", ""), params)

    console.print(table)


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

    table = Table(title=f"Cluster: {cluster['name']} | {cluster.get('zone', '?')}", box=box.SIMPLE_HEAVY)
    table.add_column("Node Pool", style="cyan")
    table.add_column("Machine", style="white")
    table.add_column("Count", justify="right")
    table.add_column("GPU", style="yellow")

    for pool in cluster.get("node_pools", []):
        table.add_row(
            pool.get("name", ""),
            pool.get("machine", ""),
            str(pool.get("count", "")),
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
