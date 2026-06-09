from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

_PF_PID_FILE = Path.home() / ".kube-mind" / "prometheus_pf.pid"
_NS = "monitoring"
_RELEASE = "prometheus"
_SVC = "prometheus-kube-prometheus-prometheus"
_PORT = 9090


class MonitoringError(Exception):
    pass


def _helm(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["helm", *args], capture_output=True, text=True, check=check)


def _kubectl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True, check=check)


def install_prometheus() -> bool:
    """Install kube-prometheus-stack via Helm. Returns True if installed, False if already present."""
    if subprocess.run(["which", "helm"], capture_output=True).returncode != 0:
        raise MonitoringError(
            "Helm not found — install it from https://helm.sh/docs/intro/install/ then re-run init."
        )

    _helm("repo", "add", "prometheus-community",
          "https://prometheus-community.github.io/helm-charts")
    _helm("repo", "update")

    already = _helm("status", _RELEASE, "-n", _NS, check=False)
    if already.returncode == 0:
        return False  # already installed, nothing to do

    result = _helm(
        "install", _RELEASE,
        "prometheus-community/kube-prometheus-stack",
        "--namespace", _NS,
        "--create-namespace",
        "--set", "grafana.enabled=false",
        "--set", "alertmanager.enabled=false",
        "--wait",
        "--timeout", "5m",
        check=False,
    )
    if result.returncode != 0:
        raise MonitoringError(f"Helm install failed:\n{result.stderr.strip()}")

    return True


def _pf_alive() -> bool:
    """Return True if the saved port-forward PID is still running."""
    if not _PF_PID_FILE.exists():
        return False
    try:
        pid = int(_PF_PID_FILE.read_text().strip())
        os.kill(pid, 0)  # signal 0 = existence check only
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


def start_port_forward() -> None:
    """Start a background kubectl port-forward and save its PID."""
    stop_port_forward()

    proc = subprocess.Popen(
        ["kubectl", "port-forward", f"svc/{_SVC}", f"{_PORT}:{_PORT}", "-n", _NS],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    _PF_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PF_PID_FILE.write_text(str(proc.pid))

    # Give it a moment to bind the port
    time.sleep(2)

    if proc.poll() is not None:
        _PF_PID_FILE.unlink(missing_ok=True)
        raise MonitoringError(
            f"Port-forward to {_SVC} failed to start — "
            "check that the monitoring namespace and service exist."
        )


def stop_port_forward() -> None:
    if not _PF_PID_FILE.exists():
        return
    try:
        pid = int(_PF_PID_FILE.read_text().strip())
        os.kill(pid, 9)
    except (ValueError, ProcessLookupError):
        pass
    _PF_PID_FILE.unlink(missing_ok=True)


def ensure_port_forward() -> None:
    """Start port-forward if not already running. Safe to call repeatedly."""
    if not _pf_alive():
        start_port_forward()
