from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

STATE_PATH = Path.home() / ".kube-mind" / "state.json"

_DEFAULT_STATE: dict[str, Any] = {
    "cluster": {
        "name": None,
        "zone": None,
        "project": None,
        "node_pools": [],
    },
    "workloads": [],
    "history": [],
}


class StateManager:
    def __init__(self, path: Path = STATE_PATH):
        self.path = path
        self._data: dict[str, Any] = {}

    def load(self) -> dict[str, Any]:
        if self.path.exists():
            with open(self.path) as f:
                self._data = json.load(f)
        else:
            self._data = json.loads(json.dumps(_DEFAULT_STATE))
        return self._data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2)

    @property
    def cluster(self) -> dict[str, Any]:
        return self._data.get("cluster", {})

    @property
    def workloads(self) -> list[dict[str, Any]]:
        return self._data.get("workloads", [])

    @property
    def history(self) -> list[dict[str, Any]]:
        return self._data.get("history", [])

    def record(self, intent: str, ops: list[dict[str, Any]], outcome: str) -> None:
        self._data.setdefault("history", []).append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "input": intent,
            "ops": ops,
            "outcome": outcome,
        })

    def last_operation(self) -> Optional[dict[str, Any]]:
        h = self._data.get("history", [])
        return h[-1] if h else None

    def diff(self, desired: dict[str, Any]) -> dict[str, Any]:
        """Return a shallow delta between desired state and current cluster config."""
        current = self.cluster
        adds, updates, deletes = [], [], []

        desired_pools = {p["name"]: p for p in desired.get("node_pools", [])}
        current_pools = {p["name"]: p for p in current.get("node_pools", [])}

        _cmp_fields = {"name", "machine", "count"}

        def _norm(pool: dict) -> dict:
            return {k: v for k, v in pool.items() if k in _cmp_fields}

        for name, pool in desired_pools.items():
            if name not in current_pools:
                adds.append({"type": "node_pool", "name": name, "spec": pool})
            elif _norm(pool) != _norm(current_pools[name]):
                updates.append({"type": "node_pool", "name": name, "from": current_pools[name], "to": pool})

        for name in current_pools:
            if name not in desired_pools:
                deletes.append({"type": "node_pool", "name": name})

        return {"adds": adds, "updates": updates, "deletes": deletes}
