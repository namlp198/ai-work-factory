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

Mọi máy (Windows, Linux, Jetson) dùng cùng source và cùng lệnh; chỉ `.env` khác nhau theo máy.

```powershell
git clone <repo-url> ai-work-factory; cd ai-work-factory
.\scripts\bootstrap.ps1        # Linux/macOS: scripts/bootstrap.sh  (Python 3.12 + requirements.lock + .env + DB + doctor)
.\.venv\Scripts\awf start      # dashboard http://127.0.0.1:8000
```

- Demo offline (không cần API key): `awf demo` (duyệt gate trên UI) hoặc `awf demo --auto-approve`.
- Nhiều máy: `bootstrap -Role control-plane -PublicUrl <url>` trên máy trung tâm (in sẵn lệnh cho worker),
  `bootstrap -Role worker -ControlUrl ... -RegistrationToken ...` trên mỗi worker, rồi `awf start` ở mọi máy.
- Sau `git pull`: chạy lại bootstrap rồi `awf start`.
- Dashboard: đăng nhập bằng `AWF_API_TOKEN` một lần (giữ 30 ngày), hoặc `awf open` để vào thẳng.
- Máy trung tâm + worker ở nhiều địa điểm qua internet (Tailscale hoặc HTTPS/Caddy): xem §21 của hướng dẫn.
- Kiểm tra máy: `awf doctor`. Sao lưu: `awf db backup`. Test: `python -m pytest -q -p no:logging` (131 test).
- Claude thật: đặt `ANTHROPIC_API_KEY` trong `.env` của máy worker.

## Tài liệu

| Tài liệu | Nội dung |
|---|---|
| [docs/HUONG_DAN_SU_DUNG.md](docs/HUONG_DAN_SU_DUNG.md) | **Hướng dẫn chi tiết**: cài đặt, đưa repo thật vào, worker, nhiều máy (§21), cấu hình, viết workflow/agent, API, bảo mật, sự cố |
| [docs/architecture/implementation-overview.md](docs/architecture/implementation-overview.md) | Ánh xạ thiết kế → code, vòng đời run, state machine, giới hạn |
| [docs/adr/](docs/adr/) | ADR-001 … ADR-008 |
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
