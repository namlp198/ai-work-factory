# AI Work Factory

Nền tảng multi-agent **artifact-driven** cho vòng đời phần mềm: objective → requirement → architecture
→ task graph → code trong sandbox → test/review/QA → PR, với quality gate, human approval và trace đầy đủ.
Bản triển khai này bao phủ **Phase 0 và phần lõi Phase 1** của
[`AI_WORK_FACTORY_IMPLEMENTATION_PLAN.md`](AI_WORK_FACTORY_IMPLEMENTATION_PLAN.md) theo
[`AI_WORK_FACTORY_INITIAL_TECHNICAL_DESIGN.md`](AI_WORK_FACTORY_INITIAL_TECHNICAL_DESIGN.md).

```text
USER ─► Control API + Dashboard ─► Orchestrator ─► Workflow Engine (YAML)
                                     │  Planner · Task Router · Critic · Approvals
                                     ▼
                              Jobs (queue) ─► Workers (Windows / Linux / Jetson / GPU)
                                               └─ Agent Runtime ─► Model Router ─► Claude
                                               └─ Typed Tools ─► Policy Engine ─► sandbox git workspace
                                     ▼
                     Artifacts (versioned) · Traceability graph · Audit trail
```

## Bắt đầu nhanh

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\awf demo                  # dashboard http://127.0.0.1:8000, duyệt 2 gate trên UI
.\.venv\Scripts\awf demo --auto-approve   # chạy không giao diện tới COMPLETED
.\.venv\Scripts\python -m pytest -q -p no:logging   # 47 test: unit, integration, e2e
```

Demo chạy offline (model kịch bản). Dùng Claude thật: đặt `ANTHROPIC_API_KEY` trên worker và thêm `--live-model`.

## Tài liệu

| Tài liệu | Nội dung |
|---|---|
| [docs/HUONG_DAN_SU_DUNG.md](docs/HUONG_DAN_SU_DUNG.md) | **Hướng dẫn chi tiết**: cài đặt, đưa repo thật vào, worker, cấu hình, viết workflow/agent, API, bảo mật, sự cố |
| [docs/architecture/implementation-overview.md](docs/architecture/implementation-overview.md) | Ánh xạ thiết kế → code, vòng đời run, state machine, giới hạn |
| [docs/adr/](docs/adr/) | ADR-001 … ADR-007 |
| [docs/trials/internal-file-server.md](docs/trials/internal-file-server.md) | Báo cáo chạy thử trên repo .NET thật |

## Cấu trúc

```text
apps/control_api/awf_api/      FastAPI control API + dashboard tĩnh
packages/awf/
  domain/                      entities, enums, state machine, DB
  workflow_engine/             schema + graph YAML workflow
  agent_runtime/               contract/profile, runtime loop, adapters (Claude, scripted)
  model_router/  policy/       routing model, RBAC + action levels
  tool_sdk/                    fs, git, build/test/lint, shell (deny)
  worker_sdk/                  worker daemon, sandbox, executor, HTTP client
  orchestrator/                orchestrator, critic, task router, context, artifacts, audit, jobs
  cli.py                       lệnh `awf`
workers/linux_worker/          entry point worker
agents/{contracts,profiles,prompts}/   7 role: analyst, architect, planner, developer, test engineer, reviewer, QA
workflows/                     software-development (active), content/video (draft)
config/                        system, model-routing, policies
knowledge/                     coding standard, architecture guidelines, security policy, templates
infrastructure/                Alembic migrations, Dockerfile
examples/                      demo repo + kịch bản scripted, trial InternalFileServer
tests/                         unit, integration, e2e
```

## Chạy dạng dịch vụ

```powershell
.\.venv\Scripts\awf db upgrade
.\.venv\Scripts\awf api --embedded-orchestrator          # hoặc tách: awf api + awf orchestrator
.\.venv\Scripts\awf worker --url http://127.0.0.1:8000 --name win-worker-01
docker compose up -d --build                             # PostgreSQL/pgvector, Redis, MinIO, API, orchestrator, Linux worker
```
