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
- **Lần đầu với máy trung tâm + worker: làm theo checklist từng thao tác ở §3b của hướng dẫn.**
- Nhiều máy: `bootstrap -Role control-plane -PublicUrl <url>` trên máy trung tâm (in sẵn lệnh cho worker),
  `bootstrap -Role worker -ControlUrl ... -RegistrationToken ...` trên mỗi worker, rồi `awf start` ở mọi máy.
- Sau `git pull`: chạy lại bootstrap rồi `awf start`.
- Dashboard có hai ngôn ngữ (English / Tiếng Việt), chọn trên thanh tiêu đề và được ghi nhớ. Mọi thay đổi giao
  diện phải có đủ cả hai ngôn ngữ (`static/i18n.js`, quy tắc trong `CLAUDE.md`).
- Dashboard: đăng nhập bằng `AWF_API_TOKEN` một lần (giữ 30 ngày), hoặc `awf open` để vào thẳng. Cài lên điện
  thoại như một ứng dụng (PWA, cần HTTPS, ví dụ `tailscale serve`): §7 của hướng dẫn.
- Máy trung tâm + worker ở nhiều địa điểm, xem dashboard từ điện thoại: Tailscale từng bước ở §21 của hướng dẫn
  ("Cách A"). Tự chạy khi bật máy: `sudo scripts/install-systemd.sh` (Ubuntu/Linux/Jetson),
  `.\scripts\install-task.ps1` (Windows, khi đăng nhập) hoặc `.\scripts\install-task.ps1 -AtStartup` (Windows 24/7,
  không cần đăng nhập; PowerShell admin).
- Câu hỏi thường gặp (khác gì Claude Code local, repo local hay GitHub, project/run/task, API key, cập nhật
  máy): §22 của hướng dẫn.
- Kiểm tra máy: `awf doctor`. Sao lưu: `awf db backup`. Test: `python -m pytest -q -p no:logging` (292 test, thêm 8 test PostgreSQL khi có AWF_TEST_DATABASE_URL).
- Model thật: Claude, Gemini, ChatGPT, Qwen, Kimi, DeepSeek, MiniMax. Đặt API key của hãng trong `.env` của máy
  worker (bảng tổng hợp và cách lấy key ở §6 của hướng dẫn). Key dùng cho mọi agent, không riêng coding. Developer chạy bằng engine viết code
  (Claude Code, Codex, Antigravity cho Gemini, Qwen Code, Kimi Code, DeepSeek Harness, MiniMax Code): chọn
  *Coding engine* cho run hoặc project (§6, ADR-011, ADR-012, ADR-013).
- Chỉ có gói Claude Pro/Max hoặc Google AI Pro, không có API key: đăng nhập Claude Code hoặc Antigravity trên
  worker, chọn *Engine dùng cho = Mọi role*. Cả run chạy bằng gói (§6 "Chạy cả run bằng gói Pro", ADR-014).

## Tài liệu

| Tài liệu | Nội dung |
|---|---|
| [docs/HUONG_DAN_SU_DUNG.md](docs/HUONG_DAN_SU_DUNG.md) | **Hướng dẫn chi tiết**: cài đặt, đưa repo thật vào, worker, nhiều máy (§21), cấu hình, viết workflow/agent, API, bảo mật, sự cố |
| [docs/architecture/implementation-overview.md](docs/architecture/implementation-overview.md) | Ánh xạ thiết kế → code, vòng đời run, state machine, giới hạn |
| [docs/adr/](docs/adr/) | ADR-001 … ADR-017 |
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
agents/{contracts,profiles,prompts}/   7 role phần mềm + 6 role content (researcher, strategist, copywriter, reviewer, editor, SEO)
workflows/                     software-development, content-production (active), video (draft)
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
