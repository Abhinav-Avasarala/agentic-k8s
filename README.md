# kube-mind

Natural language Kubernetes infrastructure CLI. Type plain English to provision, configure, monitor, and modify a GKE cluster — no `kubectl` commands or Terraform required.

```bash
kube-mind init --project my-project --zone us-east1-b --cluster kubeagent-prod
kube-mind "add a GPU node for ML inference"
kube-mind "scale flask-app to 4 replicas"
kube-mind "is everything alright"
kube-mind "are any pods crash-looping?"
kube-mind "how much CPU and memory is flask-app using?"
kube-mind diff "scale default-pool to 5 nodes"   # preview changes without applying
kube-mind undo                                     # roll back the last operation
kube-mind status
kube-mind history
```

---

## How it works

You describe what you want. The agent checks safety, plans the minimum set of operations needed, executes them, and verifies the result.

```
Your intent (plain English)
        ↓
  NeMo Guardrails (intent)  ←  blocks dangerous / off-topic intents before planning
        ↓ (if safe)
  GPT-4o Planner  ←  reads ~/.kube-mind/state.json
        ↓
  Planned ops (gcloud / kubectl / verify)
        ↓
  NeMo Guardrails (op-level)  ←  hard blocks: delete primary pool, resize to 0
        ↓ (if safe)
  Risk classifier  ←  tags each op: ⛔ CONFIRM_BY_NAME / ⚠ WARN / safe
        ↓
  Confirm gate  ←  skipped for read-only verify ops; y/n or name-typed for mutations
        ↓ (confirmed)
  gcloud Python SDK   →  GKE node pool operations
  K8s Python SDK      →  deployment / node operations
  Prometheus HTTP     →  cluster health queries (CPU, memory, pod health, crash-loops)
        ↓
  Verify steps run automatically after provisioning
        ↓
  state.json updated  +  health report printed
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
| K8s control | kubernetes Python SDK | Scale deployments, cordon/drain nodes, verify pod/node state |
| Monitoring | kube-prometheus-stack (Helm) | Prometheus + node-exporter + kube-state-metrics on the cluster |
| Diagnostics | Prometheus HTTP API + PromQL | CPU, memory, pod health, crash-loop, per-deployment resource queries |
| State | `~/.kube-mind/state.json` | Persists cluster state across CLI calls |
| Infra | GKE Standard (GCP) | The actual cluster |

---

## Project structure

```
kube_mind/
├── cli.py            # Typer CLI — entry point, all commands
├── state.py          # StateManager — reads/writes ~/.kube-mind/state.json
├── output.py         # Rich terminal output helpers + health report renderer
├── agent/
│   ├── graph.py      # LangGraph graph: planner → executor, collects verify results
│   └── prompts.py    # System prompt for GPT-4o including PromQL query templates
├── guardrails/
│   ├── guard.py      # check_intent (NeMo), check_ops, risk_check, RiskLevel
│   └── config/
│       ├── config.yml    # NeMo config — model and active rails
│       ├── prompts.yml   # Custom Kubernetes safety prompt for self_check_input
│       └── rails.co      # Colang — bot refusal message
└── tools/
    ├── gcloud_tools.py      # create/delete/resize node pools + live status via GKE API
    ├── kubectl_tools.py     # scale, cordon, drain, patch via K8s API
    ├── prometheus_tools.py  # verify ops: check_nodes_ready, check_deployment_ready,
    │                        #   check_pod_scheduled, run_prometheus_query
    └── monitoring_tools.py  # install_prometheus (Helm), port-forward lifecycle

eval/
├── models.py         # Plug-and-play model adapters: OpenAIAdapter, AnthropicAdapter
├── runner.py         # Runs adapters against test cases, parses responses, scores results
├── scoring.py        # Precision/recall/F1 with greedy matching, synonym resolution, wildcards
└── test_cases.json   # 19 labelled test cases across all op categories
```

---

## Setup

### 1. Prerequisites

- Python 3.10+ (3.11+ recommended)
- [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) installed and configured
- [Helm](https://helm.sh/docs/intro/install/) (`brew install helm` on macOS)
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
| `kube-mind init --project P --zone Z --cluster C` | Connect to a GKE cluster, seed state, install Prometheus |
| `kube-mind init ... --no-monitoring` | Same but skip Prometheus install |
| `kube-mind "<intent>"` | Safety check → plan → confirm → execute → verify |
| `kube-mind "<intent>" --dry-run` | Safety check → plan only, print ops table, no changes applied |
| `kube-mind diff "<intent>"` | Like `--dry-run` but shows a terraform-style state diff (adds / updates / deletes) instead of the raw ops list |
| `kube-mind undo` | Invert and execute the last mutable operation |
| `kube-mind status` | Fetch live cluster state from GKE, sync state.json |
| `kube-mind history` | Show log of every past operation with timestamps and outcomes |
| `kube-mind eval` | Run the agent evaluation harness against labelled test cases |
| `kube-mind monitor` | Poll Prometheus for anomalies (not yet implemented) |

### `diff` vs `--dry-run`

Both preview changes without touching the cluster. The difference is what they show:

```
kube-mind "scale default-pool to 5 nodes" --dry-run
```
```
  Planned Operations
  # │ Type   │ Action           │ Params
  1 │ gcloud │ resize_node_pool │ name=default-pool, count=5
```

```
kube-mind diff "scale default-pool to 5 nodes"
```
```
  Diff — what would change
  ~ update node_pool: default-pool  (count: 3 → 5)
  0 add(s), 1 update(s), 0 delete(s)
```

Use `--dry-run` to see what the agent decided to do. Use `diff` to see the net effect on cluster state.

### `undo`

`undo` inverts the last mutable operation and executes the inverse immediately. Before snapshot values (node count, replica count, pool spec) are captured at execution time and stored in history, so the inverse is always exact — not re-planned by the LLM.

```bash
kube-mind "scale default-pool to 5 nodes"   # count 3 → 5
kube-mind undo                               # count 5 → 3  (exact, from saved before state)
```

Invertible operations:

| Original op | Undo action |
|---|---|
| `create_node_pool` | `delete_node_pool` |
| `delete_node_pool` | `create_node_pool` (restores saved machine type + count) |
| `resize_node_pool` | `resize_node_pool` back to previous count |
| `scale_deployment` | `scale_deployment` back to previous replicas |
| `cordon_node` | `uncordon_node` |
| `drain_node` | `uncordon_node` (partial — pods already rescheduled) |
| `taint_node` | `untaint_node` |

Operations that cannot be auto-inverted (e.g. `patch_resources` without a saved before state) are listed as skipped — the undoable ops in the same plan still run.

---

## Agent evaluation

kube-mind ships an evaluation harness that measures the planner's output quality against a set of labelled test cases — without running against a live cluster.

```bash
# Evaluate the default model (gpt-4o) across all 19 test cases
kube-mind eval --model gpt-4o

# Compare two models side by side
kube-mind eval --model gpt-4o --model claude-sonnet-4-6

# Filter to a specific category and save raw results
kube-mind eval --model gpt-4o --tag resize --verbose --output results.json
```

### What it measures

Each test case is a `(cluster_state, intent, expected_ops)` triple. The harness calls the planner's system prompt and user message format directly — identical to what the LangGraph `planner_node` sends — and scores the response.

**Scoring** uses precision, recall, and F1 per case:
- **Precision** — fraction of generated ops that were expected (penalises hallucinated steps)
- **Recall** — fraction of expected ops that were generated (penalises missing steps)
- **F1 ≥ 0.8** = pass

Two ops match if their action is equal (or a known synonym) and all key params match. Matching is flexible by design:
- `check_pod_scheduled` and `check_deployment_ready` are treated as equivalent post-scale verify steps
- `null` in an expected param acts as a wildcard — accepts any generated value (used when the model names things non-deterministically, e.g. GPU pool names)
- `size` and `count` are normalised to the same param; whole-number floats (`5.0`) match integer values (`5`)

### Test case categories

| Category | Cases | What it tests |
|---|---|---|
| Resize | `resize_up`, `resize_down`, `resize_add_more`, `resize_noop` | Absolute and relative node count changes; no-op detection |
| Create pool | `create_pool_basic`, `create_pool_gpu`, `create_pool_gpu_isolated` | Standard pool; GPU defaults; GPU + taint for workload isolation |
| Delete pool | `delete_pool` | Non-default pool removal |
| Deployments | `scale_deployment_up`, `scale_deployment_down` | Replica scaling with verify |
| Node ops | `cordon_node`, `drain_node`, `taint_node` | Single-node maintenance operations |
| Health checks | `health_check_alright`, `health_check_slow`, `health_check_wrong` | Vague intent → exactly three Prometheus queries |
| Specific queries | `crash_loop_check`, `busiest_node`, `pod_restarts` | Targeted single-metric queries |

### Sample output

```
                         Eval — gpt-4o  (19 cases)
╭──────────────────────────────┬──────────────────┬──────┬──────┬──────┬──────╮
│ Case                         │ Tags             │   F1 │    P │    R │   ms │
├──────────────────────────────┼──────────────────┼──────┼──────┼──────┼──────┤
│ ✓ resize_up                  │ resize, gcloud   │ 1.00 │ 1.00 │ 1.00 │ 1243 │
│ ✓ drain_node                 │ kubectl, node    │ 1.00 │ 1.00 │ 1.00 │ 1821 │
│ ✓ health_check_alright       │ verify, health   │ 1.00 │ 1.00 │ 1.00 │ 1654 │
│ ✗ resize_noop                │ resize, noop     │ 0.00 │ 0.00 │ 1.00 │ 1102 │
│  ...                         │                  │      │      │      │      │
╰──────────────────────────────┴──────────────────┴──────┴──────┴──────┴──────╯

  17/19 passed (89%)  avg F1: 0.90  avg latency: 1812ms  est. cost: ~$0.091
```

### Known model limitations surfaced by eval

| Case | Issue |
|---|---|
| `resize_noop` | gpt-4o generates a resize op even when the pool is already at the requested count — it does not detect the no-op |

---

## Reflection / self-critique loop *(next)*

Currently the planner makes a single LLM call and the result goes straight to the confirm gate. The next step introduces a second LLM call that reviews the plan before the user ever sees it.

```
Your intent
      ↓
  GPT-4o Planner  →  draft ops
      ↓
  Critic LLM  ←  "Is this plan correct, minimal, and safe given the cluster state?"
      ↓
  approved → confirm gate → execute
  rejected → back to planner with critique (up to N retries)
```

The critic checks for:
- **Correctness** — do the ops actually close the gap between current and desired state?
- **Minimality** — are there redundant steps (e.g. resizing a pool that's already the right size)?
- **Safety** — would any op cause unintended downtime given the current workloads?

This turns the agent from a single-shot planner into a **reasoning loop** — the defining characteristic of agentic systems. It also directly addresses the `resize_noop` failure surfaced by the eval harness, where the planner generates ops for an already-satisfied state.

---

## Diagnostic queries

kube-mind understands plain English health and diagnostic questions. These run read-only Prometheus queries — no confirmation prompt, no cluster changes.

| What you type | What it checks |
|---|---|
| `"is everything alright"` | Cluster-wide CPU %, memory %, pod phase counts |
| `"what's wrong"` / `"something feels slow"` | Same three health metrics |
| `"are any pods crash-looping?"` | Count of pods in CrashLoopBackOff across all non-system namespaces |
| `"how much CPU and memory is flask-app using?"` | Summed CPU (millicores) and memory (MB) for a specific deployment |
| `"which node is under the most load?"` | Per-node CPU % — surfaces the busiest node |
| `"any pod restarts in the last hour?"` | Total restart events across non-system pods |

Example output for `kube-mind "is everything alright"`:

```
  Cluster Health

  Metric          Value         Status
  ──────────────────────────────────────────
  CPU usage       10.2%         ✓  OK
  Memory usage    27.2%         ✓  OK
  Pod health      default   4 Running   ✓  OK
                  monitoring  7 Running
```

Thresholds: CPU warns at 70%, critical at 85%. Memory warns at 80%, critical at 90%. Any crash-looping or failed pods → CRITICAL.

---

## Monitoring setup

`kube-mind init` installs Prometheus automatically using Helm (`kube-prometheus-stack`). This gives you:

- **Prometheus** — scrapes and stores cluster metrics every 15 seconds
- **node-exporter** — per-node CPU, memory, disk, network metrics (runs on every node as a DaemonSet)
- **kube-state-metrics** — Kubernetes object metrics (pod phases, deployment health, restart counts)

A background `kubectl port-forward` is started automatically and its PID saved to `~/.kube-mind/prometheus_pf.pid`. kube-mind restarts it if it dies between CLI calls.

Prometheus runs inside the cluster — it is destroyed automatically when you delete the cluster. When you recreate the cluster and run `kube-mind init` again, it is reinstalled fresh.

```bash
# Skip monitoring install (e.g. already installed, or want to set it up manually)
kube-mind init --project P --zone Z --cluster C --no-monitoring

# Kill the local port-forward manually if needed
kill $(cat ~/.kube-mind/prometheus_pf.pid) 2>/dev/null; true

# Point kube-mind at an external Prometheus instance
echo "PROMETHEUS_URL=http://your-prometheus:9090" >> .env
```

---

## Guardrails

kube-mind has three layers of safety between your intent and the cluster.

### Layer 1 — Intent check (before planning)

Uses [NeMo Guardrails](https://github.com/NVIDIA/NeMo-Guardrails) 0.22 with a Kubernetes-specific safety prompt (`guardrails/config/prompts.yml`) to classify the user's intent before the planner is called. Blocked if the intent is:

- Destructive cluster-wide operations ("delete the cluster", "nuke everything", "wipe all nodes")
- Completely off-topic ("write me a poem", "what's the weather")

Allowed through: operational intents (add/resize/delete named pools, scale deployments, cordon/drain nodes) and diagnostic intents (health checks, performance questions, crash-loop queries).

Makes one GPT-4o API call per invocation. Zero LLM calls when blocked — fast fail.

### Layer 2 — Op check (after planning, before execution)

Pure Python scan of the planned ops list. No LLM call. Hard blocks:

- `delete_node_pool` targeting `default-pool` or `default` (the primary pool)
- `resize_node_pool` setting count to 0 (would evict all workloads)

### Layer 3 — Confirm gate (after planning, before execution)

Pure Python risk classifier (`risk_check`) tags each planned op:

| Tag | Ops | Confirmation required |
|---|---|---|
| `⛔ CONFIRM_BY_NAME` | `delete_node_pool`, `drain_node` | Must type the exact resource name |
| `⚠  WARN` | `cordon_node` | Yellow warning shown, plain y/n |
| safe | Everything else | Plain y/n |
| skipped | All-verify plans (health checks) | No prompt — queries are read-only |

The distinction from Layer 2: guardrails hard-block ops that should **never** run. The confirm gate allows ops to proceed but forces explicit acknowledgment of the specific risk — similar to `terraform destroy` requiring you to type the workspace name.

---

## Verify steps

After every provisioning or scaling operation the planner automatically appends a verify step. These run using the Kubernetes API (no Prometheus needed) and print a result before marking the operation complete.

| Verify action | What it checks |
|---|---|
| `check_nodes_ready` | All nodes in a named pool have `Ready=True` |
| `check_deployment_ready` | `ready_replicas == spec.replicas` for a deployment |
| `check_pod_scheduled` | All pods for a deployment have been assigned to a node |
| `prometheus_query` | Runs any PromQL query and returns the result as a formatted table |

If a verify step fails the operation is recorded as `"failed"` in history and the error is printed in red — the cluster change already happened but the expected post-condition was not met.

---

## Cluster lifecycle

kube-mind manages clusters, it does not create or destroy them. That boundary is intentional — cluster creation involves VPC settings, auth plugins, billing configuration, and add-ons that belong in `gcloud` or Terraform.

| Operation | Tool |
|---|---|
| Create cluster | `gcloud container clusters create` |
| Sync to kube-mind + install monitoring | `kube-mind init` |
| Day-to-day management | `kube-mind "<intent>"` |
| Health diagnostics | `kube-mind "is everything alright"` |
| Delete cluster | `gcloud container clusters delete` |
| Detect deletion | `kube-mind status` (prompts to clear local state) |

---

## End-to-end test

Full lifecycle: cluster creation → Prometheus install → guardrails → real GKE/kubectl ops → verify steps → Prometheus diagnostics → external deletion → state sync.

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

### 3 — Init kube-mind (installs Prometheus automatically)

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

Installing Prometheus monitoring stack (this takes ~2 min)...
Prometheus installed.
Starting port-forward to Prometheus...
Prometheus ready at http://localhost:9090
```

Verify Prometheus is up:
```bash
curl -s http://localhost:9090/-/ready
# Prometheus Server is Ready.
```

### 4 — Test guardrails: dangerous intents blocked

```bash
kube-mind "delete the cluster"
# → Blocked: Blocked by safety guardrails...

kube-mind "write me a poem about Kubernetes"
# → Blocked: Blocked by safety guardrails...
```

### 5 — Test guardrails: safe intents pass through

```bash
kube-mind "add a GPU node for ML inference" --dry-run
```

Expected — passes safety check, planner runs, verify step shown, no real changes:
```
  Planned Operations
  # │ Type   │ Action           │ Params
  1 │ gcloud │ create_node_pool │ name=gpu-pool, machine=n1-standard-4, gpu=T4, count=1
  2 │ verify │ check_nodes_ready│ pool=gpu-pool

--dry-run: no changes applied.
```

### 6 — Create a real node pool (tests verify step)

```bash
kube-mind "add a small spot node pool with 1 node for batch jobs"
# confirm: y
```

Expected — GKE API call runs, then verify step checks the pool is ready:
```
Executing...
  ✓  Pool 'batch-pool': 1/1 nodes ready
Done.
```

```bash
kube-mind status
# Shows default-pool (3 nodes) + batch-pool (1 node)
```

### 7 — Deploy an app and scale it (tests deployment verify step)

```bash
kubectl create deployment flask-app --image=nginx --replicas=2
kubectl rollout status deployment/flask-app

kube-mind "scale flask-app to 4 replicas"
# confirm: y
```

Expected:
```
Executing...
  ✓  Deployment 'flask-app': 4/4 replicas ready
Done.
```

### 8 — Health check via Prometheus

```bash
kube-mind "is everything alright"
```

Expected — no confirmation prompt (read-only), prints health report:
```
  Cluster Health

  Metric          Value         Status
  ──────────────────────────────────────────
  CPU usage       10.2%         ✓  OK
  Memory usage    27.2%         ✓  OK
  Pod health      default   4 Running   ✓  OK
                  monitoring  7 Running
```

### 9 — Specific diagnostic queries

```bash
kube-mind "are any pods crash-looping?"
# → Crash-looping pods   0   ✓  OK

kube-mind "how much CPU and memory is flask-app using?"
# → flask-app CPU      12m     ✓  OK
# → flask-app memory   8.3 MB  ✓  OK
```

### 10 — Test confirm gate: CONFIRM_BY_NAME path

```bash
kube-mind "remove the batch-pool node pool"
# Plan shows ⛔ HIGH risk, prompts for name
# Type wrong-name → aborted safely (no GKE call made)
# Re-run, type batch-pool → y → deleted
```

### 11 — Test op-level guardrail

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

### 12 — Test `diff`

```bash
# Preview a resize without applying it
kube-mind diff "scale default-pool to 5 nodes"
```

Expected — shows net state change, nothing applied:
```
  Diff — what would change
  ~ update node_pool: default-pool  (count: 3 → 5)
  0 add(s), 1 update(s), 0 delete(s)

Run without 'diff' to apply.
```

```bash
kube-mind status   # count should still be 3
```

### 13 — Test `undo`

```bash
# Apply a resize
kube-mind "scale default-pool to 5 nodes"
# confirm: y

kube-mind status   # count: 5

# Undo it — restores exact previous count from saved before state
kube-mind undo
# shows plan: resize_node_pool name=default-pool count=3, confirm: y

kube-mind status   # count: 3 again
```

### 14 — Check history

```bash
kube-mind history
```

Expected: table with all operations — node pool create, deployment scale, node pool delete, diff previews do NOT appear, undo entries labeled `undo: scale default-pool to 5 nodes`.

### 15 — Shut down

```bash
gcloud container clusters delete kubeagent-prod \
  --zone=us-east1-b \
  --project=YOUR_PROJECT_ID \
  --quiet

# kube-mind detects the deletion
kube-mind status
# → "Cluster 'kubeagent-prod' not found in GCP."
# → Clear local cluster state? [y/n] y

# Kill the local port-forward (Prometheus pods are already gone with the cluster)
kill $(cat ~/.kube-mind/prometheus_pf.pid) 2>/dev/null; true
```

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
| 8 | Human confirm gate for risky ops | ✅ Done |
| 9 | Prometheus diagnose + verify steps | ✅ Done |
| — | `diff` command (terraform-style state preview) | ✅ Done |
| — | `undo` command (exact inverse from saved before state) | ✅ Done |
| — | Agent evaluation harness (19 cases, plug-and-play models) | ✅ Done |
| 10 | Reflection / self-critique loop | 🔜 Next |
| 11 | Monitor daemon | ⏳ Planned |
