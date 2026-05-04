## InfraAdvisor AI — Agent Context

Global infrastructure consulting firm AI assistant. Refer to `@docs/agent-guides/project-map.md` for architectural overview.

---

### Build & Verify Commands

- **Infrastructure:** `make deploy-infra` (Bicep IaC)
- **K8s Auth:** `make create-ghcr-secret` (Run before `deploy-k8s`)
- **Deployment:** `make deploy-k8s` (All manifests)
- **Testing:** `uv run pytest -x services/<service>/tests/`
- **Monitoring:** \* `kubectl get pods -n infra-advisor`
  - `kubectl logs -n infra-advisor deploy/<n> --tail=50`
- **Access:** `az aks get-credentials --resource-group rg-tola-infra-advisor-ai --name aks-infra-advisor`

---

### Workflow Orchestration (Senior Standards)

1.  **Plan Mode Default:** Enter plan mode for ANY task with 3+ steps or architectural decisions. Write detailed specs upfront. If logic goes sideways, **STOP** and re-plan immediately.
2.  **Subagent Strategy:** Offload research, parallel analysis, and exploration to subagents to keep the main context window clean. One specific task per subagent.
3.  **Verification Before Done:** Never mark a task complete without proving it works. Run tests, check logs, and diff behavior. Ask: _"Would a staff engineer approve this?"_
4.  **Demand Elegance:** For non-trivial changes, pause and seek the most elegant solution. If a fix feels hacky, refactor based on current knowledge rather than over-engineering.
5.  **Autonomous Bug Fixing:** When given a bug report, resolve it autonomously using logs and failing tests. Aim for zero context switching for the user.

---

### Task Management & Self-Improvement

1.  **Plan First:** Write actionable items to `tasks/todo.md` and verify with the user before implementation.
2.  **Track & Explain:** Mark items complete as you go and provide a high-level summary at each step.
3.  **Document Results:** Add a review section to `tasks/todo.md` upon completion.
4.  **Self-Improvement Loop:** After ANY user correction, update `tasks/lessons.md` with the pattern. Ruthlessly iterate on these lessons to prevent repeat mistakes.

---

### Key Constraints

- **Runtime:** All Python services use `uv`, **Python 3.12**, and `pyproject.toml`.
- **Security:** Never hardcode secrets. Use `os.environ["VAR_NAME"]` and fail fast.
- **Schema:** Do not modify NBI field names; use exact names from PRD Section 3.
- **Orchestration:** \* Namespace: `infra-advisor` (Exceptions: `kafka`, `airflow`, `datadog`).
  - Manifests: Must include `imagePullSecrets: [{name: ghcr-pull-secret}]`.
  - Registry: `ghcr.io/kyletaylored/infra-advisor-ai/<service>:latest`.

---

### Execution Phase

Implement phases sequentially. Check `@specs/` for the current phase task list.  
**Current Progress:** Refer to `@claude-progress.txt`.

> **Core Principles:** > \* **Simplicity First:** Impact minimal code.
>
> - **No Laziness:** Find root causes. No temporary fixes.
> - **Minimal Impact:** Only touch what is necessary. Avoid introducing regressions.
