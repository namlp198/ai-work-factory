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
21. [Chạy trên nhiều máy: cùng source, cùng lệnh](#21-chạy-trên-nhiều-máy-cùng-source-cùng-lệnh)

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

Yêu cầu: **Python 3.12** (ghi trong `.python-version`) và Git. Trên máy worker cần thêm toolchain của repo
bạn định chạy (dotnet, cmake, node...).

Mọi máy cài **giống hệt nhau** bằng một lệnh bootstrap (chạy lại sau mỗi `git pull` cũng an toàn):

```powershell
.\scripts\bootstrap.ps1            # Windows
```

```bash
scripts/bootstrap.sh               # Linux / macOS / Jetson
```

Bootstrap làm các việc sau, theo thứ tự:

1. tạo `.venv` bằng đúng Python 3.12 (tạo lại nếu sai phiên bản);
2. cài đúng phiên bản trong `requirements.lock`;
3. tạo `.env` cho máy này (chỉ lần đầu, không bao giờ ghi đè);
4. migrate database;
5. chạy `awf doctor`.

Sau đó chạy máy bằng:

```powershell
.\.venv\Scripts\awf start
```

Lệnh `awf` có các subcommand: `start`, `env init`, `doctor`, `db`, `api`, `orchestrator`, `worker`, `demo`.
Chạy nhiều máy: xem [mục 21](#21-chạy-trên-nhiều-máy-cùng-source-cùng-lệnh).

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

`http://<host>:8000/` — đăng nhập bằng `AWF_API_TOKEN` một lần (giữ 30 ngày), hoặc chạy `awf open` trên máy trung tâm để vào thẳng. Xem [mục 21](#đăng-nhập-dashboard-không-phải-nhập-token-mỗi-lần).

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
- Chạy như service: Linux dùng `sudo scripts/install-systemd.sh`, Windows dùng `.\scripts\install-task.ps1` (xem mục 21).

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
.\.venv\Scripts\python -m pytest -q -p no:logging            # toàn bộ (78 test)
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
| API 401 | Script: thiếu `Authorization: Bearer ...`. Dashboard: đăng nhập lại hoặc `awf open`. |
| Worker: `worker name ... is in use by another online machine` | Hai máy trùng tên: đặt `AWF_WORKER_NAME` khác nhau. |
| Worker: `... was revoked` | Worker đã bị Revoke: Delete bản ghi trên dashboard nếu muốn cho tham gia lại. |
| Worker: `... is a public address over plain HTTP` | Dùng `https://` (Caddy/tunnel) hoặc Tailscale; xem mục 21. |
| Job tự chạy lại với `JOB_LEASE_EXPIRED` | Worker mất liên lạc quá lâu; kiểm tra mạng/worker đó. |

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

## 21. Chạy trên nhiều máy: cùng source, cùng lệnh

### Nguyên tắc

| Thứ | Giống nhau trên mọi máy (trong git) | Khác nhau theo máy (không commit) |
|---|---|---|
| Mã nguồn, workflow, agent, config, migration | ✔ | |
| Phiên bản Python (`.python-version`) và package (`requirements.lock`) | ✔ | |
| Line ending (`.gitattributes`) | ✔ | |
| Lệnh cài đặt và chạy: `bootstrap` → `awf start` | ✔ | |
| Vai trò máy, URL máy trung tâm, token, API key Claude | | `.env` (tạo bằng `awf env init`) |
| Dữ liệu runtime: DB, artifact, workspace, `worker-id` | | `var/` |

Mọi đường dẫn tương đối (`DATABASE_URL=sqlite:///./var/awf.db`, `AWF_WORKSPACE_ROOT`,
`AWF_SCRIPTED_RESPONSES`...) được tính theo **thư mục repo**. Tên worker mặc định là hostname; platform và
capability tự phát hiện.

### Ba vai trò (`AWF_ROLE`)

| Vai trò | Chạy gì | Dùng khi |
|---|---|---|
| `all` | API + dashboard + orchestrator + 1 worker cục bộ | 1 máy |
| `control-plane` | API + dashboard + orchestrator + **database** | máy trung tâm |
| `worker` | worker kết nối tới `AWF_CONTROL_URL`, **không có database** | máy build/test ở bất kỳ đâu |

### Mô hình: một máy trung tâm, worker ở nhiều địa điểm

```text
            Địa điểm A                         Internet / VPN                 Địa điểm B, C, ...
 ┌──────────────────────────────┐                                   ┌───────────────────────────┐
 │ MÁY TRUNG TÂM (control-plane)│ ◄── HTTPS, worker gọi RA ──────── │ worker Windows (.NET)     │
 │  API + dashboard + orchestr. │ ◄── HTTPS ─────────────────────── │ worker Linux / Jetson     │
 │  DATABASE + artifact (DUY NHẤT)│ ◄── trình duyệt (dashboard) ──── │ laptop của bạn            │
 └──────────────────────────────┘                                   └───────────────────────────┘
                    ▲                                                            │
                    └────────────── GitHub / GitLab (mã nguồn các repo) ◄────────┘
```

**Không đồng bộ database giữa các máy.** Chỉ máy trung tâm có database và artifact — đó là nguồn sự thật duy
nhất. Worker không lưu trạng thái lâu dài. Nó chủ động kết nối **ra** máy trung tâm để:

1. nhận job;
2. báo heartbeat và tiến độ;
3. nộp kết quả và artifact.

Mã nguồn các repo đi qua git remote (GitHub/GitLab/Gitea), không qua database. Vì worker chỉ gọi ra, các máy
worker **không cần mở cổng nào** và chạy được sau NAT/firewall của từng địa điểm. Chỉ máy trung tâm cần
truy cập được từ xa.

Những gì nền tảng tự lo khi mạng không ổn định:

- **Mất mạng lúc nộp kết quả:** worker thử lại với backoff tới 30 phút (`AWF_RESULT_RETRY_SECONDS`).
  Báo tiến độ và log là best-effort, nên job không bị dừng chờ chúng.
- **Worker biến mất hoặc kết quả không bao giờ tới:** mỗi job có **lease**, bằng timeout của task cộng 15 phút.
  Hết lease, job được giao lại cho worker khác.
- **Worker khởi động lại:** máy trung tâm giao lại các job nó đang dở.
- **Hai máy cùng tên worker:** bị từ chối (mỗi máy có `var/worker-id` riêng). Đặt `AWF_WORKER_NAME`.
- **Kết quả bị gửi trùng:** bị bỏ qua (HTTP 409).

Đã kiểm chứng thật: tắt máy trung tâm 25 giây khi worker (máy khác) đang chạy job.

- Job vẫn chạy xong trong lúc máy trung tâm tắt.
- Worker thử nộp kết quả 3 lần, rồi nộp thành công ngay khi máy trung tâm bật lại.
- Job không phải làm lại; run đi tiếp bình thường.

### Đưa máy trung tâm ra internet — chọn một trong hai cách

**Cách A — Mạng riêng Tailscale (khuyến nghị, đơn giản và an toàn nhất)**

Tailscale tạo mạng riêng mã hoá WireGuard giữa các máy của bạn ở mọi địa điểm. Không cần domain, không mở cổng
ra internet, dashboard không lộ ra ngoài.

1. Cài Tailscale trên máy trung tâm và mọi máy worker, đăng nhập cùng một tài khoản (tailnet).
2. Máy trung tâm:
   ```powershell
   .\scripts\bootstrap.ps1 -Role control-plane -PublicUrl http://<ten-may>.<tailnet>.ts.net:8000
   .\.venv\Scripts\awf start
   ```
3. Bootstrap in ra lệnh cài sẵn cho worker. Chạy lệnh đó trên từng máy worker, rồi `awf start`.

Địa chỉ `*.ts.net` và `100.x.y.z` được worker chấp nhận dù là `http://`, vì đường truyền đã được WireGuard
mã hoá. Muốn có HTTPS thật trong tailnet thì dùng `tailscale serve`.

**Cách B — Domain công khai + HTTPS (Caddy tự lấy chứng chỉ Let's Encrypt)**

Dùng khi worker là máy không cài được VPN (ví dụ CI runner, máy của đối tác).

1. Trỏ DNS `factory.example.com` về IP công khai của máy trung tâm, mở cổng **80 và 443** (chỉ hai cổng này).
2. Máy trung tâm:
   ```powershell
   .\scripts\bootstrap.ps1 -Role control-plane -PublicUrl https://factory.example.com
   .\.venv\Scripts\awf start                    # API chỉ nghe 127.0.0.1:8000
   $env:AWF_PUBLIC_DOMAIN = "factory.example.com"
   caddy run --config infrastructure\deploy\Caddyfile    # HTTPS 443 -> 127.0.0.1:8000
   ```
   Hoặc dùng Docker: `AWF_PUBLIC_DOMAIN=factory.example.com docker compose --profile public up -d`.
3. Worker: `bootstrap ... -Role worker -ControlUrl https://factory.example.com -RegistrationToken <token>`.

Biến thể không cần mở cổng: Cloudflare Tunnel (`cloudflared tunnel --url http://127.0.0.1:8000`) với domain
của bạn. Nếu dùng tunnel/proxy ở máy khác, đặt `AWF_TRUSTED_PROXIES` để API tin header `X-Forwarded-*`.

**Bảo vệ có sẵn**

- Worker **từ chối** gửi token và mã nguồn qua `http://` tới địa chỉ công khai; chỉ cho phép HTTPS, LAN hoặc
  Tailscale. `AWF_ALLOW_INSECURE_HTTP=1` chỉ dùng để thử nghiệm.
- `awf doctor` báo `FAIL` khi URL không an toàn.
- API từ chối khởi động trên địa chỉ ngoài localhost nếu thiếu token.
- Đăng nhập dashboard bị chặn tạm (HTTP 429) sau 10 lần sai trong 10 phút trên một IP.
- Chứng chỉ của CA riêng: đặt `AWF_CA_BUNDLE=<đường dẫn file .pem>` trên worker.

### Đăng nhập dashboard (không phải nhập token mỗi lần)

- Nhập `AWF_API_TOKEN` **một lần** cho mỗi trình duyệt. Trình duyệt giữ phiên đăng nhập 30 ngày bằng cookie
  HttpOnly; phiên vẫn còn sau khi khởi động lại server.
- Trên máy trung tâm, `awf open` mở dashboard và **đăng nhập sẵn**. Lệnh dùng link một lần, hết hạn sau
  2 phút; token không rời khỏi máy. `awf open --print-only` in link ra để mở ở trình duyệt khác.
- Nút **Logout** để đăng xuất. Đổi `AWF_API_TOKEN` thì mọi phiên đăng nhập đều hết hiệu lực.
- Script/CI vẫn dùng header `Authorization: Bearer <AWF_API_TOKEN>`.

### Quản lý worker

- Dashboard → **Workers**: tên, `machine_id`, platform, capability, nhãn, heartbeat.
- **Revoke** một máy bị mất hoặc nghi bị xâm nhập. Token của nó vô hiệu ngay, job của nó được giao lại, và nó
  không đăng ký lại được.
- Nếu máy bị xâm nhập, đổi luôn `AWF_WORKER_REGISTRATION_TOKEN` trên máy trung tâm (`awf env init --force`
  tạo mới toàn bộ token, nên cần cài lại các worker còn lại). **Delete** bản ghi worker đã revoke để máy
  đó tham gia lại.
- Nhãn để định tuyến theo địa điểm: `AWF_WORKER_LABELS=site=hanoi,gpu=rtx4090`.

### Chạy như dịch vụ (tự bật khi khởi động máy)

**Linux (Ubuntu/Debian/Jetson) — systemd.** Sau khi đã chạy bootstrap:

```bash
sudo scripts/install-systemd.sh          # cài, bật khi khởi động, chạy ngay
```

- `awf.service` chạy `awf start` bằng user sở hữu thư mục repo (không chạy bằng root). Vai trò vẫn lấy từ
  `.env`, nên cùng một lệnh dùng được cho máy trung tâm và máy worker Linux.
- Tự chạy lại sau 5 giây nếu tiến trình chết. Dừng nhẹ nhàng khi tắt máy.
- Máy có database SQLite (`control-plane`/`all`) được cài thêm `awf-backup.timer`: sao lưu lúc 02:30 mỗi đêm vào
  `var/backups/`, giữ 14 bản. Nếu máy tắt lúc 02:30 thì timer chạy bù khi bật lại.
- Lệnh thường dùng:

  | Việc | Lệnh |
  |---|---|
  | Xem trạng thái | `systemctl status awf` |
  | Xem log trực tiếp | `journalctl -u awf -f` |
  | Áp dụng thay đổi sau khi sửa `.env` hoặc `git pull` + bootstrap | `sudo systemctl restart awf` |
  | Xem lịch sao lưu | `systemctl list-timers awf-backup` |
  | Gỡ dịch vụ (dữ liệu trong `var/` được giữ nguyên) | `sudo scripts/install-systemd.sh --uninstall` |
- Máy trung tâm có `ufw` đang bật: mở cổng cho LAN, ví dụ
  `sudo ufw allow from 192.168.0.0/16 to any port 8000 proto tcp`.

Đã thử trên Ubuntu 24.04 (systemd 255):

- cài đặt;
- `systemd-analyze verify`;
- API trả lời;
- giết tiến trình bằng `SIGKILL` → tự chạy lại;
- chạy backup qua service;
- dừng sạch;
- gỡ cài đặt.

**Windows — Task Scheduler (không cần quyền admin).** Thường dùng cho PC Windows làm worker, sau khi đã chạy
bootstrap:

```powershell
.\scripts\install-task.ps1          # đăng ký, chạy mỗi lần bạn đăng nhập Windows, chạy ngay
```

- Chạy `awf start --supervise` **ẩn hoàn toàn** (`pythonw`, không có cửa sổ console) trong phiên đăng nhập của
  bạn, nên dùng được credential git và `.env` của bạn. Log ghi vào `var\logswf.log`, xoay vòng ở 10 MB
  và giữ 3 bản cũ.
- Bỏ giới hạn 72 giờ mặc định của Task Scheduler. Vẫn chạy khi máy dùng pin hoặc đang rảnh.
- Task Scheduler **không** tự chạy lại tác vụ khi tiến trình bị chết, nên script thêm một trigger canh chừng
  (watchdog) kích hoạt lại mỗi 5 phút. Lúc worker đang chạy thì lần kích hoạt đó bị bỏ qua.
- Lỗi Python bất ngờ thì `--supervise` tự khởi động lại trong vài giây. Lỗi cấu hình (sai token, URL không an
  toàn) thì dừng và ghi rõ lý do vào log.
- Lệnh thường dùng:

  | Việc | Lệnh |
  |---|---|
  | Trạng thái + log gần nhất | `.\scripts\install-task.ps1 -Status` |
  | Áp dụng thay đổi sau khi sửa `.env` hoặc `git pull` + bootstrap | `.\scripts\install-task.ps1 -Restart` |
  | Xem log trực tiếp | `Get-Content var\logswf.log -Wait` |
  | Tạm tắt, kể cả watchdog (bật lại bằng `-Start`) | `.\scripts\install-task.ps1 -Stop` |
  | Gỡ | `.\scripts\install-task.ps1 -Uninstall` |
- Tác vụ chỉ chạy khi bạn **đã đăng nhập** Windows; khoá màn hình thì không sao. Nếu muốn PC làm việc lúc
  bạn vắng mặt, đặt chế độ sleep thành *Never* (Settings → System → Power).

Đã thử trên Windows 10:

- cài đặt;
- chạy ẩn;
- worker đăng ký với máy trung tâm Linux và chạy trọn một workflow 13 job;
- `-Restart`;
- giết tiến trình → watchdog bật lại;
- `-Stop` → watchdog **không** bật lại;
- `-Start`;
- `-Uninstall` không để sót tiến trình.

Riêng trigger "khi đăng nhập" chưa thử bằng một lần đăng xuất/đăng nhập thật.

### Sao lưu (chỉ máy trung tâm)

```powershell
.\.venv\Scripts\awf db backup --keep 14     # SQLite: bản sao nhất quán khi đang chạy -> var\backups\
```

Lên lịch chạy hằng ngày (Task Scheduler/cron). Sao lưu cả thư mục artifact (`var\artifacts`). PostgreSQL thì
dùng `pg_dump`. Khi đổi máy trung tâm, chép `.env`, `var\` (hoặc dump PostgreSQL) sang máy mới và trỏ
DNS/Tailscale name sang. Worker không phải cài lại.

### Cập nhật mọi máy

Luôn cùng một quy trình:

```bash
git pull
scripts/bootstrap.sh          # Windows: .\scripts\bootstrap.ps1 — cài đúng lock mới, giữ nguyên .env
.venv/bin/awf start           # máy trung tâm tự chạy migration mới (dữ liệu được giữ)
```

Nên cập nhật máy trung tâm trước, rồi đến các worker.

### Những thứ phải "chung" giữa các máy

- **Repository của project** phải truy cập được từ mọi worker có thể nhận job của project đó. Dùng git remote;
  đường dẫn cục bộ như `D:/...` chỉ đúng trên một máy. Credential git (SSH key / credential helper) cài trên
  từng worker.
- **Lệnh build/test của project** chạy bằng PATH của worker. Giới hạn worker bằng `labels.platform` và
  `required_capabilities` của project.
- **Database:** một máy trung tâm có một database. SQLite đủ cho một máy trung tâm. Dùng PostgreSQL khi cần
  sao lưu/HA chuyên nghiệp.

### Cách đã kiểm chứng

- Clone sạch → bootstrap → `doctor` OK → toàn bộ test pass trên Windows và Linux (WSL Ubuntu), và trên CI
  GitHub Actions (`ubuntu-latest`, `windows-latest`).
- Phân tán: máy trung tâm Linux, worker Windows. Workflow chạy tới `COMPLETED`; tắt máy trung tâm giữa lúc chạy
  job vẫn không mất kết quả.
- Nâng cấp tại chỗ một database đang có dữ liệu từ migration `0001` lên `0002` sau `git pull`.
- Đăng nhập dashboard bằng `awf open`, phiên vẫn còn sau khi khởi động lại server, đăng xuất: kiểm tra trên
  trình duyệt thật.

**Chưa kiểm chứng trên máy này:** Tailscale, Caddy/Let's Encrypt và Cloudflare Tunnel, vì máy chưa cài các
công cụ đó và chưa có domain. Docker image cũng chưa được build vì Docker daemon chưa chạy.

### Docker (tùy chọn)

`docker compose up -d --build` dùng cùng `requirements.lock` và Python 3.12. Profile `public` thêm Caddy cho
HTTPS. Worker Windows (.NET) vẫn chạy native bằng bootstrap.

### Đổi dependency

Sửa `pyproject.toml`, rồi trên Python 3.12:

```bash
pip install -e ".[dev,postgres]" && pip freeze --exclude-editable > requirements.lock
```

Giữ dòng `colorama ... ; sys_platform == "win32"`, rồi commit. `awf doctor` báo `FAIL` nếu máy nào còn lệch
lock.
