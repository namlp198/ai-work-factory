# AI WORK FACTORY — HƯỚNG DẪN SỬ DỤNG CHI TIẾT

**Phiên bản:** 0.1 (Phase 0 → đầu Phase 1)
**Đối tượng:** Admin / Product Owner, developer tích hợp repo, người viết workflow và agent.

Mục lục

1. [Factory làm gì](#1-factory-làm-gì)
2. [Khái niệm cốt lõi](#2-khái-niệm-cốt-lõi)
3. [Cài đặt](#3-cài-đặt)
4. [Chạy demo trong 1 phút](#4-chạy-demo-trong-1-phút)
5. [Đưa một repo thật vào factory (từng bước)](#5-đưa-một-repo-thật-vào-factory-từng-bước)
6. [Bật Claude (model thật)](#6-bật-claude-model-thật)
7. [Dashboard](#7-dashboard)
8. [Duyệt (human approval) và fix loop](#8-duyệt-human-approval-và-fix-loop)
9. [Lấy kết quả: nhánh git, patch, PR](#9-lấy-kết-quả-nhánh-git-patch-pr)
10. [Worker: Windows, Linux, Jetson, GPU](#10-worker-windows-linux-jetson-gpu)
11. [Cấu hình](#11-cấu-hình)
12. [Viết workflow mới](#12-viết-workflow-mới)
13. [Viết agent (contract + profile + prompt)](#13-viết-agent-contract--profile--prompt)
14. [Traceability, impact analysis, audit](#14-traceability-impact-analysis-audit)
15. [Bảo mật](#15-bảo-mật)
16. [Triển khai bằng Docker Compose + PostgreSQL](#16-triển-khai-bằng-docker-compose--postgresql)
17. [API tham khảo](#17-api-tham-khảo)
18. [Kiểm thử nền tảng](#18-kiểm-thử-nền-tảng)
19. [Xử lý sự cố](#19-xử-lý-sự-cố)
20. [Giới hạn hiện tại và lộ trình](#20-giới-hạn-hiện-tại-và-lộ-trình)

---

## 1. Factory làm gì

Bạn đưa vào một **objective** cho một **project** (một repository). Factory chạy workflow
`software-development`:

```text
Requirement → Architecture → [DUYỆT] → Planning → Development (sandbox riêng) → Verification
(test engineer + static analysis) → Code review → QA → Prepare PR → [DUYỆT merge]
          ↑______________ fix loop khi review / QA / verification yêu cầu sửa ______________|
```

Mỗi bước do một **agent có contract** thực hiện, sinh ra **artifact có version**, được **Critic** kiểm
bằng luật + bằng chứng thật (build/test do worker chạy), và mọi hành động được ghi **audit**.
Bạn chỉ can thiệp ở các gate: duyệt kiến trúc và duyệt merge.

Kết quả cuối: nhánh `awf/run-<id>` trên repo (base branch không bao giờ bị ghi), file patch,
mô tả pull request, toàn bộ tài liệu (requirement, architecture, ADR, test/review/QA report) và trace.

## 2. Khái niệm cốt lõi

| Khái niệm | Ý nghĩa |
|---|---|
| **Project** | Một repository + cấu hình lệnh build/test/lint, độ bảo mật, ngân sách, nhãn platform. |
| **Workflow** | File YAML trong `workflows/` mô tả các stage và phụ thuộc. |
| **Workflow run** | Một lần chạy workflow với một objective. Có nhánh integration `awf/run-<8 ký tự id>`. |
| **Stage / Task** | Stage sinh ra task (1 task, nhiều task song song, hoặc 1 task/planned task). |
| **Agent** | Role profile + contract + tools + permissions + context + model route. |
| **Job** | Đơn vị thực thi giao cho worker (agent job hoặc tool job). |
| **Artifact** | Đầu ra có version + checksum + provenance (requirement, architecture, diff, report...). |
| **Critic** | Kiểm tra output theo contract và evidence: `PASS` / `CHANGES_REQUIRED` / `BLOCK`. |
| **Verdict** | Kết luận của reviewer/QA/tool: `APPROVE` / `CHANGES_REQUIRED` — kích hoạt fix loop. |
| **Gate** | Điểm dừng chờ con người duyệt. |
| **Evidence** | Kết quả build/test/lint do worker ghi lại — agent không thể "khai" là đã pass. |

Trạng thái task: `NEW → READY → WAITING → RUNNING → REVIEW → DONE` (+ `APPROVAL_REQUIRED`,
`CHANGES_REQUIRED`, `FAILED`, `CANCELLED`).

## 3. Cài đặt

Yêu cầu: Python 3.11+, Git. Thêm toolchain của repo bạn định chạy (dotnet, cmake, node...) trên máy worker.

**Windows (PowerShell):**

```powershell
cd D:\src\ai-work-factory
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item .env.example .env      # rồi sửa token trong .env
.\.venv\Scripts\awf db upgrade   # tạo schema (SQLite mặc định: var\awf.db)
```

**Linux/macOS:**

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env
.venv/bin/awf db upgrade
```

File `.env` ở thư mục gốc được nạp tự động (biến môi trường thật luôn được ưu tiên).
Lệnh `awf` có các subcommand: `db`, `api`, `orchestrator`, `worker`, `demo`.

## 4. Chạy demo trong 1 phút

Demo dùng repo mẫu `examples/demo/perception-service` (Python) và **model kịch bản offline**
(không cần API key, không tốn tiền):

```powershell
.\.venv\Scripts\awf demo
```

Mở `http://127.0.0.1:8000/`, vào **Approvals**, duyệt `approve_architecture`, xem run chạy tiếp, duyệt
`approve_merge`. Hoặc chạy không giao diện:

```powershell
.\.venv\Scripts\awf demo --auto-approve
```

Trong demo, Code Reviewer cố ý yêu cầu sửa ở vòng 1 (`check_health(None)` bị lỗi) → bạn sẽ thấy fix loop:
development/verification/review chạy lại với `iteration 2`, rồi QA và delivery.

## 5. Đưa một repo thật vào factory (từng bước)

Ví dụ thực tế: `D:\src\ai-workspace\06_Repositories\InternalFileServer` (.NET 8). Báo cáo đầy đủ của lần
chạy thử: [`docs/trials/internal-file-server.md`](trials/internal-file-server.md).

### Bước 1 — Quyết định origin mà factory được phép đẩy nhánh

Worker clone từ `repository.url` và **đẩy** nhánh `awf/run-*` và `awf/tasks/*` về đó. Base branch không bao
giờ bị ghi. Có 3 lựa chọn:

| Lựa chọn | Khi nào | Cách làm |
|---|---|---|
| Bare mirror cục bộ (khuyến nghị khi thử) | Không muốn đụng repo thật | `git clone --bare <repo> D:\factory\repos\InternalFileServer.git` |
| Thư mục repo cục bộ | Chấp nhận nhánh `awf/*` xuất hiện trong repo | `url` = đường dẫn repo (không đẩy được vào nhánh đang checkout, nhưng `awf/*` thì được) |
| Remote (GitHub/GitLab) | Làm việc nhóm | `url` = HTTPS/SSH URL; credential git cấu hình **trên worker** (credential helper/SSH key), bật branch protection cho `main` |

### Bước 2 — Xác định lệnh build/test/lint

Lệnh do **bạn** cấu hình, agent không tự chế lệnh. Mỗi lệnh là mảng argv; chương trình đầu tiên phải nằm
trong `command_allowlist` của `config/policies.yaml` (đã có python, pytest, dotnet, cmake, ctest, npm, make, go,
cargo...). Ví dụ:

| Loại repo | build_command | test_command |
|---|---|---|
| .NET | `["dotnet","build","App.csproj","-nologo","-warnaserror"]` | `["dotnet","test","tests/App.Tests","-nologo"]` |
| Python | `["python","-m","compileall","-q","src"]` | `["python","-m","pytest","-q"]` |
| CMake/C++ | `["cmake","--build","build"]` | `["ctest","--test-dir","build","--output-on-failure"]` |
| Node | `["npm","run","build"]` | `["npm","test"]` |

Mẹo: nếu repo có nhiều project (.csproj), luôn chỉ rõ file; nếu repo chưa có test, vẫn khai báo
`test_command` trỏ tới project test mà Developer sẽ tạo (Developer phải chạy `test.run` và nó phải pass).

### Bước 3 — Khởi động control plane và worker

Hai cửa sổ terminal (PowerShell):

```powershell
# Terminal 1: API + dashboard + orchestrator nhúng
.\.venv\Scripts\awf api --port 8000 --embedded-orchestrator
```

```powershell
# Terminal 2: worker trên máy có toolchain (ở đây: Windows có dotnet)
$env:AWF_WORKER_REGISTRATION_TOKEN = "<giống trong .env>"
.\.venv\Scripts\awf worker --url http://127.0.0.1:8000 --name win-worker-01 --workspace-root D:\factory\workspaces
```

Worker tự phát hiện platform (`windows-amd64`) và capability (git, python, dotnet, cmake, docker, node,
cuda...). Kiểm tra ở dashboard → **Workers**.

### Bước 4 — Tạo project

Qua dashboard (**Projects → New project**) hoặc API. Ví dụ PowerShell:

```powershell
$H = @{ Authorization = "Bearer $env:AWF_API_TOKEN" }
$project = @{
  name = "InternalFileServer"
  slug = "internal-file-server"
  repository = @{
    url = "D:/factory/repos/InternalFileServer.git"
    base_branch = "main"
    build_command = @("dotnet","build","InternalFileServer.csproj","-nologo","-warnaserror")
    test_command  = @("dotnet","test","tests/InternalFileServer.Tests","-nologo")
    lint_command  = @("dotnet","build","InternalFileServer.csproj","-nologo","-warnaserror","-p:EnforceCodeStyleInBuild=true")
    required_capabilities = @("dotnet")
  }
  labels = @{ platform = "windows" }   # job chỉ đi tới worker windows-*
  budget_usd = 20
  confidentiality = "internal"         # "restricted" = cấm gửi code ra model hosted
} | ConvertTo-Json -Depth 5
$p = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/projects -Headers $H -ContentType "application/json" -Body $project
```

`labels.platform` + `required_capabilities` quyết định worker nào được nhận job (routing theo capability).

### Bước 5 — Bắt đầu run với một objective

Viết objective cụ thể, có phạm vi và tiêu chí kiểm chứng được:

```powershell
$run = @{ workflow_id = "software-development"; inputs = @{ objective =
  "Add GET /api/health (status, version, storage writable, admin configured) and remove the insecure change-me admin password fallback. Add an xUnit test project." } } | ConvertTo-Json -Depth 4
$r = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/projects/$($p.id)/workflow-runs" -Headers $H -ContentType "application/json" -Body $run
$r.id
```

### Bước 6 — Theo dõi, duyệt, nhận kết quả

- Dashboard → run → xem pipeline, task, agent run (model, token, chi phí, tool call, critic), log job.
- **Approvals**: đọc `architecture_document`/`adr` rồi Approve hoặc Reject kèm comment (Reject → Architect
  làm lại với comment của bạn).
- Sau `approve_merge`: lấy nhánh `awf/run-<id>`, patch và `pull_request.md` (mục 9).

## 6. Bật Claude (model thật)

Mặc định `config/model-routing.yaml` định tuyến theo model class:

| Class | Model | Dùng cho |
|---|---|---|
| FAST | `claude-haiku-4-5` | việc cơ học (log summarization, refactor nhỏ) |
| BALANCED | `claude-sonnet-5-5` (effort medium) | analyst, test engineer, QA |
| REASONING | `claude-opus-5-5` (effort high) | architect, planner, code reviewer |
| CODE_SPECIALIST | `claude-opus-5-5` (effort high) | developer |
| LOCAL_PRIVATE | (chưa có) | project `restricted` → fail an toàn |

Agent chạy **trên worker**, nên credential đặt ở máy worker:

```powershell
$env:ANTHROPIC_API_KEY = "<key của bạn>"     # chỉ trên máy worker
Remove-Item Env:AWF_MODEL_PROVIDER -ErrorAction SilentlyContinue   # bỏ chế độ scripted
.\.venv\Scripts\awf worker --url http://127.0.0.1:8000 --name win-worker-01
```

Control plane cũng không được đặt `AWF_MODEL_PROVIDER=scripted` (biến này ghi đè route). Demo với model thật:
`awf demo --live-model`.

Chi tiết tích hợp (`awf/agent_runtime/adapters/anthropic_adapter.py`): Messages API, vòng lặp tool-use thủ
công (để mọi tool call đi qua Policy Engine), prompt caching tự động, `output_config.effort` theo class,
`fallbacks: "default"` khi model bị từ chối (refusal), lỗi 429/5xx là retryable, 400/401/403 là không retry.
Chi phí ước tính theo bảng `pricing` và hiển thị theo agent run / run / project. Đặt `budget_usd` cho
project: khi ngân sách còn dưới `budget_downgrade_threshold_usd`, router tự hạ class.

**Chế độ scripted** (`AWF_MODEL_PROVIDER=scripted` + `AWF_SCRIPTED_RESPONSES=<file.yaml>`): agent không gọi
model mà phát lại kịch bản; role không có trong kịch bản dùng hành vi tự động từ contract. Phần nền tảng
(git, sandbox, build/test thật, critic, gate) vẫn chạy thật — dùng để kiểm thử tích hợp repo trước khi tốn
tiền model. Xem `examples/*/scripted-responses.yaml`.

## 7. Dashboard

`http://<host>:8000/` — nút **Token** để nhập `AWF_API_TOKEN` (lưu trong trình duyệt).

| Trang | Nội dung |
|---|---|
| Overview | project, run/task đang chạy, task lỗi, approval chờ, worker online, chi phí LLM |
| Projects | danh sách, tạo project, chi tiết project, bắt đầu run |
| Run | pipeline stage (iteration, trạng thái), bảng task (critic decision + verdict), approval chờ, artifact |
| Task (click dòng) | tiêu chí chấp nhận, feedback, agent run (model, route reason, token, chi phí, critic findings, tool calls), log job |
| Artifact (click dòng) | nội dung, checksum, quan hệ trace, impact analysis |
| Approvals | hàng đợi duyệt + lịch sử |
| Workers | platform, capability, tải, heartbeat |
| Agents | KPI theo role: số run, success, retry, critic rejection, latency, chi phí |
| Audit | toàn bộ sự kiện |

## 8. Duyệt (human approval) và fix loop

**Gate mặc định:** `approve_architecture` (sau stage Architecture) và `approve_merge` (sau Prepare PR).

- **Approve** → task `DONE`, workflow chạy tiếp.
- **Reject + comment** trên task của agent → task chạy lại với comment làm feedback (đến khi hết
  `retry_policy.max_attempts`, sau đó run `FAILED`). Reject gate-only (merge) → run `FAILED`.

**Fix loop:** khi Test Engineer / Static analysis / Code Reviewer / QA trả verdict `CHANGES_REQUIRED`, stage
có `on_changes_required: {goto: development, max_iterations: N}` sẽ tăng iteration cho
`development → … → stage hiện tại`. Developer nhận 1 task "Address review findings (iteration N)" kèm
findings và report. Quá `max_iterations` → run `FAILED` với lý do rõ ràng.

**Retry task do Critic:** output thiếu section, JSON sai schema, build/test fail, không có thay đổi…
→ `CHANGES_REQUIRED` → task chạy lại với danh sách lỗi. Critic `BLOCK` (secret trong output, liên tục
vượt quyền) → dừng run và chờ người.

**Retry thủ công:** `POST /api/v1/tasks/{id}/retry` (task `FAILED` → `READY`, run tiếp tục).
**Huỷ:** `POST /api/v1/workflow-runs/{id}/cancel`.

## 9. Lấy kết quả: nhánh git, patch, PR

```powershell
# Từ repo làm việc của bạn, thêm origin mà factory đã đẩy nhánh vào:
git remote add factory D:/factory/repos/InternalFileServer.git
git fetch factory "awf/run-0af3d47f"
git log --oneline main..factory/awf/run-0af3d47f
git diff main...factory/awf/run-0af3d47f
# Tạo PR (nếu dùng GitHub): đẩy nhánh lên origin rồi dùng nội dung pull_request.md làm mô tả
git push origin factory/awf/run-0af3d47f:refs/heads/feature/health-endpoint
```

Artifact `source_patch` (`delivery/patch.patch`) áp dụng được bằng `git apply`. Artifact `pull_request`
chứa nhánh nguồn/đích, diffstat, danh sách commit, trace artifact. Merge vào nhánh protected **luôn** do
người thực hiện (policy level 2).

## 10. Worker: Windows, Linux, Jetson, GPU

```text
awf worker --url <control-api> --name <tên duy nhất> [--platform linux-arm64] [--capabilities git,python,cmake,cuda,realsense]
           [--label device=jetson-orin --label location=lab] [--workspace-root <dir>]
```

- Platform tự phát hiện: `windows-amd64`, `linux-amd64`, `linux-arm64` (Jetson)… Job yêu cầu `platform: linux`
  khớp mọi `linux-*`.
- Capability tự phát hiện từ PATH (git, python, docker, cmake, ctest, dotnet, npm, node, nvcc→cuda, ros2);
  thêm capability phần cứng (vd. `realsense`) bằng `--capabilities`.
- Đăng ký cần `AWF_WORKER_REGISTRATION_TOKEN`; worker nhận token riêng dùng cho mọi call sau đó.
- Heartbeat mỗi 15s; quá `worker_heartbeat_timeout_seconds` (90s) → offline, job đang chạy được requeue.
- Mỗi job: clone mới vào `<workspace_root>/<job_id>`, xoá sau khi xong.
- Chạy như service: Windows dùng NSSM/Task Scheduler; Linux dùng systemd (`ExecStart=/opt/awf/.venv/bin/awf worker ...`).

Routing theo stage: thêm `worker: {platform: linux, capabilities: [cuda]}` vào stage trong workflow, hoặc
`labels.platform` + `repository.required_capabilities` ở project.

## 11. Cấu hình

| File / biến | Nội dung |
|---|---|
| `.env` | `AWF_API_TOKEN`, `AWF_WORKER_REGISTRATION_TOKEN`, `DATABASE_URL`, `AWF_OBJECT_STORE`, `AWF_MODEL_PROVIDER`, `AWF_EMBEDDED_ORCHESTRATOR` |
| `config/system.yaml` | môi trường, DB, artifact store, chu kỳ tick, timeout heartbeat, số lần thử job, giới hạn context |
| `config/model-routing.yaml` | class → model, rule theo task type, confidentiality, budget, bảng giá |
| `config/policies.yaml` | action → level/permission, command allowlist, timeout & giới hạn output/file |
| `workflows/*.yaml` | workflow |
| `agents/contracts|profiles|prompts` | agent |
| `knowledge/` | tài liệu công ty (coding standard, security policy, template) đưa vào context theo profile |

`AWF_HOME` trỏ tới thư mục gốc (mặc định tự tìm). `AWF_ENVIRONMENT=development` cho phép API không token
— chỉ dùng khi bind `127.0.0.1`.

## 12. Viết workflow mới

```yaml
id: hotfix
version: "1.0"
inputs: [objective, repository]
stages:
  - id: development
    role: engineering.developer
  - id: verification
    depends_on: [development]
    parallel:
      - {id: tests, role: engineering.test-engineer}
      - {id: lint, tool: lint.run}
    on_changes_required: {goto: development, max_iterations: 2}
  - id: delivery
    tool: git.export_patch
    depends_on: [verification]
  - id: merge_approval
    depends_on: [delivery]
    gate: {type: human_approval, action: approve_merge, risk_level: 2}
```

Quy tắc: mỗi stage có đúng một trong `role` / `tool` / `parallel`, hoặc chỉ `gate`. `foreach: planned_task`
cần stage Planner phía trước. `goto` phải là stage phía trên. Workflow được validate khi khởi động (cycle,
stage/role/tool không tồn tại). Tool job hiện có: `lint.run`, `test.run`, `build.run`, `git.export_patch`.
`status: draft` để giữ workflow chưa đủ role (như `content-production`, `video-production`).

## 13. Viết agent (contract + profile + prompt)

**Contract** (`agents/contracts/<id>.yaml`) — cái agent phải giao:

```yaml
id: engineering.security-reviewer
version: "1"
role: {name: Security Reviewer, objective: Find security defects in the integrated diff.}
outputs:
  - name: security_report
    type: markdown            # markdown | text | json | source_diff (worker) | test_evidence (worker)
    artifact_type: security_report
    required_sections: [Summary, Findings, Verdict]
decision: {values: [APPROVE, CHANGES_REQUIRED]}   # có verdict => có thể kích hoạt fix loop
allowed_tools: [fs.list, fs.read, fs.search, git.diff]
permissions:
  filesystem: {read: true, workspace_write: false}
  git: {read: true, branch_write: false}
completion_rules: []          # build_pass | unit_test_pass | lint_pass | has_changes | tests_executed
retry_policy: {max_attempts: 2}
timeout_policy: {seconds: 1500, max_turns: 30}
human_approval: false
```

**Profile** (`agents/profiles/<id>.yaml`) — cách agent chạy:

```yaml
id: engineering.security-reviewer
version: "1.0"
contract: engineering.security-reviewer
prompt: engineering.security-reviewer.md
task_type: code_review
model_policy: {class: REASONING, fallback: BALANCED}
context:
  include: [objective, task, "upstream:architecture", "upstream:development", workspace]
  knowledge: [company/security-policy.md]
```

**Prompt** (`agents/prompts/<file>.md`): mô tả vai trò, cách làm việc, tiêu chí verdict. Runtime tự thêm luật
nền tảng (dùng tool, evidence, gọi `submit_result` một lần).

Agent có quyền ghi (`filesystem.workspace_write` + `git.branch_write`) sẽ chạy trên nhánh task riêng và được
commit/merge tự động; agent chỉ đọc làm việc trên nhánh integration và mọi file nó để lại bị bỏ.

## 14. Traceability, impact analysis, audit

- Mỗi artifact có quan hệ tới artifact đầu vào: `implements` (architecture→requirement, code→architecture),
  `verifies`, `reviews`, `includes`, `derived_from`.
- `GET /api/v1/artifacts/{id}/relations` — quan hệ trực tiếp.
- `GET /api/v1/artifacts/{id}/impact` — mọi artifact/task phát sinh từ artifact này (khi requirement đổi, biết
  ngay architecture/code/test/PR nào bị ảnh hưởng).
- `GET /api/v1/audit?run_id=...` — sự kiện: `WORKFLOW_STARTED`, `TASK_ASSIGNED`, `JOB_CLAIMED`,
  `AGENT_STARTED`, `TOOL_CALLED`, `POLICY_DENIED`, `ARTIFACT_CREATED`, `CRITIC_PASSED/BLOCKED`,
  `TASK_CHANGES_REQUIRED`, `FIX_LOOP_STARTED`, `APPROVAL_REQUESTED/GRANTED/REJECTED`, `WORKFLOW_COMPLETED`…
- Log JSON có `project_id`, `workflow_run_id`, `task_id`, `job_id`, `worker_id`, `event`.

## 15. Bảo mật

- Đặt `AWF_API_TOKEN` và `AWF_WORKER_REGISTRATION_TOKEN`; ngoài `development`, API trả 503 cho mọi request admin/đăng ký worker khi thiếu token.
- Không agent nào có quyền production / merge protected / shell (kiểm tra bằng unit test).
- File tool bị khoá trong workspace, không truy cập `.git`; lệnh build/test chỉ từ cấu hình + allowlist.
- Secret được redact trước khi vào prompt model, log, audit, artifact. Critic `BLOCK` nếu diff/output chứa
  dữ liệu dạng credential — nên **đừng để secret trong repo**: factory sẽ không đẩy chúng ra ngoài, nhưng
  agent cũng không thể sửa đúng các dòng đó (chúng bị che).
- Credential model chỉ ở worker; credential production không bao giờ đặt trên coding worker.
- Project `confidentiality: restricted` → không gửi code tới model hosted.

## 16. Triển khai bằng Docker Compose + PostgreSQL

```powershell
Copy-Item .env.example .env    # đặt token, mật khẩu
docker compose up -d --build   # postgres(pgvector), redis, minio, control-api (tự migrate), orchestrator, linux-worker
```

- API/dashboard: `http://127.0.0.1:8000`. Các cổng chỉ bind `127.0.0.1`.
- Worker trong compose là Linux; worker Windows chạy native (mục 10) trỏ vào API.
- Repo cho worker container: mount qua `AWF_REPOS_DIR` → `/repos`, hoặc dùng URL remote.
- Artifact store MinIO: `AWF_OBJECT_STORE=s3`, `AWF_S3_ENDPOINT`, `AWF_S3_BUCKET` (+ credential AWS chuẩn).

## 17. API tham khảo

Base `/api/v1`, header `Authorization: Bearer <AWF_API_TOKEN>`, tùy chọn `X-Actor: <tên người duyệt>`.
OpenAPI đầy đủ: `http://<host>:8000/docs`.

| Method | Path | Mô tả |
|---|---|---|
| POST/GET | `/projects`, `/projects/{id}` | tạo / liệt kê / chi tiết (kèm `spend_usd`) |
| GET | `/workflows`, `/agents` | định nghĩa workflow, agent |
| POST | `/projects/{id}/workflow-runs` | `{workflow_id, inputs: {objective}}` |
| GET | `/projects/{id}/workflow-runs`, `/workflow-runs/{id}` | run + stage overview + chi phí |
| POST | `/workflow-runs/{id}/cancel` | huỷ |
| GET | `/workflow-runs/{id}/tasks`, `/tasks/{id}` | task, agent run, job, approval, artifact |
| POST | `/tasks/{id}/retry` | retry task FAILED |
| GET | `/jobs/{id}`, `/jobs/{id}/logs` | job + log worker |
| GET | `/projects/{id}/artifacts?run_id=&type=` | artifact |
| GET | `/artifacts/{id}`, `/content`, `/relations`, `/impact` | metadata, nội dung, trace, impact |
| GET | `/approvals?state=pending` | hàng đợi |
| POST | `/approvals/{id}/approve`, `/reject` | `{comment}` |
| GET | `/workers`, `/overview`, `/stats/agents`, `/audit` | quan sát |
| POST | `/workers/register`, `/workers/{id}/heartbeat`, `/workers/{id}/claim` | giao thức worker |
| POST | `/jobs/{id}/progress`, `/log`, `/complete`, `/fail` | giao thức worker |

## 18. Kiểm thử nền tảng

```powershell
.\.venv\Scripts\python -m pytest -q -p no:logging            # toàn bộ (47 test)
.\.venv\Scripts\python -m pytest tests/unit -q                # state machine, workflow, policy, router, critic, redaction
.\.venv\Scripts\python -m pytest tests/integration -q         # giao thức worker, auth, routing, các kịch bản lỗi
.\.venv\Scripts\python -m pytest tests/e2e -q                 # workflow đầy đủ trên git repo thật + fix loop
```

Các kịch bản lỗi được kiểm (Design §20): worker offline, model timeout, output sai cấu trúc, build fail,
test fail, critic block, approval reject, duplicate event, cancel, retry thủ công, project restricted.

## 19. Xử lý sự cố

| Triệu chứng | Nguyên nhân / cách xử lý |
|---|---|
| Task `WAITING` mãi | Không worker nào khớp `requirements` (platform/capability). Xem job (`/jobs/{id}`) → `requirements`; xem Workers. |
| `executable 'x' is not in the command allowlist` | Thêm vào `command_allowlist` trong `config/policies.yaml`. |
| Developer fail `MISSING_ACCEPTANCE_EVIDENCE` | Agent không chạy `build.run`/`test.run`, hoặc lệnh chưa cấu hình. |
| `COMPLETION_RULE_FAILED` | Build/test thật sự fail — xem log job và `test_evidence`. |
| `NO_CHANGES` | Developer không tạo thay đổi nào (thường do `fs.replace` không khớp). |
| `critic blocked: ... credential-like material` | Diff/output chứa secret. Gỡ secret khỏi repo/thay đổi, dùng biến môi trường. |
| `INTEGRATION_CONFLICT` | Hai task song song sửa cùng chỗ; Planner nên đặt `depends_on`. |
| `no model available for class LOCAL_PRIVATE` | Project `restricted`; chưa có model cục bộ. |
| `model provider rejected the request` | Thiếu/sai `ANTHROPIC_API_KEY` trên worker. |
| `git push ... rejected` vào repo cục bộ | Không đẩy vào nhánh đang checkout; dùng bare mirror. |
| API 401 | Thiếu header `Authorization: Bearer ...` / nhập Token trên dashboard. |

## 20. Giới hạn hiện tại và lộ trình

Đã có (Phase 0 + phần Phase 1): domain model, workflow engine YAML, agent contract/profile, agent runtime +
adapter Claude/scripted, model router, policy engine, tool SDK, worker phân tán theo capability, sandbox per
job, critic theo evidence, fix loop, human approval, artifact versioning + traceability + impact, audit,
dashboard, Alembic, Docker Compose, 47 test.

Tiếp theo:

1. **Phase 1 còn lại:** mở PR thật (GitHub/GitLab API, level 0), tích hợp CI (đọc kết quả pipeline làm evidence),
   LLM critic bổ sung cho rule-based critic.
2. **Phase 2:** container per job, Jetson/GPU worker thực tế, staging deploy (level 1), security/dependency
   scan làm tool stage, release workflow + gate production.
3. **Phase 3:** pgvector cho project memory/semantic search, impact analysis khi requirement đổi tự tạo task.
4. **Phase 4:** content/video workflow (đang ở trạng thái draft).
5. **Phase 5:** budget đa project, adaptive routing theo KPI agent.
