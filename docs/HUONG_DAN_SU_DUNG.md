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
  labels = @{ platform = "windows" }   # BẮT BUỘC: run chỉ đi tới máy windows-*
  budget_usd = 20
  confidentiality = "internal"         # "restricted" = cấm gửi code ra model hosted
} | ConvertTo-Json -Depth 5
$p = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/projects -Headers $H -ContentType "application/json" -Body $project
```

`labels.platform` + `required_capabilities` quyết định **máy nào được nhận run** (xem mục 10, "Một run — một
máy"). Nếu không có máy nào đã đăng ký đáp ứng, run bị từ chối ngay khi tạo, kèm lý do từng máy không đạt.

**`labels.platform` là bắt buộc**: `windows`, `linux`, `linux-amd64`, `linux-arm64`, … hoặc `any` khi project
chạy được ở mọi nơi. Không có giá trị mặc định ngầm: project tạo trước khi có quy định này sẽ bị từ chối khi
bắt đầu run cho tới khi được khai báo, trên trang project (**Where it runs → Save**) hoặc:

```powershell
Invoke-RestMethod -Method Patch -Uri http://127.0.0.1:8000/api/v1/projects/<id> -Headers $H `
  -ContentType "application/json" -Body '{"labels": {"platform": "windows"}}'
```

Trang project liệt kê mọi máy đã đăng ký: máy nào chạy được project, và máy không chạy được thì thiếu gì (sai
platform, thiếu capability nào). Một project WPF/WinForms nên yêu cầu `dotnet-windowsdesktop` thay vì chỉ
`dotnet`, vì máy Linux cũng có thể cài .NET SDK.

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

## 6. Bật Claude hoặc Gemini (model thật)

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

### Gemini

Mỗi run dùng **một** nhà cung cấp cho mọi agent của nó: Claude (`anthropic`, mặc định) hoặc Gemini (`google`).
Bảng `providers.google` trong `config/model-routing.yaml` ánh xạ cùng các class sang model Gemini:

| Class | Gemini |
|---|---|
| FAST | `gemini-3.5-flash-lite` (thinking low) |
| BALANCED | `gemini-3.8-flash` (thinking medium) |
| REASONING, CODE_SPECIALIST | `gemini-4-argon` (thinking high) |
| MULTIMODAL | `gemini-3.8-flash` |
| LOCAL_PRIVATE | không đổi: project `restricted` không bao giờ được gửi sang model hosted |

Chọn nhà cung cấp, theo thứ tự ưu tiên:

1. Khi tạo run: dashboard → **Model**, hoặc API `{"llm": "google"}`.
2. Mặc định của project: trang project → **Default model** (lưu ở `labels.llm`).
3. `default_provider` trong `config/model-routing.yaml` (`anthropic`).

Key đặt **trên máy worker**: `GEMINI_API_KEY=<key từ Google AI Studio>` (hoặc `GOOGLE_API_KEY`) trong `.env`
của worker, rồi khởi động lại worker. Worker tự khai báo capability theo key nó có: `llm-anthropic`,
`llm-google`. Run dùng Gemini chỉ được giao cho máy có `llm-google`; nếu không máy nào có, run bị từ chối
ngay khi tạo, kèm lý do. Với `AWF_WORKER_CAPABILITIES` (ghi đè), các capability `llm-*` vẫn theo key thật.

Sau khi cập nhật lên phiên bản này, **khởi động lại mọi worker** để chúng khai báo `llm-*`; worker cũ chưa
khai báo sẽ không nhận run dùng model thật.

Chi tiết tích hợp (`awf/agent_runtime/adapters/gemini_adapter.py`): API `generateContent` qua REST (không thêm
SDK), function calling thủ công qua Policy Engine như Claude, trả lại nguyên lượt trả lời của model (Gemini
gắn *thought signature* vào đó và đòi gửi lại), `thinkingLevel` theo `effort` (Gemini 3 trở lên), token suy
nghĩ tính vào output khi ước tính chi phí. 429/5xx thử lại 3 lần rồi báo retryable; 400/401/403/404 không
retry; bị chặn vì an toàn thì dừng (không retry). Giá trong bảng `pricing` là giá tháng 10/2026; Google tăng
giá Gemini 4 Argon từ 1/12/2026 và 3.8 Flash từ 1/1/2027 (ghi chú trong file).

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

### Thông báo và duyệt từ điện thoại (Telegram, WhatsApp, Zalo)

Máy trung tâm gửi tin khi có việc cần bạn. Mỗi tin có link mở thẳng trang duyệt trên dashboard (giao diện cho
điện thoại):

| Sự kiện (`AWF_NOTIFY_EVENTS`) | Khi nào |
|---|---|
| `approval` | Có yêu cầu cần duyệt (kiến trúc, merge, …) |
| `approval_resolved` | Yêu cầu đã được duyệt/từ chối (ở bất kỳ đâu) |
| `failed`, `completed` | Run thất bại / hoàn tất |
| `worker_offline` | Một máy mất kết nối |
| `run_moved` | Run được tự động chuyển khỏi máy hỏng |

Tin được ghi vào bảng outbox cùng transaction với sự kiện, rồi gửi lại khi lỗi mạng (tối đa 8 lần, giãn dần),
riêng từng người nhận: người đã nhận không bị gửi trùng. Trang **Notifications** trên dashboard cho thấy kênh
nào đang bật, tin nào đã gửi/lỗi, và có nút **Send a test message**. Đặt `AWF_PUBLIC_URL` (URL máy trung tâm
mà điện thoại mở được, ví dụ qua Tailscale) để tin có link. Mọi biến dưới đây đặt trong `.env` của **máy
trung tâm**, rồi khởi động lại `awf`.

**Telegram (khuyến nghị: miễn phí, không cần duyệt, duyệt được ngay trong chat).**

1. Trong Telegram, nhắn `@BotFather` → `/newbot` → lấy token.
2. Đặt `AWF_TELEGRAM_BOT_TOKEN=<token>`, khởi động lại `awf`, rồi nhắn `/start` cho bot: bot trả lời
   **Chat ID** và **User ID** của bạn (thêm bot vào một group thì `/start` trong group cho Chat ID của group).
3. Đặt `AWF_TELEGRAM_CHAT_IDS=<chat id,...>` (nơi nhận tin) và `AWF_TELEGRAM_APPROVERS=<user id,...>`
   (người được bấm duyệt), khởi động lại `awf`.

Duyệt trong chat: bấm **✅ Duyệt** hoặc **❌ Từ chối**, rồi bấm nút **xác nhận** (hai lần bấm, tránh bấm nhầm).
Chỉ người trong `AWF_TELEGRAM_APPROVERS`, trong chat thuộc `AWF_TELEGRAM_CHAT_IDS`, mới duyệt được; người khác
bấm chỉ nhận thông báo "không có quyền". Người duyệt được ghi là `telegram:<username>` trong audit. Duyệt xong
(ở Telegram hay dashboard), nút biến mất ở mọi bản tin. Lệnh `/pending` liệt kê các yêu cầu đang chờ.
Không đặt `AWF_TELEGRAM_APPROVERS` thì tin chỉ có link, duyệt trên dashboard (cần đăng nhập).

Telegram nhận nút bấm bằng long-polling từ máy trung tâm, **không cần** mở cổng hay webhook. Chỉ một tiến
trình được đọc một bot: đừng dùng cùng token cho hai máy trung tâm.

**WhatsApp (WhatsApp Business Cloud API của Meta).** Cần tài khoản Meta Business, một số điện thoại dành cho
API và token hệ thống (System User, không hết hạn).

```text
AWF_WHATSAPP_TOKEN=<system user token>
AWF_WHATSAPP_PHONE_NUMBER_ID=<phone number id trong WhatsApp → API Setup>
AWF_WHATSAPP_TO=84901234567            # số nhận, dạng quốc tế, nhiều số cách nhau bằng dấu phẩy
AWF_WHATSAPP_TEMPLATE=awf_alert        # nên đặt: template đã được Meta duyệt, body có đúng một biến {{1}}
```

WhatsApp chỉ cho gửi tin tự do trong 24 giờ sau lần cuối người nhận nhắn cho số business. Ngoài khoảng đó phải
dùng template đã duyệt (Meta duyệt mất 1–2 ngày); toàn bộ nội dung tin được đưa vào biến `{{1}}`. Thiếu template
mà ngoài 24 giờ, trang Notifications báo lỗi kèm gợi ý. Duyệt: mở link trong tin.

**Zalo (Zalo Official Account).** Cần một OA đã xác thực và một ứng dụng Zalo for Developers được cấp quyền
OA (lấy `app_id`, `app_secret`, và refresh token qua bước cấp quyền OA). Người nhận phải **quan tâm** OA và đã
nhắn cho OA gần đây (tin tư vấn chỉ gửi được trong khoảng thời gian Zalo cho phép sau lần tương tác cuối).

```text
AWF_ZALO_APP_ID=<app id>
AWF_ZALO_APP_SECRET=<app secret>
AWF_ZALO_REFRESH_TOKEN=<refresh token của OA>
AWF_ZALO_USER_IDS=<user id theo OA của người nhận, cách nhau bằng dấu phẩy>
```

Access token của OA chỉ sống khoảng một ngày; factory tự làm mới bằng refresh token và lưu cặp token mới vào
database (refresh token chỉ dùng được một lần, nên giá trị trong `.env` chỉ dùng cho lần đầu). Duyệt: mở link.

Đã kiểm thử với API giả lập (không có tài khoản thật): Telegram (gửi, nút duyệt hai bước, quyền, `/start`,
`/pending`, gửi lại khi lỗi), WhatsApp (text, template, lỗi ngoài 24 giờ), Zalo (làm mới token, token bị từ chối).
Endpoint gửi tin tư vấn Zalo `https://openapi.zalo.me/v3.0/oa/message/cs` lấy từ SDK chính thức; URL làm mới
token và phiên bản Graph API của WhatsApp chưa đối chiếu được với tài khoản thật, nên chỉnh được bằng
`AWF_ZALO_OAUTH_URL`, `AWF_ZALO_API_BASE`, `AWF_WHATSAPP_API_VERSION` nếu nhà cung cấp đổi.

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

## 9b. Quy trình viết content (`content-production`)

Một run content đi trọn quy trình trên **một** máy, như run phần mềm:

```text
research -> plan -> draft -> review --(CHANGES_REQUIRED, tối đa 2 vòng)--> draft
                                    \-> edit -> seo -> publish (người duyệt)
```

| Stage | Agent | Kết quả (artifact) |
|---|---|---|
| research | `content.researcher` | `research_brief`: Audience, Key facts (F1, F2…), Sources, Open questions |
| plan | `content.strategist` | `content_plan`: Angle, Key messages, Outline, Call to action, Format |
| draft | `content.copywriter` | `content_draft`: bài hoàn chỉnh (markdown) |
| review | `content.reviewer` | `content_review` + verdict; `CHANGES_REQUIRED` gửi bài về draft kèm nhận xét |
| edit | `content.editor` | `content_final` (bản xuất bản) + `editor_notes` (Changes, Before publishing) |
| seo | `content.seo` | `seo_package` (JSON): title, meta description, slug không dấu, keywords, social post |
| publish | gate `approve_publish` | người duyệt đọc bài cuối, ghi chú của editor và gói SEO (tin Telegram/Zalo/WhatsApp kèm link) |

Cách dùng:

1. Tạo project **không cần repository**: dashboard → Projects → để trống *Repository URL*, chọn
   *Platform* = **Any machine** (`any`).
2. Bắt đầu run với workflow **Content production**. Viết brief vào *Objective*: chủ đề, người đọc, kênh
   (blog, LinkedIn, Facebook, newsletter), độ dài, giọng văn, ngôn ngữ. Ví dụ: "Viết bài blog 800 chữ giới
   thiệu tính năng X cho trưởng nhóm kỹ thuật ở doanh nghiệp vừa; giọng thân thiện; tiếng Việt."
3. Duyệt ở bước *Publish approval*. Bài cuối nằm trong artifact `content_final` của run.

Project content không có repository chạy được trên máy **không có git**: agent làm việc trong một thư mục trống,
không clone gì. Nếu project có repository (tài liệu sản phẩm, bài cũ, ghi chú), agent đọc được tài liệu tham khảo
từ đó (chỉ đọc, không ghi), và run khi đó cần máy có `git`.

Agent không có công cụ duyệt web: researcher chỉ dựa vào tài liệu trong repository của project và kiến thức của
model, phải đánh dấu "(cần kiểm chứng)" những gì không chắc, và không được bịa số liệu, trích dẫn, nguồn.
Reviewer chặn những khẳng định không có trong research brief. Mục *Before publishing* của editor liệt kê những gì
người duyệt phải tự kiểm tra.

Giọng văn và quy tắc của công ty: sửa [`knowledge/content/style-guide.md`](../knowledge/content/style-guide.md);
mọi agent content đều đọc file này. Prompt của từng agent ở `agents/prompts/content.*.md`.

Chưa có: đăng bài tự động (WordPress, Facebook…) sau khi duyệt, tìm kiếm web cho researcher, workflow video.

## 10. Worker: Windows, Linux, Jetson, GPU

```text
awf worker --url <control-api> --name <tên duy nhất> [--platform linux-arm64] [--capabilities git,python,cmake,cuda,realsense]
           [--extra-capabilities realsense] [--max-jobs 3]
           [--label device=jetson-orin --label location=lab] [--workspace-root <dir>]
```

- Platform tự phát hiện: `windows-amd64`, `linux-amd64`, `linux-arm64` (Jetson)… Job yêu cầu `platform: linux`
  khớp mọi `linux-*`.
- Capability tự phát hiện từ PATH (git, python, docker, cmake, ctest, dotnet, npm, node, nvcc→cuda, ros2), cùng
  các capability chi tiết cho .NET/Windows:

  | Capability | Nghĩa | Phát hiện từ |
  |---|---|---|
  | `dotnet-sdk-8`, `dotnet-sdk-9`, … | có .NET SDK major đó | `dotnet --list-sdks` |
  | `dotnet-windowsdesktop` | build/chạy được WPF, WinForms (chỉ có trên Windows) | `dotnet --list-runtimes` |
  | `msbuild` | MSBuild của Visual Studio (.NET Framework, C++) | `msbuild` trên PATH hoặc `vswhere` |
  | `vs-2022`, `vs-2026`, … | phiên bản Visual Studio | `vswhere` |

- Capability không dò được (thiết bị, SDK riêng): `AWF_WORKER_EXTRA_CAPABILITIES=realsense,robo-station-sdk`
  (hoặc `--extra-capabilities`) **cộng thêm** vào kết quả dò. `AWF_WORKER_CAPABILITIES` / `--capabilities`
  **thay thế** hoàn toàn kết quả dò. `awf doctor` in ra capability máy sẽ khai báo.
- Đăng ký cần `AWF_WORKER_REGISTRATION_TOKEN`; worker nhận token riêng dùng cho mọi call sau đó.
- Heartbeat mỗi 15s; quá `worker_heartbeat_timeout_seconds` (90s) → offline, job đang chạy được requeue.
- Mỗi job: clone mới vào `<workspace_root>/<job_id>`, xoá sau khi xong.
- Chạy như service: Linux dùng `sudo scripts/install-systemd.sh`, Windows dùng `.\scripts\install-task.ps1` (xem mục 21).

Yêu cầu theo stage: thêm `worker: {platform: linux, capabilities: [cuda]}` vào stage trong workflow, hoặc
`labels.platform` + `repository.required_capabilities` ở project.

### Một run — một máy (ADR-008)

Mặc định **mọi job của một run chạy trên cùng một máy**: một task phần mềm đi từ requirement tới merge trên
máy được giao.

- **Yêu cầu của run** được tính một lần khi tạo run: platform và capability của project, cộng với `worker:`
  của **mọi** stage. Stage cuối cần `cuda` thì ngay từ stage đầu run đã được giao cho máy có `cuda`. Hai stage
  đòi hai hệ điều hành khác nhau → run bị từ chối.
- **Chọn máy**: ở lần dispatch đầu tiên, orchestrator chọn máy online, đáp ứng yêu cầu, ít việc nhất
  (job đang chờ + đang chạy chia cho số slot), rồi máy giữ ít run hơn, rồi máy có nhãn
  `preferred_for=<slug project hoặc id workflow>`. Không có máy online phù hợp → run chờ, dashboard ghi lý do.
- **Chọn tay**: dashboard → project → *Machine*, hoặc API `placement: {worker: "<tên>"}` khi tạo run.
  Chuyển run sang máy khác: trang run → *Move run* (không được khi một job của run đang chạy).
- **Máy hỏng**: máy offline quá `run_reassign_after_seconds` (mặc định 600 giây,
  `AWF_RUN_REASSIGN_AFTER_SECONDS`) trong lúc run có việc đang chờ → run chuyển sang máy phù hợp khác và chạy
  tiếp từ nhánh `awf/run-*` trên origin. Máy bị **Revoke** → chuyển ngay. Máy chỉ restart → giữ nguyên run.
  Run đang chờ phê duyệt thì giữ máy cho tới khi có việc để chạy.
- **Draining**: máy draining chạy nốt các run đang giữ, không nhận run mới.
- Workflow cần nhiều máy (ví dụ build Linux rồi test Windows): đặt `placement: stage` ở đầu workflow để
  từng job được giao cho bất kỳ máy nào khớp, như trước đây.

Các phần song song trong một run (nhiều task development, verification song song) chạy cùng lúc trên máy
được giao nếu máy có nhiều slot (`AWF_WORKER_MAX_JOBS`, xem bên dưới); với 1 slot chúng chạy lần lượt.

### Nhiều job cùng lúc trên một máy

`AWF_WORKER_MAX_JOBS=3` trong `.env` của worker (hoặc `awf worker --max-jobs 3`): máy chạy tối đa 3 job cùng
lúc, mỗi job một thread và một thư mục làm việc riêng. Máy chỉ xin việc khi còn slot trống, và máy trung tâm
cũng không bao giờ giao quá số slot máy đã khai báo. Số slot được dùng khi chọn máy cho run: máy 4 slot đang có
3 job (tải 0.75) được coi là rảnh hơn máy 1 slot đang có 1 job (tải 1.0).

Chọn số slot:

- Job agent chủ yếu chờ API của model, tốn ít CPU; job build/test (`dotnet build`, `pytest`, CMake) tốn CPU,
  RAM và đĩa (mỗi job clone repo riêng).
- Gợi ý: máy văn phòng 16 GB RAM: 2; máy dev 32 GB: 3–4; Jetson: 1. Tăng dần và theo dõi tải ở trang Workers.
- Mặc định là 1 (hành vi cũ). Đổi giá trị rồi khởi động lại worker; worker đăng ký lại với số slot mới.

Dừng worker khi đang chạy nhiều job: các job dở được giao lại khi máy đăng ký lại (hoặc khi hết lease), giống
như khi máy mất điện lúc đang chạy một job.

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
`status: draft` để giữ workflow chưa đủ role (như `video-production`).

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
| POST/GET | `/projects`, `/projects/{id}` | tạo (bắt buộc `labels.platform`) / liệt kê / chi tiết (kèm `spend_usd`, `requirements`, `machines`: máy nào chạy được và lý do) |
| PATCH | `/projects/{id}` | sửa các trường gửi lên; `repository`, `labels` được thay cả khối |
| GET | `/workflows`, `/agents` | định nghĩa workflow, agent |
| GET | `/models` | nhà cung cấp model (Claude, Gemini) và model của từng class |
| POST | `/projects/{id}/workflow-runs` | `{workflow_id, inputs: {objective}, placement?: {worker}, llm?: "anthropic"\|"google"}` |
| GET | `/projects/{id}/workflow-runs`, `/workflow-runs/{id}` | run + stage overview + chi phí + máy được giao (`assigned_worker`, `requirements`, `placement.wait`) |
| POST | `/workflow-runs/{id}/reassign` | `{worker}` chuyển run sang máy đó; `{}` để orchestrator chọn lại |
| POST | `/workflow-runs/{id}/cancel` | huỷ |
| GET | `/workflow-runs/{id}/tasks`, `/tasks/{id}` | task, agent run, job, approval, artifact |
| POST | `/tasks/{id}/retry` | retry task FAILED |
| GET | `/jobs/{id}`, `/jobs/{id}/logs` | job + log worker |
| GET | `/projects/{id}/artifacts?run_id=&type=` | artifact |
| GET | `/artifacts/{id}`, `/content`, `/relations`, `/impact` | metadata, nội dung, trace, impact |
| GET | `/approvals?state=pending` | hàng đợi |
| GET | `/approvals/{id}` | một yêu cầu duyệt + project, run, artifact cần xem |
| POST | `/approvals/{id}/approve`, `/reject` | `{comment}` |
| GET | `/notifications` | kênh đang bật + các tin gần đây và trạng thái gửi |
| POST | `/notifications/test` | xếp hàng một tin thử tới mọi kênh |
| GET | `/workers`, `/overview`, `/stats/agents`, `/audit` | quan sát |
| POST | `/workers/register`, `/workers/{id}/heartbeat`, `/workers/{id}/claim` | giao thức worker |
| POST | `/jobs/{id}/progress`, `/log`, `/complete`, `/fail` | giao thức worker |

## 18. Kiểm thử nền tảng

```powershell
.\.venv\Scripts\python -m pytest -q -p no:logging            # toàn bộ (171 test)
.\.venv\Scripts\python -m pytest tests/unit -q                # state machine, workflow, policy, router, critic, redaction
.\.venv\Scripts\python -m pytest tests/integration -q         # giao thức worker, auth, routing, các kịch bản lỗi
.\.venv\Scripts\python -m pytest tests/e2e -q                 # workflow đầy đủ trên git repo thật + fix loop
```

Chạy cả bộ test trên PostgreSQL (database **dùng để thử, sẽ bị xoá sạch** ở mỗi test):

```bash
AWF_TEST_DATABASE_URL=postgresql+psycopg://awf:<mật khẩu>@127.0.0.1:5432/awf_test \
  .venv/bin/python -m pytest -q -p no:logging --ignore=tests/unit/test_db_migrations.py
```

`tests/integration/test_postgres_ops.py` (migration, import SQLite, `pg_dump`/`pg_restore`) chỉ chạy khi có biến
này. CI chạy cả bộ trên service `postgres:16`.

Các kịch bản lỗi được kiểm (Design §20): worker offline, model timeout, output sai cấu trúc, build fail,
test fail, critic block, approval reject, duplicate event, cancel, retry thủ công, project restricted.

## 19. Xử lý sự cố

| Triệu chứng | Nguyên nhân / cách xử lý |
|---|---|
| Task `READY` mãi, run ghi "waiting for an online worker ..." | Không máy online nào đáp ứng yêu cầu của run. Bật máy phù hợp, hoặc trang run → *Move run*. |
| Task `WAITING` mãi | Máy được giao chưa nhận job (offline, đang bận job khác). Xem trang run → *Machine*; xem Workers. |
| Tạo run lỗi `no registered worker meets ...` | Không máy nào đã đăng ký có platform/capability project yêu cầu; lỗi ghi rõ từng máy thiếu gì. Xem trang project → **Where it runs**. |
| Tạo run lỗi `does not say which platform it runs on` | Project cũ chưa khai báo platform: chọn trên trang project hoặc `PATCH /projects/{id}`. |
| Máy có .NET nhưng thiếu `dotnet-windowsdesktop`/`msbuild` | Máy không phải Windows, hoặc chưa cài Visual Studio/workload desktop. Chạy `awf doctor` trên máy đó để xem capability. |
| `executable 'x' is not in the command allowlist` | Thêm vào `command_allowlist` trong `config/policies.yaml`. |
| Developer fail `MISSING_ACCEPTANCE_EVIDENCE` | Agent không chạy `build.run`/`test.run`, hoặc lệnh chưa cấu hình. |
| `COMPLETION_RULE_FAILED` | Build/test thật sự fail — xem log job và `test_evidence`. |
| `NO_CHANGES` | Developer không tạo thay đổi nào (thường do `fs.replace` không khớp). |
| `critic blocked: ... credential-like material` | Diff/output chứa secret. Gỡ secret khỏi repo/thay đổi, dùng biến môi trường. |
| `INTEGRATION_CONFLICT` | Hai task song song sửa cùng chỗ; Planner nên đặt `depends_on`. |
| `no model available for class LOCAL_PRIVATE` | Project `restricted`; chưa có model cục bộ. |
| `model provider rejected the request` | Thiếu/sai `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` trên worker. |
| Tạo run lỗi `missing llm-google` (hoặc `llm-anthropic`) | Không máy nào có key của nhà cung cấp đó: đặt key trong `.env` của worker rồi khởi động lại worker. |
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
4. **Phase 4:** content workflow đã có (mục 9b); còn đăng bài tự động, tìm kiếm web, workflow video (draft).
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

Tailscale nối mọi máy của bạn (máy chủ Ubuntu, PC Windows, máy Linux, Jetson, điện thoại) vào một mạng riêng
mã hoá WireGuard, ở bất kỳ đâu (nhà, công ty, 4G). Không cần domain, không mở cổng nào ra internet, không cần
IP tĩnh; dashboard chỉ những thiết bị trong mạng riêng của bạn (tailnet) mới thấy.

```text
  Điện thoại (app Tailscale) ─┐
  PC Windows (worker)  ───────┤   tailnet (WireGuard)   ┌─ máy chủ Ubuntu 24.04
  Máy Linux/Jetson (worker) ──┘ ───────────────────────►│  tailscale serve :443 → 127.0.0.1:8000 (awf)
                                                         └─ https://factory.<tailnet>.ts.net
```

Bước 1 — Tạo tailnet và bật HTTPS (một lần, trên trình duyệt): đăng ký tại tailscale.com (Google/Microsoft/
GitHub account; gói miễn phí cá nhân thường đủ cho vài người và vài chục máy, xem tailscale.com/pricing). Trong trang quản trị → **DNS**: bật
**MagicDNS** và **HTTPS Certificates**.

Bước 2 — Máy chủ Ubuntu:

```bash
curl -fsSL https://tailscale.com/install.sh | sh     # bộ cài chính thức, tự bật dịch vụ tailscaled khi khởi động
sudo tailscale up --hostname=factory                 # mở link in ra để đăng nhập vào tailnet
sudo tailscale serve --bg 8000                       # HTTPS https://factory.<tailnet>.ts.net -> 127.0.0.1:8000
tailscale serve status                               # in ra URL đầy đủ
```

`--bg` giữ cấu hình qua các lần khởi động lại. Sau đó chạy bootstrap với URL vừa in ra (HTTPS, nên API chỉ nghe
`127.0.0.1`; không ai vào được cổng 8000 từ mạng, kể cả LAN):

```bash
scripts/bootstrap.sh --role control-plane --public-url https://factory.<tailnet>.ts.net
sudo scripts/install-postgres.sh --import-sqlite
sudo scripts/install-systemd.sh
```

Bootstrap in ra lệnh cài sẵn cho worker (kèm `--registration-token`).

Bước 3 — Mỗi PC Windows làm worker: cài Tailscale từ tailscale.com/download (chạy nền như dịch vụ Windows, tự bật
cùng máy), đăng nhập cùng tài khoản. Rồi:

```powershell
.\scripts\bootstrap.ps1 -Role worker -ControlUrl https://factory.<tailnet>.ts.net -RegistrationToken <token>
.\scripts\install-task.ps1 -AtStartup   # PowerShell "Run as administrator": chạy từ lúc bật máy, không cần đăng nhập
```

Bước 4 — Mỗi máy Linux/Jetson làm worker:

```bash
curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
scripts/bootstrap.sh --role worker --control-url https://factory.<tailnet>.ts.net --registration-token <token>
sudo scripts/install-systemd.sh
```

Bước 5 — Điện thoại: cài app **Tailscale** (iOS/Android), đăng nhập cùng tài khoản, bật VPN. Mở
`https://factory.<tailnet>.ts.net` trong trình duyệt; đăng nhập dashboard một lần (giữ 30 ngày). Đặt
`AWF_PUBLIC_URL` là URL này (bootstrap đã làm), thì link trong tin Telegram/Zalo/WhatsApp mở thẳng được trên
điện thoại.

Kiểm tra:

| Ở đâu | Lệnh | Mong đợi |
|---|---|---|
| mọi máy | `tailscale status` | thấy tên các máy khác của tailnet |
| worker | `tailscale ping factory` | `pong from factory (100.x.y.z)` |
| worker | `awf doctor` | `[OK] tailscale this machine is ...` và `[OK] control plane https://factory... -> ok` |
| máy chủ | `awf doctor` | `[OK] tailscale this machine is factory.<tailnet>.ts.net` |
| máy chủ | `tailscale serve status` | liệt kê `https://factory.<tailnet>.ts.net` (tailnet only) → `http://127.0.0.1:8000` |

Thêm (tùy chọn):

- **Quản trị từ xa không cần mở SSH ra ngoài:** `sudo tailscale up --ssh` trên máy chủ, rồi từ máy bạn
  `ssh <user>@factory`.
- **Giới hạn ai vào được máy chủ:** trang quản trị → **Access controls**, ví dụ chỉ cho phép vào cổng 443 của
  máy chủ (gắn tag `tag:factory`) từ tài khoản của bạn và các worker:
  ```json
  {"tagOwners": {"tag:factory": ["autogroup:admin"], "tag:worker": ["autogroup:admin"]},
   "grants": [{"src": ["autogroup:member", "tag:worker"], "dst": ["tag:factory"], "ip": ["443"]}]}
  ```
  Gắn tag cho máy chủ bằng `sudo tailscale up --advertise-tags=tag:factory` (và `tag:worker` cho worker).
- **Máy tắt key-expiry:** trang quản trị → Machines → máy chủ và các worker → *Disable key expiry*, để máy chạy
  24/7 không bị đăng xuất khỏi tailnet sau 180 ngày.
- Không dùng `tailscale serve`: đặt `AWF_API_HOST` = IP Tailscale của máy chủ (`tailscale ip -4`) và
  `--public-url http://factory.<tailnet>.ts.net:8000`. Worker chấp nhận `http://` tới `*.ts.net`/`100.x.y.z` vì
  đường truyền đã được WireGuard mã hoá, nhưng trình duyệt điện thoại sẽ báo "không bảo mật".

Sự cố thường gặp:

| Triệu chứng | Cách xử lý |
|---|---|
| `tailscale serve` báo cần bật HTTPS | Trang quản trị → DNS → bật *HTTPS Certificates* rồi chạy lại. |
| Worker `doctor`: `tailscale ... FAIL` | Máy chưa cài/chưa `tailscale up`, hoặc đăng nhập tài khoản khác. |
| Điện thoại không mở được URL | App Tailscale trên điện thoại chưa bật VPN, hoặc ACL chặn. |
| Tên `factory` không phân giải | Bật MagicDNS; trên Linux kiểm tra `tailscale up --accept-dns`. |

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

- Dashboard → **Workers**: tên, `machine_id`, platform, capability, nhãn, số run đang giữ, heartbeat.
- **Revoke** một máy bị mất hoặc nghi bị xâm nhập. Token của nó vô hiệu ngay, các run của nó chuyển sang máy
  phù hợp khác, và nó không đăng ký lại được.
- Nếu máy bị xâm nhập, đổi luôn `AWF_WORKER_REGISTRATION_TOKEN` trên máy trung tâm (`awf env init --force`
  tạo mới toàn bộ token, nên cần cài lại các worker còn lại). **Delete** bản ghi worker đã revoke để máy
  đó tham gia lại.
- Nhãn để định tuyến theo địa điểm: `AWF_WORKER_LABELS=site=hanoi,gpu=rtx4090`. Ưu tiên một máy cho một
  project: `AWF_WORKER_LABELS=preferred_for=robo-station`.

### Máy trung tâm Ubuntu 24.04 với PostgreSQL (khuyến nghị cho máy chạy 24/7)

Mọi máy trong factory dùng chung một database, nằm trên máy trung tâm. Worker **không** kết nối database: mọi
thứ đi qua API của máy trung tâm. SQLite đủ để bắt đầu. Máy trung tâm chạy 24/7 cho nhiều máy thì nên dùng
PostgreSQL: chịu ghi đồng thời tốt hơn, sao lưu bằng `pg_dump` khi đang chạy, công cụ quản trị chuẩn.

Trên máy trung tâm, theo thứ tự:

```bash
git clone <repo-url> ai-work-factory && cd ai-work-factory
scripts/bootstrap.sh --role control-plane --public-url <url>   # như mục "Mô hình" ở trên
sudo scripts/install-postgres.sh --import-sqlite                 # PostgreSQL + chép dữ liệu SQLite cũ (nếu có)
sudo scripts/install-systemd.sh                                  # chạy 24/7, tự bật khi khởi động, backup hằng đêm
```

`install-postgres.sh` (chạy lại nhiều lần vẫn an toàn):

1. Cài `postgresql` (bản 16 trên Ubuntu 24.04), bật cùng máy. Chỉ nghe ở `localhost`.
2. Tạo role `awf` với mật khẩu ngẫu nhiên và database `awf`.
3. Ghi `DATABASE_URL` vào `.env` (bản cũ giữ ở `.env.bak-<thời điểm>`), chạy `awf db upgrade`.
4. Với `--import-sqlite`: dừng `awf` nếu đang chạy, chép toàn bộ dữ liệu từ `var/awf.db` sang PostgreSQL trong
   một transaction (lỗi giữa chừng thì không ghi gì), rồi chạy lại `awf`. File SQLite được giữ nguyên; xoá khi
   bạn đã yên tâm.

`awf.service` khởi động sau `postgresql.service`. `awf-backup.timer` sao lưu lúc 02:30 mỗi đêm bằng `pg_dump`
(định dạng custom, nén) vào `var/backups/awf-*.dump`, giữ 14 bản.

| Việc | Lệnh |
|---|---|
| Sao lưu ngay | `.venv/bin/awf db backup` |
| Khôi phục từ bản sao lưu | `sudo systemctl stop awf && .venv/bin/awf db restore var/backups/<file>.dump --yes && sudo systemctl start awf` |
| Chép dữ liệu SQLite vào PostgreSQL (database mới, trống) | `.venv/bin/awf db import-sqlite var/awf.db` |
| Mở psql | `sudo -u postgres psql awf` |
| Kiểm tra | `.venv/bin/awf doctor` (dòng `database` và `backups`) |

`awf db restore` thay **toàn bộ** nội dung database bằng bản sao lưu, nên đòi `--yes` và phải dừng `awf` trước.
Nếu bản sao lưu cũ hơn code hiện tại, `awf start` tự chạy migration khi khởi động.

Nên chép `var/backups/` và `var/artifacts/` sang máy khác (rsync, ổ mạng) định kỳ: bản sao lưu nằm cùng ổ đĩa
với database không cứu được khi hỏng ổ.

### Chạy như dịch vụ (tự bật khi khởi động máy)

Tóm tắt cho từng loại máy (cùng một lệnh dùng cho máy chủ và worker; `AWF_ROLE` trong `.env` quyết định máy đó làm
gì):

| Máy | Cách tự chạy | Lệnh cài | Khi nào chạy |
|---|---|---|---|
| Máy chủ Ubuntu (control-plane) | systemd `awf.service` + `awf-backup.timer` | `sudo scripts/install-systemd.sh` | ngay khi bật máy, không cần đăng nhập |
| Worker Linux / Jetson | systemd `awf.service` | `sudo scripts/install-systemd.sh` | ngay khi bật máy, không cần đăng nhập |
| Worker Windows (máy bạn dùng hằng ngày) | Task Scheduler (ẩn, watchdog 5 phút) | `.\scripts\install-task.ps1` | khi bạn **đăng nhập** Windows |
| Worker Windows chạy 24/7, không ai đăng nhập | Task Scheduler, chạy khi Windows khởi động | `.\scripts\install-task.ps1 -AtStartup` (PowerShell **Run as administrator**) | ngay khi Windows khởi động (sau 1 phút), không cần đăng nhập |
| Tailscale (mọi máy) | dịch vụ của chính Tailscale | cài là xong | ngay khi bật máy |

**Linux (Ubuntu/Debian/Jetson) — systemd.** Sau khi đã chạy bootstrap:

```bash
sudo scripts/install-systemd.sh          # cài, bật khi khởi động, chạy ngay
```

- `awf.service` chạy `awf start` bằng user sở hữu thư mục repo (không chạy bằng root). Vai trò vẫn lấy từ
  `.env`, nên cùng một lệnh dùng được cho máy trung tâm và máy worker Linux.
- Tự chạy lại sau 5 giây nếu tiến trình chết. Dừng nhẹ nhàng khi tắt máy.
- Máy có database (`control-plane`/`all`) được cài thêm `awf-backup.timer`: sao lưu lúc 02:30 mỗi đêm vào
  `var/backups/` (SQLite: bản sao `.db`; PostgreSQL: `pg_dump` `.dump`), giữ 14 bản. Nếu máy tắt lúc 02:30 thì
  timer chạy bù khi bật lại.
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
  bạn, nên dùng được credential git và `.env` của bạn. Log ghi vào `var\logs\awf.log`, xoay vòng ở 10 MB
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
  | Xem log trực tiếp | `Get-Content var\logs\awf.log -Wait` |
  | Tạm tắt, kể cả watchdog (bật lại bằng `-Start`) | `.\scripts\install-task.ps1 -Stop` |
  | Gỡ | `.\scripts\install-task.ps1 -Uninstall` |
- Mặc định tác vụ chỉ chạy khi bạn **đã đăng nhập** Windows; khoá màn hình thì không sao. Nếu muốn PC làm việc
  lúc bạn vắng mặt, đặt chế độ sleep thành *Never* (Settings → System → Power).

**PC Windows làm worker 24/7 (chạy từ lúc bật máy, không cần ai đăng nhập).** Mở PowerShell bằng **Run as
administrator** (bằng chính tài khoản Windows sẽ chạy worker, tài khoản đó phải là admin), rồi:

```powershell
cd D:\src\ai-work-factory
.\scripts\install-task.ps1 -AtStartup
```

- Windows hiện hộp thoại hỏi **mật khẩu** của tài khoản (mật khẩu Windows/Microsoft account, **không phải PIN**).
  Mật khẩu đi thẳng từ hộp thoại vào Task Scheduler (Windows tự mã hoá và lưu); script không ghi hay in ra.
- Worker chạy với tài khoản của bạn nên vẫn dùng được `.env`, SSH key và Git Credential Manager như khi bạn
  đăng nhập. Chạy ở chế độ nền (không có cửa sổ), chờ mạng sẵn sàng và khởi động sau Windows 1 phút để
  Tailscale kịp kết nối; mất mạng lúc khởi động thì `--supervise` thử lại.
- Đổi mật khẩu Windows → chạy lại `.\scripts\install-task.ps1 -AtStartup`, nếu không tác vụ không chạy được.
- Không muốn lưu mật khẩu: `.\scripts\install-task.ps1 -AtStartup -S4U`. Khi đó Windows không cho tác vụ dùng
  các credential đã lưu (Git Credential Manager, ổ mạng): chỉ dùng được khi mọi repository clone bằng **SSH key**.
- Để PC tự hoạt động lại sau mất điện: BIOS/UEFI → *Restore on AC power loss* = **On** (tên tùy hãng), và sleep =
  *Never*.
- `-Status`, `-Restart`, `-Stop`, `-Start`, `-Uninstall` dùng như trên (với tác vụ `-AtStartup`, chạy chúng trong
  PowerShell admin). Quay về chế độ "khi đăng nhập": `.\scripts\install-task.ps1` (không có `-AtStartup`).

Đã thử trên Windows 10:

- cài đặt;
- chạy ẩn;
- worker đăng ký với máy trung tâm Linux và chạy trọn một workflow 13 job;
- `-Restart`;
- giết tiến trình → watchdog bật lại;
- `-Stop` → watchdog **không** bật lại;
- `-Start`;
- `-Uninstall` không để sót tiến trình.

Riêng trigger "khi đăng nhập" chưa thử bằng một lần đăng xuất/đăng nhập thật. Chế độ `-AtStartup` chưa đăng ký
thật trên máy nào (cần mật khẩu và quyền admin của bạn): đã kiểm tra cú pháp script, các đối tượng Task
Scheduler nó tạo, và việc từ chối khi chưa chạy bằng admin; hãy chạy thử một lần rồi khởi động lại máy để xác
nhận (`-Status` phải báo `starts at Windows startup ... (Password)` và log có dòng mới).

### Sao lưu (chỉ máy trung tâm)

```powershell
.\.venv\Scripts\awf db backup --keep 14     # SQLite: bản sao nhất quán; PostgreSQL: pg_dump -> var\backups\
```

Linux: `install-systemd.sh` đã lên lịch hằng đêm. Windows: lên lịch bằng Task Scheduler. Sao lưu cả thư mục
artifact (`var\artifacts`). Khôi phục PostgreSQL: `awf db restore <file>.dump --yes` (xem mục PostgreSQL ở trên).
Khi đổi máy trung tâm, chép `.env`, `var\` (và bản `.dump` mới nhất, khôi phục bằng `awf db restore`) sang máy
mới và trỏ DNS/Tailscale name sang. Worker không phải cài lại.

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
- **Database:** một máy trung tâm có một database; worker không bao giờ kết nối database. SQLite đủ để bắt
  đầu; máy trung tâm 24/7 nên dùng PostgreSQL (`sudo scripts/install-postgres.sh --import-sqlite`, xem mục
  PostgreSQL ở trên).

### Cách đã kiểm chứng

- Clone sạch → bootstrap → `doctor` OK → toàn bộ test pass trên Windows và Linux (WSL Ubuntu), và trên CI
  GitHub Actions (`ubuntu-latest`, `windows-latest`).
- Phân tán: máy trung tâm Linux, worker Windows. Workflow chạy tới `COMPLETED`; tắt máy trung tâm giữa lúc chạy
  job vẫn không mất kết quả.
- Nâng cấp tại chỗ một database đang có dữ liệu từ migration `0001` lên `0002` sau `git pull`.
- Đăng nhập dashboard bằng `awf open`, phiên vẫn còn sau khi khởi động lại server, đăng xuất: kiểm tra trên
  trình duyệt thật.

**Chưa kiểm chứng trên máy này:** Tailscale (các lệnh theo tài liệu chính thức của Tailscale; `awf doctor`
kiểm tra được trạng thái), Caddy/Let's Encrypt và Cloudflare Tunnel, vì máy chưa cài các
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
