# kube-mind

Natural language Kubernetes infrastructure CLI. Type plain English to provision, configure, and modify a GKE cluster — no `kubectl` commands or Terraform required.

```bash
kube-mind init --project my-project --zone us-east1-b --cluster kubeagent-prod
kube-mind "add a GPU node for ML inference"
kube-mind "scale flask-app to 4 replicas"
kube-mind status
kube-mind history
```

---

## How it works

You describe what you want. The agent checks safety, plans the minimum set of operations needed, and executes them.

```
Your intent (plain English)
        ↓
  NeMo Guardrails          ←  blocks dangerous or off-topic intents
        ↓ (if safe)
  LangGraph Agent
        ↓
  GPT-4o Planner  ←  reads ~/.kube-mind/state.json
        ↓
  Planned ops (gcloud / kubectl / verify)
        ↓
  NeMo Guardrails (op-level)  ←  blocks destructive ops (delete primary pool, resize to 0)
        ↓ (if safe)
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
| Safety | NeMo Guardrails 0.22 | Blocks dangerous and off-topic intents before planning |
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
├── guardrails/
│   ├── guard.py      # check_intent (NeMo) and check_ops (pure Python)
│   └── config/
│       ├── config.yml    # NeMo config — model and active rails
│       ├── prompts.yml   # Custom Kubernetes safety prompt for self_check_input
│       └── rails.co      # Colang — bot refusal message
└── tools/
    ├── gcloud_tools.py   # create/delete/resize node pools + live status via GKE API
    └── kubectl_tools.py  # scale, cordon, drain, patch via K8s API
```

---

## Setup

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
# Edit .env and set OPENAI_API_KEY=sk-...
```

### 4. Authenticate with GCP

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud auth application-default login
```

---

## CLI commands

| Command | What it does |
|---|---|
| `kube-mind init --project P --zone Z --cluster C` | Connect kube-mind to an existing GKE cluster, seed local state |
| `kube-mind "<intent>"` | Safety check → plan → confirm → execute |
| `kube-mind "<intent>" --dry-run` | Safety check → plan only, no changes |
| `kube-mind status` | Fetch live cluster state from GKE, sync state.json |
| `kube-mind history` | Show log of every past operation with timestamps and outcomes |
| `kube-mind diff "<intent>"` | Show adds/updates/deletes without executing |
| `kube-mind undo` | Roll back the last operation |
| `kube-mind monitor` | Poll Prometheus for anomalies (not yet built) |

---

## End-to-end test

This test covers the full lifecycle: cluster creation → state sync → guardrails → real GKE/kubectl ops → external deletion → state sync.

Replace `YOUR_PROJECT_ID` with your GCP project ID throughout.

### 1 — Create the cluster

```bash
gcloud services enable container.googleapis.com --project=YOUR_PROJECT_ID

gcloud container clusters create kubeagent-prod \
  --zone=us-east1-b \
  --num-nodes=3 \
  --machine-type=e2-medium \
  --disk-size=50 \
  --project=YOUR_PROJECT_ID
```

Takes ~4 minutes. Expected finish:
```
NAME            LOCATION    STATUS
kubeagent-prod  us-east1-b  RUNNING
```

### 2 — Connect kubectl

```bash
gcloud components install gke-gcloud-auth-plugin

gcloud container clusters get-credentials kubeagent-prod \
  --zone=us-east1-b \
  --project=YOUR_PROJECT_ID

kubectl get nodes   # 3 nodes, status Ready
```

### 3 — Seed local state

```bash
kube-mind init \
  --project=YOUR_PROJECT_ID \
  --zone=us-east1-b \
  --cluster=kubeagent-prod
```

Expected:
```
Initialized. Synced cluster kubeagent-prod to ~/.kube-mind/state.json

 Cluster: kubeagent-prod | us-east1-b  [RUNNING]
  Node Pool      Machine     Count   Status
  default-pool   e2-medium       3   RUNNING
```

### 4 — Test guardrails: dangerous intents are blocked

```bash
kube-mind "delete the cluster"
```
Expected — NeMo blocks before the planner is ever called:
```
→ delete the cluster
Checking safety...
Blocked: Blocked by safety guardrails: the request is either dangerous or unrelated to Kubernetes infrastructure.
```

```bash
kube-mind "write me a poem about Kubernetes"
```
Expected: same block message.

### 5 — Test guardrails: safe intents pass through

```bash
kube-mind "add a GPU node for ML inference" --dry-run
```
Expected — passes safety check, planner runs, no real changes:
```
→ add a GPU node for ML inference
Checking safety...
Planning...

  Planned Operations
  # │ Type   │ Action           │ Params
  1 │ gcloud │ create_node_pool │ name=gpu-pool, machine=n1-standard-4, gpu=T4, count=1
  2 │ verify │ check_nodes_ready│ pool=gpu-pool

--dry-run: no changes applied.
```

### 6 — Create a real node pool

```bash
kube-mind "add a small spot node pool with 1 node for batch jobs"
```

Expected — confirm prompt, then GKE API call:
```
→ add a small spot node pool with 1 node for batch jobs
Checking safety...
Planning...

  Planned Operations
  # │ Type   │ Action           │ Params
  1 │ gcloud │ create_node_pool │ name=batch-pool, machine=e2-small, count=1, preemptible=True

Apply these changes? [y/n] y
Executing...
Done.
```

Verify:
```bash
kube-mind status
# Shows both default-pool and batch-pool as RUNNING
```

### 7 — Test op-level guardrail

The op-level check runs after planning and catches destructive ops that slipped past the intent check. It is pure Python with no LLM call.

```bash
python3 - <<'EOF'
import sys; sys.path.insert(0, ".")
from kube_mind.guardrails.guard import check_ops, GuardrailsError

cases = [
    ([{"action": "delete_node_pool", "params": {"name": "default-pool"}}], True),
    ([{"action": "resize_node_pool",  "params": {"name": "batch-pool", "count": 0}}], True),
    ([{"action": "resize_node_pool",  "params": {"name": "batch-pool", "count": 2}}], False),
]
for ops, should_block in cases:
    try:
        check_ops(ops)
        print(f"{'FAIL' if should_block else 'PASS'}  allowed: {ops[0]['action']}({ops[0]['params']})")
    except GuardrailsError as e:
        print(f"{'PASS' if should_block else 'FAIL'}  blocked: {e}")
EOF
```

Expected:
```
PASS  blocked: Refusing to delete 'default-pool' — it is the cluster's primary node pool...
PASS  blocked: Refusing to resize 'batch-pool' to 0 nodes — that would evict all running workloads.
PASS  allowed: resize_node_pool({'name': 'batch-pool', 'count': 2})
```

### 8 — Deploy an app and scale it

```bash
kubectl create deployment flask-app --image=nginx --replicas=2
kubectl get deployments flask-app   # 2/2 READY

kube-mind "scale flask-app to 4 replicas"
```

Expected:
```
→ scale flask-app to 4 replicas
Checking safety...
Planning...

  Planned Operations
  # │ Type    │ Action           │ Params
  1 │ kubectl │ scale_deployment │ deployment=flask-app, replicas=4

Apply these changes? [y/n] y
Executing...
Done.
```

Verify:
```bash
kubectl get deployments flask-app   # 4/4 READY
```

### 9 — Check history

```bash
kube-mind history
```

Expected: table with both operations (node pool creation, deployment scale) with timestamps and `success` outcome.

### 10 — Delete the cluster externally

```bash
gcloud container clusters delete kubeagent-prod \
  --zone=us-east1-b \
  --project=YOUR_PROJECT_ID \
  --quiet
```

Takes ~2 minutes.

### 11 — Verify kube-mind detects the deletion

```bash
kube-mind status
```

Expected:
```
Fetching live cluster status from GKE...
Cluster 'kubeagent-prod' not found in GCP.
It was likely deleted outside kube-mind. Local state.json still shows the old info.
Clear local cluster state? [y/n] y
Local state cleared.
```

After clearing, `kube-mind status` shows "No cluster found in state" — ready for the next `kube-mind init`.

---

## Guardrails

kube-mind uses [NeMo Guardrails](https://github.com/NVIDIA/NeMo-Guardrails) 0.22 for two layers of safety:

### Layer 1 — Intent check (before planning)

Uses GPT-4o with a Kubernetes-specific safety prompt (`guardrails/config/prompts.yml`) to classify the user's intent. Blocked if the intent is:

- Destructive cluster-wide operations ("delete the cluster", "nuke everything", "wipe all nodes")
- Completely off-topic ("write me a poem", "what's the weather")

One extra GPT-4o API call per invocation. Zero LLM calls when blocked.

### Layer 2 — Op check (after planning, before execution)

Pure Python scan of the planned ops list. No LLM call. Blocked if:

- `delete_node_pool` targets `default-pool` or `default` (the primary pool)
- `resize_node_pool` sets count to 0 (would evict all workloads)

---

## Cluster lifecycle

kube-mind manages clusters, it does not create or destroy them. That boundary is intentional — cluster creation involves VPC settings, auth plugins, billing configuration, and add-ons that belong in `gcloud` or Terraform.

| Operation | Tool |
|---|---|
| Create cluster | `gcloud container clusters create` |
| Sync to kube-mind | `kube-mind init` |
| Day-to-day management | `kube-mind "<intent>"` |
| Delete cluster | `gcloud container clusters delete` |
| Detect deletion | `kube-mind status` (prompts to clear local state) |

---

## Stopping the cluster

Delete when not in use to avoid GCP charges (~$0.15/hr for 3x e2-medium):

```bash
gcloud container clusters delete kubeagent-prod \
  --zone=us-east1-b --project=YOUR_PROJECT_ID --quiet
```

All code and operation history are local — nothing is lost. Recreate the cluster and run `kube-mind init` again to resume.

---

## Build status

| Step | Feature | Status |
|---|---|---|
| 1 | GKE cluster | ✅ Done |
| 2 | Typer CLI skeleton | ✅ Done |
| 3 | State manager | ✅ Done |
| 4 | LangGraph + GPT-4o planner | ✅ Done |
| 5 | gcloud tool node (node pool ops) | ✅ Done |
| 6 | kubectl tool node (scale, cordon, drain) | ✅ Done |
| 7 | NeMo Guardrails safety layer | ✅ Done |
| 8 | Human confirm gate for risky ops | 🔜 Next |
| 9 | Prometheus diagnose | 🔜 Upcoming |
| 10 | Monitor daemon | 🔜 Upcoming |
