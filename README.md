# kube-mind

Natural language Kubernetes infrastructure CLI. Type plain English to provision, configure, and modify a GKE cluster — no `kubectl` commands or Terraform required.

```bash
kube-mind "add a GPU node for ML inference"
kube-mind "scale down for the weekend, keep it cheap"
kube-mind diff "migrate to a 5-node cluster"
kube-mind status
```

---

## How it works

You describe what you want. The agent figures out what currently exists, what needs to change, and executes the minimum set of operations to close that gap.

```
Your intent (plain English)
        ↓
  LangGraph Agent
        ↓
  GPT-4o Planner  ←  reads ~/.kube-mind/state.json
        ↓
  Planned ops (gcloud / kubectl / verify)
        ↓
  Apply? [y/n]
        ↓
  gcloud Python SDK  →  GKE node pool operations
  K8s Python SDK     →  deployment / node operations
        ↓
  state.json updated
```

---

## Tech stack

| Layer | Tool | Purpose |
|---|---|---|
| CLI | Typer + Rich | Commands, terminal output |
| Agent loop | LangGraph 1.x | Stateful planner → executor graph |
| LLM | GPT-4o (OpenAI) | Translates vague intent to typed ops |
| GKE control | google-cloud-container SDK | Create/delete/resize node pools |
| K8s control | kubernetes Python SDK | Scale deployments, cordon/drain nodes |
| State | `~/.kube-mind/state.json` | Persists cluster state across CLI calls |
| Infra | GKE Standard (GCP) | The actual cluster |

---

## Project structure

```
kube_mind/
├── cli.py            # Typer CLI — entry point, all commands
├── state.py          # StateManager — reads/writes ~/.kube-mind/state.json
├── output.py         # Rich terminal output helpers
├── agent/
│   ├── graph.py      # LangGraph graph: planner → executor
│   └── prompts.py    # System prompt for GPT-4o
└── tools/
    ├── gcloud_tools.py   # create/delete/resize node pools via GKE API
    └── kubectl_tools.py  # scale, cordon, drain, patch via K8s API
```

---

## Setup from scratch

### 1. Prerequisites

- Python 3.10+
- [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) installed and configured
- A GCP project with billing enabled
- An OpenAI API key

### 2. Clone and install

```bash
git clone <repo-url>
cd agentic-k8s

python3 -m venv .venv
source .venv/bin/activate

pip install -e .
```

### 3. Set your API key

```bash
cp .env.example .env
# Edit .env and add your OpenAI key
export OPENAI_API_KEY=sk-...
```

### 4. Authenticate with GCP

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud auth application-default login   # needed by the Python SDK
```

### 5. Create the GKE cluster

```bash
gcloud services enable container.googleapis.com --project=YOUR_PROJECT_ID

gcloud container clusters create kubeagent-prod \
  --zone=us-east1-b \
  --num-nodes=3 \
  --machine-type=e2-medium \
  --disk-size=50 \
  --enable-managed-prometheus \
  --project=YOUR_PROJECT_ID
```

This takes ~4 minutes.

### 6. Connect kubectl

```bash
gcloud components install gke-gcloud-auth-plugin

gcloud container clusters get-credentials kubeagent-prod \
  --zone=us-east1-b --project=YOUR_PROJECT_ID

kubectl get nodes   # should show 3 Ready nodes
```

### 7. Seed state.json

kube-mind reads `~/.kube-mind/state.json` to understand what currently exists. Seed it with your real cluster info:

```bash
mkdir -p ~/.kube-mind
cat > ~/.kube-mind/state.json << EOF
{
  "cluster": {
    "name": "kubeagent-prod",
    "zone": "us-east1-b",
    "project": "YOUR_PROJECT_ID",
    "node_pools": [
      { "name": "default-pool", "machine": "e2-medium", "count": 3 }
    ]
  },
  "workloads": [],
  "history": []
}
EOF
```

---

## CLI commands

| Command | What it does |
|---|---|
| `kube-mind "<intent>"` | Plan + execute: agent reads intent, shows plan, asks y/n, executes |
| `kube-mind "<intent>" --dry-run` | Plan only — shows what would happen, no changes made |
| `kube-mind diff "<intent>"` | Shows adds/updates/deletes without executing |
| `kube-mind status` | Print current cluster state from state.json |
| `kube-mind history` | Show log of every past operation |
| `kube-mind undo` | Roll back the last operation |
| `kube-mind monitor` | Poll Prometheus for anomalies (Step 10, not yet built) |

---

## Testing the full flow

### Test 1 — dry run (zero real changes)

```bash
kube-mind "add a GPU node for ML inference" --dry-run
```

Expected: prints a plan with `create_node_pool`, `taint_node`, `check_nodes_ready`. Nothing executes.

### Test 2 — add a node pool (real GKE call)

```bash
kube-mind "add a small spot node pool with 1 node for batch jobs"
```

Expected: shows plan → `Apply? [y/n]` → on `y`, calls GKE API, waits for pool to reach RUNNING → `Done.`

Verify it worked:

```bash
python3 - <<'EOF'
from google.cloud import container_v1
client = container_v1.ClusterManagerClient()
resp = client.list_node_pools(parent="projects/YOUR_PROJECT_ID/locations/us-east1-b/clusters/kubeagent-prod")
for p in resp.node_pools:
    print(f"  {p.name}  status={p.status}  count={p.initial_node_count}")
EOF
```

### Test 3 — scale a deployment (real kubectl call)

```bash
kubectl create deployment flask-app --image=nginx --replicas=2
kube-mind "scale flask-app to 3 replicas"
kubectl get deployments   # should show 3/3 READY
```

### Test 4 — check history

```bash
kube-mind history
```

Expected: table showing every operation kube-mind has executed with timestamp and outcome.

---

## Stopping the cluster

Delete the cluster when not in use to avoid GCP charges (~$0.15/hr for 3x e2-medium):

```bash
gcloud container clusters delete kubeagent-prod \
  --zone=us-east1-b --project=YOUR_PROJECT_ID --quiet
```

Recreate it with the command in Step 5. All code and state history are local — nothing is lost.

---

## Build status

| Step | Feature | Status |
|---|---|---|
| 1 | GKE cluster | ✅ Done |
| 2 | Typer CLI skeleton | ✅ Done |
| 3 | State manager | ✅ Done |
| 4 | LangGraph + GPT-4o planner | ✅ Done |
| 5 | gcloud tool node (node pool ops) | ✅ Done |
| 6 | kubectl tool nodes (scale, cordon, drain) | ✅ Done |
| 7 | NeMo Guardrails safety layer | 🔜 Next |
| 8 | Human confirm gate for risky ops | 🔜 Upcoming |
| 9 | Prometheus diagnose | 🔜 Upcoming |
| 10 | Monitor daemon | 🔜 Upcoming |
