# AI WORK FACTORY — HƯỚNG DẪN SỬ DỤNG CHI TIẾT

**Phiên bản:** 0.1 (Phase 0 → đầu Phase 1)
**Đối tượng:** Admin / Product Owner, developer tích hợp repo, người viết workflow và agent.

Mục lục

1. [Factory làm gì](#1-factory-làm-gì)
2. [Khái niệm cốt lõi](#2-khái-niệm-cốt-lõi)
3. [Cài đặt](#3-cài-đặt)
   - 3b. [Lần đầu: dựng máy trung tâm và worker (từng thao tác)](#3b-lần-đầu-dựng-máy-trung-tâm-và-worker-từng-thao-tác)
4. [Chạy demo trong 1 phút](#4-chạy-demo-trong-1-phút)
5. [Đưa một repo thật vào factory (từng bước)](#5-đưa-một-repo-thật-vào-factory-từng-bước)
6. [Model thật: Claude, Gemini, ChatGPT, Qwen, Kimi, DeepSeek, MiniMax](#6-model-thật-claude-gemini-chatgpt-qwen-kimi-deepseek-minimax)
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
22. [Câu hỏi thường gặp](#22-câu-hỏi-thường-gặp)

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
Chạy nhiều máy: làm theo [mục 3b](#3b-lần-đầu-dựng-máy-trung-tâm-và-worker-từng-thao-tác); chi tiết ở
[mục 21](#21-chạy-trên-nhiều-máy-cùng-source-cùng-lệnh).

## 3b. Lần đầu: dựng máy trung tâm và worker (từng thao tác)

Danh sách việc cần làm theo đúng thứ tự, từ máy trống tới run đầu tiên. Làm xong bước nào thì kiểm tra ngay ở
cột "Đúng khi" rồi mới sang bước sau. Phần giải thích nằm ở mục 21.

**Quy ước:** *máy trung tâm* = máy chạy `control-plane` (API, dashboard, orchestrator, database). *Worker* = máy
chạy job (build/test, gọi model). Có thể có một hoặc nhiều worker. Máy trung tâm **luôn làm trước**, và phải đang
chạy khi bạn cài worker.

`awf` trong bảng là lệnh trong virtualenv: Windows `.\.venv\Scripts\awf`, Linux `.venv/bin/awf`.

### Bước 0 — Chọn cách các máy nhìn thấy nhau

| Trường hợp | Chọn | URL máy trung tâm (gọi là `<URL>` bên dưới) |
|---|---|---|
| Các máy ở nhiều địa điểm, hoặc muốn xem dashboard trên điện thoại | **Tailscale** (khuyến nghị) | `https://factory.<tailnet>.ts.net` |
| Mọi máy cùng một mạng LAN, chỉ để thử | LAN | `http://<tên-máy-hoặc-IP-LAN>:8000` |
| Worker không cài được VPN (CI, máy đối tác) | Domain + HTTPS | `https://factory.example.com` (mục 21, Cách B) |

Với Tailscale: tạo tailnet và bật **MagicDNS** + **HTTPS Certificates** trước (mục 21, Cách A, Bước 1).

### Bước 1 — Chuẩn bị mọi máy (máy trung tâm và từng worker)

| Việc | Windows | Ubuntu / Jetson |
|---|---|---|
| Git | `winget install Git.Git` | `sudo apt install -y git` |
| Python 3.12 | `winget install Python.Python.3.12` | `sudo apt install -y python3.12 python3.12-venv` |
| Tailscale (nếu chọn) | cài từ tailscale.com/download, đăng nhập | `curl -fsSL https://tailscale.com/install.sh \| sh && sudo tailscale up` |
| Lấy source | `git clone <repo-url> D:\src\ai-work-factory` | `git clone <repo-url> ~/ai-work-factory` |

Chỉ trên worker: cài toolchain của các repo sẽ chạy (dotnet, cmake, node...) và cấu hình credential git để
worker clone/push được repo (SSH key hoặc Git Credential Manager). Thử `git clone <repo của project>` một lần
bằng tay trên worker.

### Bước 2 — Máy trung tâm

Chạy trong thư mục repo, theo thứ tự:

| # | Thao tác | Đúng khi |
|---|---|---|
| 2.1 | (Tailscale) `sudo tailscale up --hostname=factory` rồi `sudo tailscale serve --bg 8000` | `tailscale serve status` in ra `https://factory.<tailnet>.ts.net` |
| 2.2 | Linux: `scripts/bootstrap.sh --role control-plane --public-url <URL>`<br>Windows: `.\scripts\bootstrap.ps1 -Role control-plane -PublicUrl <URL>` | in ra `wrote .env (role=control-plane)` và **hai dòng lệnh cho worker**. Nếu in `.env already exists ... keeping it` thì `.env` cũ được giữ nguyên, kể cả URL cũ: xem lưu ý bên dưới |
| 2.3 | **Chép lại lệnh cho worker** vừa in ra (có `-RegistrationToken`). Giữ bí mật | — |
| 2.4 | (Ubuntu 24/7, khuyến nghị) `sudo scripts/install-postgres.sh --import-sqlite` | `awf doctor`: dòng `database` là PostgreSQL |
| 2.5 | Chạy thử bằng tay: `.venv/bin/awf start` (Windows: `.\.venv\Scripts\awf start`) | in `awf start: role=control-plane` và `Dashboard: <URL>/` |
| 2.6 | Mở dashboard: `awf open` trên máy này, hoặc mở `<URL>` ở trình duyệt khác và nhập `AWF_API_TOKEN` (trong `.env`) | thấy dashboard; trang **Workers** đang trống |

Lưu ý:

- Mạng LAN không có Tailscale: bỏ 2.1, bỏ `--public-url`. API nghe `0.0.0.0:8000`; lệnh cho worker in ra dùng
  `http://<hostname>:8000`. Nếu worker không phân giải được hostname, thay bằng IP LAN. Mở cổng 8000 trên tường
  lửa của máy trung tâm (Windows: `New-NetFirewallRule -DisplayName awf -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private`
  trong PowerShell admin; Ubuntu có ufw: `sudo ufw allow from 192.168.0.0/16 to any port 8000 proto tcp`).
- Mất lệnh cho worker: token nằm ở dòng `AWF_WORKER_REGISTRATION_TOKEN=` trong `.env` của máy trung tâm.
- `.env` đã có từ lần cài trước (log có `.env already exists ... keeping it`): bootstrap **không bao giờ ghi đè**
  `.env`, nên mọi tham số (`--role`, `--public-url`) bị bỏ qua và **không in lệnh cho worker**. Xem dòng
  `public url` trong kết quả `awf doctor`: nếu chưa phải `<URL>` thì tạo lại `.env`:
  `cp .env .env.old && .venv/bin/awf env init --role control-plane --public-url <URL> --force`.
  `--force` sinh **token mới**: worker đã cài bằng token cũ phải cài lại (Bước 3). Chép lại vào `.env` mới những
  dòng bạn đã tự thêm (key Telegram, `DATABASE_URL` PostgreSQL...).
- Để 2.5 chạy tiếp ở cửa sổ đó. Chỉ cài chạy tự động (Bước 5) khi mọi thứ đã chạy đúng bằng tay.

### Bước 3 — Mỗi worker

Máy trung tâm phải **đang chạy** (2.5). Trên từng worker, trong thư mục repo:

| # | Thao tác | Đúng khi |
|---|---|---|
| 3.1 | (Tailscale) `tailscale ping factory` | `pong from factory (100.x.y.z)` |
| 3.2 | Dán lệnh đã chép ở 2.3:<br>Windows: `.\scripts\bootstrap.ps1 -Role worker -ControlUrl <URL> -RegistrationToken <token>`<br>Linux: `scripts/bootstrap.sh --role worker --control-url <URL> --registration-token <token>` | `wrote .env (role=worker)`; doctor có `[OK] control plane <URL> -> ok` |
| 3.3 | Mở `.env` của worker, thêm key model: `ANTHROPIC_API_KEY=...` (và/hoặc `GEMINI_API_KEY=...`). Bỏ qua nếu chỉ chạy demo offline | — |
| 3.4 | (Tùy chọn) đặt trong `.env`: `AWF_WORKER_NAME` (mặc định = hostname, phải khác nhau giữa các máy), `AWF_WORKER_MAX_JOBS`, `AWF_WORKER_LABELS`, `AWF_WORKER_EXTRA_CAPABILITIES` (mục 10) | — |
| 3.5 | `awf doctor` | `result: OK`; dòng capability liệt kê đúng toolchain đã cài (dotnet, cmake...) |
| 3.6 | Chạy thử bằng tay: `awf start` | in `awf start: role=worker`, không có `registration refused` |
| 3.7 | Trên dashboard của máy trung tâm → **Workers** | máy này **online**, đúng platform (`windows-amd64`, `linux-arm64`...) và capability |

Lỗi hay gặp ở bước này:

| Thông báo | Xử lý |
|---|---|
| `role 'worker' needs --control-url and --registration-token` | Thiếu tham số ở 3.2. |
| `control plane ... not reachable` | Máy trung tâm chưa chạy, sai URL, tường lửa, hoặc Tailscale chưa `up`. |
| `worker registration refused` (401/403) | Sai token, hoặc máy đã bị Revoke. Sửa `AWF_WORKER_REGISTRATION_TOKEN` trong `.env` của worker. |
| `worker name ... is in use` (409) | Trùng tên với máy khác: đặt `AWF_WORKER_NAME`. |
| `... is a public address over plain HTTP` | Dùng `https://`, Tailscale hoặc IP LAN. |

Sửa `.env` của worker thì chỉ cần dừng (`Ctrl+C`) và `awf start` lại, không phải chạy lại bootstrap.

`.env` của worker **không có** `AWF_API_TOKEN`, đó là đúng thiết kế. Đăng nhập dashboard từ bất kỳ máy nào cũng
dùng `AWF_API_TOKEN` của **máy trung tâm** (mục 22).

### Bước 4 — Run đầu tiên

| # | Thao tác | Đúng khi |
|---|---|---|
| 4.1 | Dashboard → **Projects → New project**: URL repo (git remote mà **worker** truy cập được), lệnh build/test, **Where it runs** (`windows`, `linux`... khớp với worker) | trang project liệt kê worker của bạn ở nhóm chạy được |
| 4.2 | Trên trang project → bắt đầu run `software-development` với objective cụ thể | run chuyển sang RUNNING, trang run ghi *Machine* = worker của bạn |
| 4.3 | **Approvals**: duyệt `approve_architecture`, sau đó `approve_merge` | run `COMPLETED`; nhánh `awf/run-<id>` có trên origin (mục 9) |

Chi tiết từng trường của project: mục 5, Bước 1–6. Muốn kiểm tra đường ống trước khi tốn tiền model: chạy
`awf demo --auto-approve` ở một máy riêng (vai trò `all`). Không có repo: dùng workflow `content-production`
(mục 9b).

### Bước 5 — Cho các máy tự chạy khi bật máy

Chỉ làm sau khi Bước 2–4 đã chạy đúng bằng tay. Dừng `awf start` đang chạy ở cửa sổ terminal trước.

| Máy | Lệnh | Kiểm tra |
|---|---|---|
| Máy trung tâm / worker Linux, Jetson | `sudo scripts/install-systemd.sh` | `systemctl status awf` |
| Worker Windows dùng hằng ngày | `.\scripts\install-task.ps1` | `.\scripts\install-task.ps1 -Status` |
| Worker Windows 24/7, không ai đăng nhập | `.\scripts\install-task.ps1 -AtStartup` (PowerShell admin) | `-Status` báo `starts at Windows startup` |

Khởi động lại một máy để xác nhận: worker phải tự **online** lại trên trang **Workers** trong vòng 1–2 phút.

### Thêm worker sau này / cập nhật

- Thêm worker: lặp lại Bước 1 và Bước 3 trên máy mới với cùng lệnh ở 2.3.
- Cập nhật: `git pull` → bootstrap (không cần tham số; `.env` được giữ) → khởi động lại `awf`. Máy trung tâm
  trước, worker sau (mục 21, "Cập nhật mọi máy").

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

**Cách khuyến nghị: repo chung trên máy trung tâm** (mục 5a ngay dưới). PC của bạn và mọi worker dùng chung một
repo như một team. Các bước 1–6 phía sau là cách cũ: dùng repo ở nơi khác (GitHub, server git, thư mục), và vẫn
dùng được.

### 5a. Repo chung trên máy trung tâm (làm việc như một team)

Máy trung tâm giữ một **bare repo** cho mỗi project, phục vụ git qua HTTPS, cùng địa chỉ với dashboard
(`https://<máy trung tâm>/git/<project>.git`). PC của bạn thêm repo đó làm remote `factory`. Worker ở mọi máy
clone từ đó và đẩy nhánh của chúng lên đó (ADR-016, ADR-017).

**Hai nhánh chính** (mặc định cho project mới):

| Nhánh | Ý nghĩa | Đổi khi nào |
|---|---|---|
| `develop` (nhánh chính của run, *base branch*) | Nơi run làm việc | Sau khi bạn duyệt `approve_merge` của một run |
| `main` (nhánh release) | Code đã hoàn thành thật sự | Chỉ khi bạn bấm **Release develop → main** |

| Ai | Được làm gì |
|---|---|
| Bạn (PC) | fetch, pull, push mọi nhánh, kể cả `main` và `develop` |
| Worker | chỉ ghi nhánh `awf/*`. Chỉ ghi `develop` trong **job merge**, sau khi bạn duyệt `approve_merge`. Không bao giờ ghi `main` |

**Tạo project:**

1. Dashboard → **Projects → New project** → **Repository** = *Repo mới trên máy trung tâm* (hoặc *Chép vào máy
   trung tâm từ một URL hoặc đường dẫn*: bản sao có sẵn toàn bộ nhánh, kể cả `main`). Giữ dấu chọn *Run làm việc
   trên develop; main chỉ đổi khi bạn release*.
2. Mở trang project, mục **Git remote**: ở đó có URL remote, token đăng nhập (nút *Hiện* / *Sao chép*) và lệnh.
3. Trong repo trên PC của bạn, làm một lần:
   ```powershell
   git remote add factory https://<máy trung tâm>/git/<project>.git
   git push factory main
   ```
   Lần đầu git hỏi đăng nhập: **tên người dùng tuỳ ý**, **mật khẩu là token** ở mục Git remote. Credential
   Manager của Git for Windows lưu lại, nên không phải nhập lại. Token là `AWF_GIT_TOKEN` trong `.env` của máy
   trung tâm; nếu không đặt, máy trung tâm tự sinh một token và lưu trong `var/git/.token`.
4. Run chỉ bắt đầu được sau lần push `main` đầu tiên. Trước đó, nút Start báo "push your code first". Nếu chưa có
   `develop`, run đầu tiên tự tạo `develop` từ `main`.

**Project cũ (repo ở GitHub hay thư mục):** không cần tạo lại. Mở trang project, mục **Repo chung** → **Chuyển lên
máy trung tâm**. Máy trung tâm chép repo từ URL cũ (để trống ô này nếu muốn tạo repo trống rồi push từ PC). Các
cài đặt khác giữ nguyên. Các mặc định mới (engine, phạm vi engine, độ khó) sửa ở mục **Chạy ở đâu**; build, test
và ngân sách sửa ở mục **Cài đặt**. Run đã chạy vẫn giữ cấu hình lúc nó bắt đầu.

**Project đang dùng một nhánh `main` (tạo trước 29/10/2026):** mở **Cài đặt**, đặt *Nhánh chính (base)* =
`develop` và *Nhánh release* = `main`, rồi **Lưu cài đặt**. Run tiếp theo tự tạo `develop` từ `main`.

**Tạo lại project trùng tên:** project đã có run thì chỉ **lưu trữ** được, không xoá được (để giữ lịch sử), và nó
vẫn giữ slug. Project mới cùng tên sẽ nhận slug kế tiếp (`internal-file-server-2`). Muốn dùng lại chính project
cũ: mở danh sách *Đã lưu trữ* ở trang Projects → **Khôi phục**, rồi sửa cài đặt.

**Một run diễn ra thế nào:**

- Mỗi task viết code làm việc trên nhánh `awf/tasks/<run>/<task>`. Xong việc, nó được **rebase** lên nhánh tích
  hợp `awf/run-<id>`, rồi nhánh tích hợp được fast-forward. Lịch sử thẳng, không có commit merge.
- Bước *Prepare pull request*: nếu trong lúc run chạy bạn đã push gì lên `develop`, nhánh tích hợp được **rebase
  lên `develop` mới nhất**, rồi platform build và test lại. `main` không liên quan tới run.
- Bạn duyệt **approve_merge**, stage *Merge into the base branch* fast-forward `develop` tới nhánh tích hợp. Không
  bao giờ có force push. Nếu `develop` lại vừa đổi, nhánh tích hợp được rebase thêm một lần nữa trước khi merge.
- Sau đó, trên PC: `git pull factory develop`.

**Dọn nhánh (tự động):**

| Nhánh | Bị xoá khi |
|---|---|
| `awf/tasks/<run>/…` | Ngay sau khi task đã gộp vào nhánh run. Nhánh của task gặp conflict được giữ tới khi conflict được giải |
| `awf/run-<id>` | Ngay sau khi đã merge vào `develop` |
| `awf/run-<id>` của run fail, bị huỷ, hoặc chưa merge | Sau 14 ngày (`AWF_STALE_BRANCH_DAYS`), để bạn kịp xem lại. Nhánh không có commit riêng thì xoá ngay |

Máy trung tâm kiểm tra mỗi 2 phút và mỗi khi một run kết thúc. Trên PC, dùng `git fetch --prune factory` để bỏ các
nhánh đã xoá. Repo ở GitHub (không phải repo chung): worker vẫn xoá nhánh task, nhưng nhánh run được giữ lại vì
nó là nhánh nguồn của pull request.

**Release develop → main** (khi đã hoàn thành thật sự):

1. Trang project, mục **Git remote** → **Release develop → main**: danh sách commit sắp lên `main`, và cho biết
   đó là fast-forward hay merge commit.
2. Tuỳ chọn: nhập **tag** (ví dụ `v1.2.0`), rồi bấm nút và xác nhận.
3. Nếu `main` có hotfix riêng, máy trung tâm tạo một merge commit, *nếu không có conflict*. Có conflict thì không
   gì thay đổi; dashboard in lệnh để bạn merge trên PC (`git switch main`, `git merge factory/develop`,
   `git push factory main`).
4. Mirror được đẩy ngay sau đó. Trên PC: `git pull factory main`.

**Mirror lên GitHub** (để code không chỉ nằm trên máy trung tâm):

1. Tạo repo trống trên GitHub (không có README).
2. Tạo **fine-grained token**: GitHub → *Settings → Developer settings → Personal access tokens → Fine-grained
   tokens → Generate new token*. Chọn *Only select repositories* (các repo cần mirror), mục *Repository
   permissions → Contents: Read and write*. Đặt hạn sử dụng, rồi sao chép token.
3. Trên **máy trung tâm**, thêm vào `.env`: `AWF_MIRROR_TOKEN=github_pat_...`, rồi khởi động lại. Token chỉ nằm
   trên máy trung tâm, và chỉ được gửi tới đúng URL mirror dưới dạng header: không nằm trong URL, log hay
   dashboard.
4. Trang project, mục **Mirror (GitHub)**: nhập `https://github.com/<bạn>/<repo>.git` → **Lưu**. Lần đẩy đầu
   tiên chạy ngay.
5. Từ đó, mỗi lần `main`, `develop` hoặc một tag đổi (run merge, Release, hay bạn push từ PC), máy trung tâm đẩy
   lên GitHub. Nhánh `awf/*` không lên GitHub. Không bao giờ force: nếu ai đó push thẳng lên GitHub làm hai bên
   khác nhau, mirror báo lỗi trên dashboard và cứ 5 phút thử lại. Hãy đưa commit đó vào repo chung
   (`git pull` từ GitHub trên PC rồi `git push factory`).
6. Muốn dùng SSH thay token: URL `git@github.com:<bạn>/<repo>.git`, và thêm khoá SSH của máy trung tâm làm
   *deploy key* (có quyền ghi) cho repo đó.

**Repo tham khảo (chỉ đọc):** mục **Cài đặt → Repo tham khảo**, mỗi dòng `tên | URL | nhánh`, ví dụ:

```text
ui-kit | awf-git:ui-kit | feature/grid
old-api | | release/1.0
```

`awf-git:<project>` là một repo khác trên máy trung tâm; để trống URL là chính repo này (một nhánh khác). Worker
clone nông vào `.awf-refs/<tên>/`, không kèm `.git`. Agent đọc được như file thường (công cụ `fs.read` hoặc
engine), không ghi được (`fs.write` bị từ chối), và platform không bao giờ commit thư mục này. Trong objective, có
thể viết: "làm giống `.awf-refs/ui-kit`".

**Ảnh chụp màn hình và báo cáo của test:** mục **Cài đặt → File test cần giữ**, ví dụ
`TestResults/**/*.png, playwright-report/**/*.html, **/*.trx`.

- Sau mỗi job có chạy test, worker gom các file khớp mẫu mà test vừa tạo: tối đa 20 file, mỗi file 2 MB, tổng
  10 MB. File văn bản (báo cáo) được che secret trước khi gửi.
- Mỗi file thành một artifact: `test_screenshot` (ảnh) hoặc `test_report`. Trang run hiện ảnh dạng lưới; bấm vào để
  xem cỡ lớn.
- Reviewer và QA nhận ảnh trong `.awf-artifacts/` của workspace (không commit). Engine như Claude Code mở và xem
  được ảnh. Runtime native chỉ thấy tên file.

**Khi có conflict:**

1. Git không tự rebase được, nên platform tạo task **Resolve conflict** cho Developer (cùng engine và model của
   run). Workspace của task chứa sẵn hai phía dưới dạng một squash merge, kèm dấu conflict.
2. Agent sửa xong thì platform kiểm tra không còn dấu `<<<<<<<` / `=======` / `>>>>>>>`, build, test, commit, rồi
   đẩy lên nhánh tích hợp. Trên trang run, task này có tên "Resolve conflict: … onto …".
3. Agent không giải được (hết lượt thử) thì run **dừng chờ bạn**, với yêu cầu duyệt **resolve_conflict**. Yêu cầu
   này liệt kê các file conflict và lệnh để bạn tự giải trên PC (có nút Sao chép). Giải xong và push thì bấm
   **Approve**, run chạy tiếp. **Reject** thì run fail.
4. Trên 6 lần conflict trong một run: run dừng, vì có thể các task đang chồng chéo nhau. Hãy xem lại kế hoạch.

**Xem việc của một run trên PC:** `git fetch factory`, rồi `git switch -c review factory/awf/run-<id>` (trong lúc
run chạy; sau khi merge, nhánh run đã bị xoá và công việc nằm trên `develop`). Không nên commit lên các nhánh
`awf/*` trong lúc run còn chạy, vì platform rebase chúng.

**Sao lưu:** `awf db backup` sao lưu cả các repo này, mỗi repo một file `var/backups/git-<project>-*.bundle`.
Khôi phục bằng `git clone --bare <file .bundle> var/git/<project>.git`. Mirror trên GitHub là bản thứ hai, ở nơi
khác: mất máy trung tâm, bạn vẫn có `main`, `develop` và các tag. Muốn dựng lại repo chung từ GitHub: tạo project
mới với *Chép vào máy trung tâm từ một URL* = URL GitHub.

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

Máy trung tâm và worker là hai máy khác nhau: làm theo [mục 3b](#3b-lần-đầu-dựng-máy-trung-tâm-và-worker-từng-thao-tác)
(Bước 2–3). Thử trên **một máy**, hai cửa sổ terminal (PowerShell):

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

## 6. Model thật: Claude, Gemini, ChatGPT, Qwen, Kimi, DeepSeek, MiniMax

Factory dùng model theo **hai tầng**:

1. **API của nhà cung cấp, cho mọi agent.** Mỗi run chọn một nhà cung cấp, và **mọi** agent của run đó gọi API
   của nhà cung cấp ấy, không riêng coding: analyst, architect, planner, developer, test engineer, reviewer, QA,
   cùng các role của workflow content (researcher, strategist, copywriter, reviewer, editor, SEO). Tầng này **cần
   API key** của nhà cung cấp, đặt trên máy worker.
2. **Engine (tuỳ chọn).** Một CLI coding agent chạy headless trên worker (Claude Code, Codex, Antigravity...).
   Mặc định nó chỉ làm stage development (sửa code thay cho runtime native). Đặt **Engine dùng cho = Mọi role**
   (`engine_scope: all`) thì nó làm **mọi role** của run, và cả run có thể chạy bằng **gói thuê bao** (Claude
   Pro/Max, Google AI Pro) thay cho API key: xem "Chạy cả run bằng gói Pro" ở dưới. Platform vẫn tự chạy
   build/test, Critic chấm trên kết quả đó, và platform tự commit.

### Bảng tổng hợp (tháng 10/2026)

| Nhà cung cấp | `llm` | API key trên worker | Lấy key ở | Engine viết code | Engine dùng gói thuê bao? |
|---|---|---|---|---|---|
| Claude (Anthropic) | `anthropic` (mặc định) | `ANTHROPIC_API_KEY` | platform.claude.com | **Claude Code** (`claude-code`) | **Có** (`AWF_CLAUDE_CODE_AUTH=login`): đăng nhập Claude Pro/Max một lần; hoặc API key |
| Gemini (Google) | `google` | `GEMINI_API_KEY` | aistudio.google.com | **Antigravity CLI** (`antigravity`) | **Có**: đăng nhập Google một lần, dùng quota gói Google AI Pro/Ultra; hoặc API key |
| ChatGPT (OpenAI) | `openai` | `OPENAI_API_KEY` | platform.openai.com | **Codex** (`codex`) | **Có** (`AWF_CODEX_AUTH=login`): `codex login` bằng tài khoản ChatGPT Plus/Pro; hoặc API key |
| Qwen (Alibaba) | `qwen` | `DASHSCOPE_API_KEY` | modelstudio.console.alibabacloud.com | **Qwen Code** (`qwen-code`) | Không: chỉ API key |
| Kimi (Moonshot AI) | `kimi` | `MOONSHOT_API_KEY` | platform.kimi.ai | **Kimi Code** (`kimi-code`) | Đăng nhập Kimi Code bằng API key (`/login`) một lần |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` | platform.deepseek.com | **DeepSeek Harness** (`deepseek-harness`) | Không: chỉ API key |
| MiniMax | `minimax` | `MINIMAX_API_KEY` | platform.minimax.io | **MiniMax Code** (`minimax-code`) | Đăng nhập tài khoản MiniMax (`mcode login`) hoặc API key đã lưu, làm một lần |

Cả bảy nhà cung cấp đều có engine viết code của chính hãng. Không bật engine thì Developer dùng runtime native của
factory (tool có kiểu `fs.read`, `fs.replace`, `build.run`...), với mọi nhà cung cấp.

**Về Gemini và gói Google AI Pro.** Gemini **có** engine viết code. Engine đó là **Antigravity CLI** (`agy`),
không phải Gemini CLI: theo trang quota chính thức của Gemini CLI, "Gemini CLI was replaced by Antigravity CLI on
June 18th, 2026", và Gemini CLI không được tối ưu cho Gemini 4 Argon (Google hướng dẫn dùng Antigravity cho model
này; qua API thì Gemini 4 Argon vẫn dùng bình thường). Antigravity (IDE và CLI) là công cụ của Google cho
coding agent. Factory chạy bản **CLI** headless của nó: `agy -p`. Gói **Google AI Pro**:

- dùng được cho **engine Antigravity**: đăng nhập tài khoản Google trên máy worker một lần, engine dùng quota của gói;
- **không** dùng được cho **tầng API** (các role khác chạy trên Gemini): tầng này cần `GEMINI_API_KEY` từ AI Studio,
  tính tiền riêng (hoặc gói miễn phí giới hạn, chỉ model Flash).

Hai cách dùng gói Google AI Pro: (1) chạy run với `llm` là nhà cung cấp bạn có API key (ví dụ Claude), đặt
**Coding engine = Antigravity**: stage development dùng quota Gemini Pro, các role khác dùng API key; hoặc (2) đặt
thêm **Engine dùng cho = Mọi role**: cả run dùng quota gói, không cần API key nào (mục kế tiếp).

### Chạy cả run bằng gói Pro (engine cho mọi role)

Gói thuê bao (Claude Pro/Max, Google AI Pro/Ultra) **không** trả tiền cho API key: hai bên tính tiền riêng, kể cả
khi cùng email. Nhưng CLI của hãng (Claude Code, Antigravity) đăng nhập được bằng tài khoản gói. Bật **engine
cho mọi role** thì factory giao mọi role của run cho CLI đó, nên cả run chạy bằng gói (ADR-014):

| Bước | Claude Pro/Max | Google AI Pro/Ultra |
|---|---|---|
| 1. Cài CLI trên worker | Claude Code (`claude`) | Antigravity CLI (`agy`) |
| 2. Đăng nhập một lần, bằng đúng tài khoản hệ điều hành chạy worker | chạy `claude`, chọn đăng nhập bằng tài khoản Claude | chạy `agy`, đăng nhập Google |
| 3. `.env` của worker | `AWF_CLAUDE_CODE_AUTH=login` (bắt buộc nếu `.env` có `ANTHROPIC_API_KEY`, kể cả key hết credit) | không cần gì thêm |
| 4. Khởi động lại worker | trang **Workers** thấy capability `claude-code` | thấy `antigravity` |
| 5. Bắt đầu run | **Coding engine** = Claude Code, **Engine dùng cho** = Mọi role | **Coding engine** = Antigravity, **Engine dùng cho** = Mọi role |

API: `"coding_engine": "claude-code", "engine_scope": "all"`. Mặc định cho project: trang project → **Engine mặc
định dùng cho**, hoặc `labels.engine_scope = "all"`. Run khi đó **không cần API key nào**: nó chỉ đòi máy có
capability của engine.

Khác với runtime native:

- Role chỉ đọc (analyst, architect, planner, reviewer, QA) không được sửa file. Với Claude Code, các tool Edit và
  Write bị gỡ hẳn; với engine khác, đây là quy tắc trong prompt, và file bị đổi sẽ bị huỷ.
- Role cần trả **quyết định** (reviewer: APPROVE/CHANGES_REQUIRED) hoặc nhiều output (architect) kết thúc bằng
  một khối JSON `awf-result`. Platform kiểm tra khối đó theo contract, giống `submit_result` của runtime native.
  Sai thì engine được làm lại **một lần** với danh sách lỗi; sai tiếp thì job fail (`engine result rejected`).
- Mỗi role là một lần chạy CLI riêng. Run chậm hơn runtime native, và mỗi role tốn vài lượt của gói.
- Chi phí ghi là `0` (`billing: subscription`): gói đã trả. Ngân sách `budget_usd` không bị trừ.
- Gói có giới hạn theo khung giờ (Claude Pro: theo phiên 5 giờ; Google AI Pro: theo chu kỳ của gói). Hết lượt thì
  job fail với lỗi của CLI. Chờ gói hồi rồi chạy lại.
- Model: Claude Code dùng model của route (ví dụ `claude-opus-5-5` cho architect). Nếu gói không có model đó, đặt
  `AWF_CLAUDE_CODE_MODEL=sonnet` trên worker. Antigravity tự chọn model, trừ khi đặt `AWF_ANTIGRAVITY_MODEL`.
- Trong chế độ `login`, Claude Code không đọc settings, quyền và MCP server của tài khoản bạn trên máy worker
  (`--setting-sources local`, `--strict-mcp-config`), nên các quyền bạn tự cấp cho Claude Code không áp vào job.

### Model và effort cho từng role (ADR-015)

Nguyên tắc: model mạnh chỉ dùng cho **kiến trúc và lập kế hoạch**, ở effort medium. Viết code, test, review, QA
dùng model cân bằng. Effort high hoặc xhigh chỉ dùng cho việc khó, và **luôn chờ bạn duyệt** trước.

| Role | Class | Bình thường | Khó | Rất khó |
|---|---|---|---|---|
| architect, planner | REASONING | `claude-opus-5-5` medium | Opus high | Opus xhigh |
| developer | CODE_SPECIALIST | `claude-sonnet-5-5` medium | Sonnet high | Opus high |
| code reviewer, analyst, QA, test engineer | BALANCED | `claude-sonnet-5-5` medium | Sonnet high | Opus high |
| việc cơ học (tóm tắt log, refactor nhỏ) | FAST | `claude-haiku-4-5` | | |
| project `restricted` | LOCAL_PRIVATE | (chưa có model) → fail an toàn | | |

Với Gemini: architect `gemini-4-argon` medium, developer và các role khác `gemini-3.8-flash` medium. Effort của
Gemini dừng ở high. Với ChatGPT: `gpt-6.1-sol`, medium cho architect và developer, low cho các role khác.

**Độ khó** của một run (dashboard → *Complexity*, API `"complexity"`, mặc định cho project ở *Where it runs*):

- *Bình thường* (mặc định): như cột "Bình thường". Hợp với hầu hết việc.
- *Khó*: mọi role tăng effort một nấc.
- *Rất khó*: các role thường chuyển sang model mạnh (Opus), architect và planner lên xhigh.
- Một task bị trả về (reviewer, Critic hoặc bạn yêu cầu sửa, hoặc fix loop) tự leo **một mức** ở lần chạy sau.
  Lỗi hạ tầng (timeout, mất worker) không làm leo mức.

**Duyệt effort cao.** Trước khi một task chạy với effort `high` hoặc `xhigh`, dù model nào hay role nào, run tạo
một yêu cầu duyệt **`approve_effort`** (trang **Approvals**, Telegram...). Yêu cầu ghi rõ task, role, model, effort
và lý do (ví dụ "hard (fix loop 2); effort medium -> high"). Task đứng ở `APPROVAL_REQUIRED` cho tới khi bạn trả lời:

- **Approve:** task chạy đúng model và effort đó.
- **Reject:** task vẫn chạy, ở effort cao nhất không cần duyệt (medium). Run không bị dừng.
- Mỗi tổ hợp model + effort chỉ hỏi một lần cho mỗi task. Nếu sau đó task leo lên effort cao hơn, bạn được hỏi lại.

Danh sách effort cần duyệt nằm ở `escalation.approval_required` trong `config/model-routing.yaml`. Antigravity,
Qwen Code, Kimi Code, DeepSeek Harness và MiniMax Code tự chọn mức suy luận, nên không có effort để duyệt.

**Xem task đang chạy model gì:** trang run → bảng task → cột **Model · effort** (di chuột để xem lý do định tuyến).
Chi tiết từng lần chạy: bấm vào task → *Agent run* → model, class, lý do định tuyến.

### Lấy API key và đặt vào worker

Agent chạy **trên worker**, nên API key đặt trong `.env` của **máy worker**, không đặt trên máy trung tâm. Một máy
chỉ nhận run của nhà cung cấp mà nó có key: mỗi key trong bảng tổng hợp ở trên làm worker báo capability
`llm-<nhà cung cấp>` (`ANTHROPIC_API_KEY` → `llm-anthropic`, `GEMINI_API_KEY` → `llm-google`, `OPENAI_API_KEY` →
`llm-openai`, `DASHSCOPE_API_KEY` → `llm-qwen`, `MOONSHOT_API_KEY` → `llm-kimi`, `DEEPSEEK_API_KEY` → `llm-deepseek`,
`MINIMAX_API_KEY` → `llm-minimax`). Không máy nào có key cần thiết thì run bị từ chối ngay khi bấm **Start**,
với lỗi như sau:

```text
worker 'DESKTOP-...' does not meet platform windows, capabilities [git, llm-anthropic]: missing llm-anthropic
```

Nghĩa là máy đó chưa có API key của Claude. Đây là key của **Claude API**, không phải tài khoản app Claude hay
gói Claude Pro/Max: gói thuê bao không trả tiền cho API key. Muốn chạy bằng gói, xem "Chạy cả run bằng gói Pro" ở
trên.

**Claude (Anthropic):**

1. Mở **platform.claude.com** (Claude Console), đăng nhập hoặc đăng ký.
2. **Billing**: nạp credit. API trả tiền theo lượng dùng; không có credit thì mọi request bị từ chối.
3. **API Keys → Create Key**: đặt tên (ví dụ `awf-worker-01`), chép key (dạng `sk-ant-...`). Key chỉ hiện **một lần**.
4. Nên tạo mỗi worker một key: khi một máy bị lộ thì chỉ cần thu hồi key của máy đó. Có thể đặt giới hạn chi tiêu
   theo tháng trong Console (**Limits**).

**Gemini (Google):**

1. Mở **aistudio.google.com**, đăng nhập bằng tài khoản Google.
2. **Get API key → Create API key**: chọn (hoặc tạo) một Google Cloud project, chép key (dạng `AIza...`).
3. Gói miễn phí có giới hạn số request và dữ liệu có thể được dùng để cải thiện sản phẩm. Để dùng cho code thật,
   bật billing cho project đó (Google Cloud Console → Billing).

**ChatGPT (OpenAI):**

1. Mở **platform.openai.com**, đăng nhập hoặc đăng ký. Đây là tài khoản **OpenAI API**, tính tiền riêng với gói
   ChatGPT Plus/Pro: gói ChatGPT không dùng được cho factory.
2. **Settings → Billing**: nạp credit. Hết credit thì request bị từ chối (`insufficient_quota`) và job fail ngay.
3. **API keys → Create new secret key**: chọn project, đặt tên (ví dụ `awf-worker-01`), chép key (dạng
   `sk-proj-...`). Key chỉ hiện **một lần**.
4. Có thể đặt giới hạn chi tiêu trong **Settings → Limits** của project.

**Qwen (Alibaba Cloud Model Studio):**

1. Mở **modelstudio.console.alibabacloud.com** (bản quốc tế), đăng nhập tài khoản Alibaba Cloud, kích hoạt Model
   Studio, chọn vùng Singapore.
2. **API Key → Create API Key**, chép key (dạng `sk-...`). Nạp tiền hoặc gắn phương thức thanh toán cho tài khoản.
3. Factory gọi endpoint quốc tế `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`. Tài khoản vùng Trung Quốc
   (Bắc Kinh) đặt thêm `AWF_QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`.

**Kimi (Moonshot AI):**

1. Mở **platform.kimi.ai** (trước là platform.moonshot.ai), đăng nhập, nạp tiền.
2. **API Keys → Create**, chép key (dạng `sk-...`).
3. Endpoint mặc định `https://api.moonshot.ai/v1`. Tài khoản Trung Quốc đặt `AWF_KIMI_BASE_URL=https://api.moonshot.cn/v1`.

**DeepSeek:**

1. Mở **platform.deepseek.com**, đăng nhập, **Top up** (nạp tiền trước; hết số dư thì request bị từ chối).
2. **API keys → Create new API key**, chép key (dạng `sk-...`).
3. Endpoint `https://api.deepseek.com`. Giá giờ thấp điểm (off-peak) bằng một nửa giờ cao điểm.

**MiniMax:**

1. Mở **platform.minimax.io**, đăng nhập, nạp tiền.
2. **API Keys → Create new secret key**, chép key.
3. Endpoint quốc tế `https://api.minimax.io/v1`. Tài khoản Trung Quốc (minimaxi.com) đặt
   `AWF_MINIMAX_BASE_URL=https://api.minimaxi.com/v1`.

**Đặt key vào worker** (mỗi máy worker):

1. Mở file `.env` trong thư mục repo của máy worker, thêm các dòng của nhà cung cấp bạn dùng (bỏ dấu `#` nếu dòng
   đã có sẵn):
   ```text
   ANTHROPIC_API_KEY=sk-ant-...
   GEMINI_API_KEY=AIza...
   OPENAI_API_KEY=sk-proj-...
   DASHSCOPE_API_KEY=sk-...
   MOONSHOT_API_KEY=sk-...
   DEEPSEEK_API_KEY=sk-...
   MINIMAX_API_KEY=...
   ```
2. Kiểm tra `.env` **không** có dòng `AWF_MODEL_PROVIDER=scripted` (chế độ offline, ghi đè mọi model). Máy trung
   tâm cũng không được đặt dòng này.
3. Khởi động lại worker: dừng `awf start` (`Ctrl+C`) rồi chạy lại. Nếu đã cài dịch vụ, chạy
   `.\scripts\install-task.ps1 -Restart` (Windows) hoặc `sudo systemctl restart awf` (Linux).
4. Kiểm tra: `awf doctor` trên worker liệt kê `llm-<nhà cung cấp>` tương ứng trong capability. Trên dashboard,
   trang **Workers** cũng phải thấy capability đó. Sau đó **Start** lại run.

Key chỉ nằm trong `.env` của máy, file này không commit vào git. Factory không gửi key sang máy trung tâm, không
đưa key vào prompt, và lệnh build/test chạy với môi trường đã gỡ mọi biến có tên dạng `*KEY*`/`*TOKEN*`.
Demo với model thật: `awf demo --live-model`.

Chi tiết tích hợp (`awf/agent_runtime/adapters/anthropic_adapter.py`): Messages API, vòng lặp tool-use thủ
công (để mọi tool call đi qua Policy Engine), prompt caching tự động, `output_config.effort` theo class,
`fallbacks: "default"` khi model bị từ chối (refusal), lỗi 429/5xx là retryable, 400/401/403 là không retry.
Chi phí ước tính theo bảng `pricing` và hiển thị theo agent run / run / project. Đặt `budget_usd` cho
project: khi ngân sách còn dưới `budget_downgrade_threshold_usd`, router tự hạ class.

### Gemini

Mỗi run dùng **một** nhà cung cấp cho mọi agent của nó: Claude (`anthropic`, mặc định), Gemini (`google`),
ChatGPT (`openai`), Qwen (`qwen`), Kimi (`kimi`), DeepSeek (`deepseek`) hoặc MiniMax (`minimax`), xem bảng tổng hợp
ở đầu mục 6.
Bảng `providers.google` trong `config/model-routing.yaml` ánh xạ cùng các class sang model Gemini:

| Class | Gemini |
|---|---|
| FAST | `gemini-3.5-flash-lite` (thinking low) |
| BALANCED | `gemini-3.8-flash` (thinking medium) |
| REASONING, CODE_SPECIALIST | `gemini-4-argon` (thinking high) |
| MULTIMODAL | `gemini-3.8-flash` |
| LOCAL_PRIVATE | không đổi: project `restricted` không bao giờ được gửi sang model hosted |

Chọn nhà cung cấp, theo thứ tự ưu tiên:

1. Khi tạo run: dashboard → **Model**, hoặc API `{"llm": "google"}` (hay `"openai"`).
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

### ChatGPT (OpenAI)

Bảng `providers.openai` trong `config/model-routing.yaml` ánh xạ các class sang model của OpenAI (giá tháng 10/2026,
USD mỗi 1M token vào/ra):

| Class | ChatGPT | Giá |
|---|---|---|
| FAST | `gpt-6-luna` (effort low) | 0.10 / 0.50 |
| BALANCED | `gpt-6.1-sol` (effort medium) | 2 / 10 |
| REASONING, CODE_SPECIALIST | `gpt-6.1-sol` (effort high) | 2 / 10 |
| MULTIMODAL | `gpt-6.1-sol` (effort medium) | 2 / 10 |
| LOCAL_PRIVATE | không đổi: project `restricted` không bao giờ được gửi sang model hosted | |

`gpt-6-astra` (10 / 50) là model mạnh nhất. Muốn dùng cho architect và reviewer thì sửa dòng `REASONING` trong
`providers.openai`. Chọn ChatGPT cho một run: dashboard → **Model** = ChatGPT, hoặc API `{"llm": "openai"}`. Mặc
định cho project: **Default model**. Key: `OPENAI_API_KEY` trong `.env` của worker (cách lấy ở trên).

Chi tiết tích hợp (`awf/agent_runtime/adapters/openai_adapter.py`): **Responses API** qua REST (không thêm SDK),
function calling thủ công qua Policy Engine như Claude/Gemini, `store: false` (OpenAI không giữ hội thoại; cả danh
sách item được gửi lại mỗi lượt, kể cả reasoning đã mã hoá), `reasoning.effort` theo class. Token suy nghĩ được
tính vào output. 429/5xx thử lại 3 lần; `insufficient_quota` (hết credit), 400/401/403/404 và từ chối an toàn thì
không retry.

### Qwen, Kimi, DeepSeek, MiniMax

Bốn nhà cung cấp này dùng chung một adapter: **Chat Completions tương thích OpenAI**
(`awf/agent_runtime/adapters/chat_completions_adapter.py`, ADR-013). Chọn cho một run: dashboard → **Model**, hoặc API
`{"llm": "qwen"}` (`kimi`, `deepseek`, `minimax`). Mặc định cho project: **Default model**.

| Class | Qwen | Kimi | DeepSeek | MiniMax |
|---|---|---|---|---|
| FAST | `qwen3.8-flash` | `kimi-k3` | `deepseek-flash` | `MiniMax-M3` |
| BALANCED | `qwen3.7-plus` | `kimi-k3` | `deepseek-flash` (thinking) | `MiniMax-M3` |
| REASONING, CODE_SPECIALIST | `qwen3.8-max` | `kimi-k3.1-pro` | `deepseek-v4-pro` (thinking, effort high) | `MiniMax-M3` |
| MULTIMODAL | `qwen3.7-plus` | `kimi-k3` | `deepseek-flash` | `MiniMax-M3` |

Giá trong bảng `pricing` (USD mỗi 1M token vào/ra, tháng 10/2026, nên kiểm tra lại trên trang của từng hãng):
`qwen3.8-max` 2 / 6, `qwen3.7-plus` 0.40 / 1.60 (tới 256K token đầu vào), `qwen3.8-flash` 0.15 / 0.47;
`deepseek-v4-pro` 1.32 / 3.96 và `deepseek-flash` 0.30 / 1.20 (giờ cao điểm; thấp điểm bằng một nửa); `MiniMax-M3`
0.30 / 1.20 (tới 512K). **Giá Kimi K3 chưa có trong bảng**: chi phí run Kimi hiện là $0 cho tới khi bạn thêm giá vào
`pricing`.

Chi tiết kỹ thuật:

- Function calling thủ công qua Policy Engine như các nhà cung cấp khác.
- Message trả lời của model được gửi lại **nguyên vẹn**, kể cả `reasoning_content`: DeepSeek và Kimi trả lỗi 400
  nếu phần suy nghĩ của lượt trước bị bỏ đi trong một chuỗi tool call.
- Trường riêng của từng hãng (bật thinking, `reasoning_effort`...) khai báo trong `extra` của
  `config/model-routing.yaml` và được gửi nguyên vào request. Đổi model hoặc bật tính năng mới không cần sửa code.
- Không retry khi hết số dư (`insufficient_balance`, HTTP 402) hoặc sai key (400/401/403/404). 429 và 5xx thử lại 3 lần.
- Tool call bị cắt vì giới hạn output, hoặc arguments không phải JSON, được trả lời bằng lỗi chứ không chạy.
- Khu vực Trung Quốc: đặt `AWF_<NHÀ CUNG CẤP>_BASE_URL` trên worker (xem cách lấy key ở trên).

**Chế độ scripted** (`AWF_MODEL_PROVIDER=scripted` + `AWF_SCRIPTED_RESPONSES=<file.yaml>`): agent không gọi
model mà phát lại kịch bản; role không có trong kịch bản dùng hành vi tự động từ contract. Phần nền tảng
(git, sandbox, build/test thật, critic, gate) vẫn chạy thật — dùng để kiểm thử tích hợp repo trước khi tốn
tiền model. Xem `examples/*/scripted-responses.yaml`.

### Engine viết code cho Developer (`coding_engine`)

Có bảy engine, mỗi nhà cung cấp một engine:

| Engine | Nhà cung cấp | Lệnh trên worker | Cài đặt | Xác thực | Giới hạn engine |
|---|---|---|---|---|---|
| `claude-code` | Claude | `claude` | code.claude.com | `ANTHROPIC_API_KEY` | Chặt nhất: Bash chỉ chạy đúng lệnh build/test/lint đã cấu hình, không web |
| `codex` | ChatGPT | `codex` | `npm i -g @openai/codex` | `OPENAI_API_KEY` | Sandbox hệ điều hành: ghi trong workspace, không mạng |
| `antigravity` | Gemini | `agy` | antigravity.google/docs/getting-started | Đăng nhập Google (gói AI Pro/Ultra) hoặc API key | Lỏng: tự duyệt mọi tool call |
| `qwen-code` | Qwen | `qwen` | `npm i -g @qwen-code/qwen-code` | `DASHSCOPE_API_KEY` | Lỏng: tự duyệt mọi tool call; tuỳ chọn sandbox Docker |
| `kimi-code` | Kimi | `kimi` | code.kimi.com | `/login` một lần bằng API key Kimi | Lỏng: tự duyệt mọi tool call |
| `deepseek-harness` | DeepSeek | `dsh` | `npm i -g @deepseek-ai/dsh` | `DEEPSEEK_API_KEY` | Trung bình: chế độ auto, mỗi tool call được bộ review tự động duyệt hoặc từ chối |
| `minimax-code` | MiniMax | `mcode` | `npm i -g @minimax-ai/code` | `mcode login`, hoặc API key lưu bằng `mcode provider set-minimax-key` | Lỏng: `--permission full` |

Bật engine giống nhau cho mọi engine: dashboard → **Coding engine**, API `"coding_engine": "<engine>"`, hoặc
`labels.coding_engine` cho project. Mặc định engine chỉ áp cho stage development (**Engine dùng cho = Mọi role**
thì áp cho mọi role, xem "Chạy cả run bằng gói Pro"), và luôn đi kèm model của nhà cung cấp
của nó (một run Claude bật engine Antigravity vẫn develop bằng Gemini). Project `restricted` và chế độ `scripted`
luôn dùng runtime native.

Điểm chung của mọi engine: worker clone repo; engine sửa code; **worker tự chạy lại build/test/lint**; Critic chấm
trên kết quả đó; worker tự commit. Engine tự commit thì job fail (`ENGINE_TOUCHED_GIT`). Lệnh chỉ thấy môi trường
đã gỡ token của máy (`AWF_*`...), chỉ còn key của chính engine.

#### Claude Code (`coding_engine: claude-code`)

#### Cài engine trên worker: từng bước

Làm trên **từng máy worker** sẽ chạy engine, bằng **đúng tài khoản hệ điều hành chạy worker**. Worker chạy dưới
dạng Scheduled Task hay systemd thì đăng nhập CLI bằng chính user đó: CLI lưu phiên đăng nhập trong thư mục của
user, và worker chạy bằng user khác sẽ không thấy phiên đó.

**Các bước chung cho mọi engine:**

1. Cài CLI (bảng dưới). Mở **cửa sổ lệnh mới** để PATH có lệnh vừa cài, rồi kiểm tra bằng `<lệnh> --version`.
2. Đăng nhập (gói thuê bao) hoặc đặt API key (bảng dưới).
3. Trong `.env` của worker: đặt `AWF_<ENGINE>_BIN=<đường dẫn đầy đủ tới lệnh>`. Không bắt buộc, nhưng **nên làm**,
   vì worker chạy dưới dạng dịch vụ thường giữ PATH cũ. Xem đường dẫn bằng `where <lệnh>` (Windows) hoặc
   `which <lệnh>` (Linux). Trên Windows, lệnh cài bằng npm là file `.cmd` trong `%APPDATA%\npm`.
4. Khởi động lại worker (`.\scripts\install-task.ps1 -Restart`, `sudo systemctl restart awf`, hoặc dừng và chạy lại
   `awf start`).
5. Dashboard → **Workers**: máy phải có capability của engine. Không thấy thì chạy `awf doctor` trên máy đó.
6. Chạy thử: project → **Coding engine** = engine đó, **Engine dùng cho** = *Chỉ viết code* (thử nhanh) hoặc
   *Mọi role* (cả run qua engine, dùng gói thuê bao).

| Engine | Cài (Windows PowerShell / Linux) | Đăng nhập hoặc key | `.env` của worker | Capability |
|---|---|---|---|---|
| **Claude Code** | `irm https://claude.ai/install.ps1 \| iex` / `curl -fsSL https://claude.ai/install.sh \| bash` | Gói Claude Pro/Max: chạy `claude`, chọn đăng nhập bằng tài khoản Claude, xong gõ `/exit`. API key: `ANTHROPIC_API_KEY` | `AWF_CLAUDE_CODE_AUTH=login` (gói) hoặc `api-key`; `AWF_CLAUDE_CODE_BIN=C:\Users\<user>\.local\bin\claude.exe`; tuỳ chọn `AWF_CLAUDE_CODE_MODEL=sonnet` | `claude-code` (+ `claude-code-login`) |
| **Codex** (ChatGPT) | Cài Node.js LTS, rồi `npm i -g @openai/codex` | Gói ChatGPT Plus/Pro: `codex login`, đăng nhập bằng tài khoản ChatGPT trong trình duyệt. API key: `OPENAI_API_KEY` | `AWF_CODEX_AUTH=login` (gói) hoặc `api-key`; `AWF_CODEX_BIN=%APPDATA%\npm\codex.cmd` | `codex` (+ `codex-login`) |
| **Antigravity** (Gemini) | Bộ cài Antigravity CLI ở antigravity.google/docs/getting-started (lệnh `agy`) | Gói Google AI Pro/Ultra: chạy `agy`, đăng nhập bằng tài khoản Google. Hoặc API key Gemini | `AWF_ANTIGRAVITY_BIN=<đường dẫn agy>`; tuỳ chọn `AWF_ANTIGRAVITY_MODEL` | `antigravity` |
| **Qwen Code** | Node.js 20+, rồi `npm i -g @qwen-code/qwen-code` (lệnh `qwen`) | API key DashScope (Model Studio): `DASHSCOPE_API_KEY` | `AWF_QWEN_CODE_BIN`; tuỳ chọn `AWF_QWEN_CODE_SANDBOX=1` (cần Docker/Podman) | `qwen-code`, `llm-qwen` |
| **Kimi Code** | Bộ cài ở code.kimi.com (lệnh `kimi`) | Chạy `kimi`, gõ `/login`, dán API key Kimi (platform.kimi.ai) | `AWF_KIMI_CODE_BIN`; tuỳ chọn `AWF_KIMI_CODE_MODEL` | `kimi-code` |
| **DeepSeek Harness** | Node.js 20+, rồi `npm i -g @deepseek-ai/dsh` (lệnh `dsh`) | `DEEPSEEK_API_KEY` (platform.deepseek.com) | `AWF_DEEPSEEK_HARNESS_BIN` | `deepseek-harness`, `llm-deepseek` |
| **MiniMax Code** | Node.js 22.19+, rồi `npm i -g @minimax-ai/code` (lệnh `mcode`) | `mcode login --region global`, hoặc key qua `mcode provider set-minimax-key` | `AWF_MINIMAX_CODE_BIN`; tuỳ chọn `AWF_MINIMAX_CODE_MODEL` | `minimax-code` |

**Claude Code (Windows), chi tiết:**

1. PowerShell (không cần quyền admin): `irm https://claude.ai/install.ps1 | iex`. Bộ cài đặt `claude.exe` vào
   `C:\Users\<user>\.local\bin` và thêm thư mục đó vào PATH của user.
2. Mở PowerShell **mới**: `claude --version`.
3. `claude` → chọn **Claude account with subscription** → trình duyệt mở trang đăng nhập → đăng nhập tài khoản
   Claude Pro/Max → quay lại cửa sổ lệnh, thấy lời chào → `/exit`. Muốn dùng API key thay gói thì bỏ bước này,
   đặt `ANTHROPIC_API_KEY` trong `.env`.
4. `.env` của worker:
   ```text
   AWF_CLAUDE_CODE_AUTH=login
   AWF_CLAUDE_CODE_BIN=C:\Users\<user>\.local\bin\claude.exe
   ```
   `AWF_CLAUDE_CODE_AUTH=login` **bắt buộc** khi `.env` có `ANTHROPIC_API_KEY`, kể cả key hết credit: thiếu dòng
   này, engine dùng key.
5. Khởi động lại worker. Trang **Workers** phải thấy `claude-code` và `claude-code-login`. Từ đó, form tạo project
   tự gợi ý *Coding engine = Claude Code, Engine dùng cho = Mọi role*.

**Codex (Windows), chi tiết:**

1. Cài Node.js LTS (nodejs.org). Mở PowerShell mới: `node --version`.
2. `npm i -g @openai/codex`, rồi `codex --version`.
3. Gói ChatGPT: `codex login` → trình duyệt mở → đăng nhập tài khoản ChatGPT → cửa sổ lệnh báo đăng nhập xong.
   Phiên được lưu trong `C:\Users\<user>\.codex`. Máy không mở được trình duyệt (server Linux): dùng
   `codex login --device-auth` rồi nhập mã trên máy khác.
4. `.env` của worker:
   ```text
   AWF_CODEX_AUTH=login
   AWF_CODEX_BIN=C:\Users\<user>\AppData\Roaming\npm\codex.cmd
   ```
5. Windows: Codex chạy lệnh trong sandbox của Windows. Nên dùng Windows 11; máy công ty có thể chặn bước cài
   sandbox lần đầu.
6. Khởi động lại worker: phải thấy `codex` và `codex-login`.

**Antigravity (Gemini), chi tiết:**

1. Tải và cài Antigravity CLI theo antigravity.google/docs/getting-started. Mở cửa sổ lệnh mới: `agy --version`.
2. Gói Google AI Pro/Ultra: chạy `agy` → đăng nhập bằng tài khoản Google của gói → thoát. Quota tính theo tài
   khoản, chung với Antigravity bạn dùng trên máy cá nhân.
3. `.env` của worker: `AWF_ANTIGRAVITY_BIN=<kết quả của where agy>`. Muốn ghim model:
   `AWF_ANTIGRAVITY_MODEL=<tên model>`; không đặt thì Antigravity tự chọn.
4. Khởi động lại worker: phải thấy `antigravity`.
5. Lưu ý: Antigravity tự chọn mức suy luận, nên mục **Độ khó** và cổng duyệt effort không áp dụng cho nó.

Gặp lỗi khi chạy: mở trang run → khối **Đang làm gì**, hoặc nút **Lỗi** ở đầu trang (nhật ký lỗi, có nút Sao
chép), rồi tra bảng ở mục 19.

#### Claude Code (`coding_engine: claude-code`)

Mặc định Developer chạy trên runtime native của factory: dùng các tool có kiểu (`fs.read`, `fs.replace`,
`build.run`...) với một vòng gọi model. Bật engine `claude-code` thì riêng stage development giao việc sửa code
cho **Claude Code** chạy headless trên worker. Mọi phần còn lại vẫn như cũ: Critic, evidence, policy, commit và
gate (ADR-011).

Bật engine:

| Phạm vi | Cách bật |
|---|---|
| Một run | Dashboard → project → *Start a workflow run* → **Coding engine** = Claude Code, hoặc API `"coding_engine": "claude-code"` |
| Mặc định cho project | Trang project → **Default coding engine**, hoặc `labels.coding_engine = "claude-code"` |

Chuẩn bị máy worker:

1. Cài Claude Code (code.claude.com). Lệnh `claude` phải có trên PATH, hoặc đặt `AWF_CLAUDE_CODE_BIN=<đường dẫn>`
   trong `.env`.
2. Chọn cách trả tiền bằng `AWF_CLAUDE_CODE_AUTH` trong `.env` của worker:
   - `api-key` (mặc định khi có `ANTHROPIC_API_KEY`): engine chạy `--bare`, chỉ dùng API key, chi phí tính vào
     tài khoản API;
   - `login` (mặc định khi không có key): engine dùng tài khoản đã đăng nhập bằng `claude` (gói Claude Pro/Max),
     không nhận API key. Đăng nhập một lần bằng đúng tài khoản hệ điều hành chạy worker.
3. Khởi động lại worker. `awf doctor` và trang **Workers** phải thấy capability `claude-code`. Run dùng engine chỉ
   được giao cho máy có capability này. Không có máy nào như vậy thì run bị từ chối ngay khi tạo.

Worker làm gì trong một job development:

- Clone repo như bình thường, rồi chạy `claude -p` (thêm `--bare` ở chế độ `api-key`) trong thư mục đó, với cùng prompt và context mà runtime
  native sẽ nhận. Model và effort lấy từ `config/model-routing.yaml` (class `CODE_SPECIALIST`).
- Engine chỉ có các tool Read/Edit/Write/Glob/Grep/Bash. Bash **chỉ chạy được** các lệnh build/test/lint đã cấu
  hình cho project và các lệnh git chỉ đọc. Lệnh khác, mạng, commit và push đều bị từ chối. Các lần bị từ chối
  được ghi ở tool call của agent run (`engine_denied`).
- Engine không đọc hook, plugin hay MCP từ máy worker hay từ repo, và không lưu session. Nó nhận
  `ANTHROPIC_API_KEY`, nhưng không nhận các token khác của máy (`AWF_*`, key Gemini...).
- `CLAUDE.md` và `AGENTS.md` ở gốc repo (nếu có) được đưa vào làm quy ước code.
- Engine xong việc thì **worker tự chạy lại build/test/lint**. Critic chấm trên kết quả đó, giống hệt runtime
  native. Engine báo "đã xong" mà code không build được thì task vẫn `COMPLETION_RULE_FAILED` và đi vào fix loop.
- Worker tự commit và integrate. Engine tự commit thì job fail với `ENGINE_TOUCHED_GIT`.
- Chi phí của agent run là ước tính `total_cost_usd` do Claude Code báo về (chế độ `login`: `0`, gói đã trả).

Không áp dụng cho:

- các role khác, trừ khi **Engine dùng cho = Mọi role**: analyst, architect, planner, reviewer, QA chạy native;
- project `restricted`: engine không bao giờ gửi code ra model hosted;
- chế độ `scripted`.

Một run Gemini có bật engine vẫn develop bằng Claude Code trên Claude.

Hạn chế: bên trong workspace, engine đọc và ghi tự do. Phạm vi kiểm soát của nó thô hơn các tool có kiểu, chỉ
giới hạn ở shell, mạng và git. Lệnh chạy một phần test (ví dụ một file test) bị từ chối nếu không bắt đầu bằng
đúng lệnh đã cấu hình.

#### Codex (`coding_engine: codex`)

Bật giống Claude Code: dashboard → **Coding engine** = Codex (OpenAI), API `"coding_engine": "codex"`, hoặc
`labels.coding_engine = "codex"` cho project (ADR-012).

Chuẩn bị máy worker:

1. Cài Node.js (bản LTS mới), rồi cài Codex CLI: `npm i -g @openai/codex`. Lệnh `codex` phải có trên PATH, hoặc đặt
   `AWF_CODEX_BIN=<đường dẫn>` trong `.env`.
2. Chọn cách trả tiền bằng `AWF_CODEX_AUTH` trong `.env` của worker:
   - `api-key` (mặc định khi có `OPENAI_API_KEY`): worker truyền key cho Codex qua `CODEX_API_KEY`, kèm một
     `CODEX_HOME` trống cho mỗi job. Chi phí tính vào tài khoản API;
   - `login` (mặc định khi không có key): Codex dùng tài khoản ChatGPT (gói Plus/Pro) đã đăng nhập bằng
     `codex login`, đọc từ `CODEX_HOME` của user chạy worker (mặc định `~/.codex`). Engine vẫn chạy
     `--ignore-user-config --ignore-rules --ephemeral`, nên chỉ dùng phần đăng nhập, không dùng cấu hình hay lịch
     sử của bạn. Chi phí ghi là 0.
3. Windows: Codex chạy lệnh trong sandbox riêng của Windows. Nên dùng Windows 11; Windows 10 bản mới (1809 trở
   lên) chỉ được hỗ trợ ở mức "cố gắng". Một số máy công ty chặn bước cài sandbox. Linux/Jetson không cần gì thêm.
4. Khởi động lại worker. `awf doctor` và trang **Workers** phải thấy capability `codex` (thêm `codex-login` ở chế
   độ `login`). Run dùng engine Codex chỉ được giao cho máy có capability này.

Worker làm gì trong một job development:

- Clone repo, rồi chạy `codex exec --json` trong thư mục đó. Prompt (role, quy tắc platform, task, context) đi qua
  stdin. Model và effort lấy từ bảng `providers.openai`, class `CODE_SPECIALIST` (`gpt-6.1-sol`, effort medium).
- Codex chạy mọi lệnh trong **sandbox của hệ điều hành** (`--sandbox workspace-write`): chỉ ghi được trong
  workspace, **không có mạng**, không có web search, `approval_policy=never` (không chờ ai duyệt).
- Codex chạy với `--ignore-user-config --ignore-rules --ephemeral`: không dùng cấu hình, rule hay lịch sử của máy
  worker. Ở chế độ `api-key`, mỗi job còn có `CODEX_HOME` trống riêng. Lệnh nó chạy chỉ thấy môi trường tối thiểu
  (`shell_environment_policy.inherit=core`), không thấy API key. Codex tự đọc `AGENTS.md` của repo (nếu có).
- Engine xong việc thì **worker tự chạy lại build/test/lint**, Critic chấm trên kết quả đó. Engine tự commit thì
  job fail với `ENGINE_TOUCHED_GIT`.
- Chi phí ước tính từ số token Codex báo về, theo bảng `pricing`.

Khác Claude Code: Codex **không giới hạn lệnh nào được chạy** bên trong sandbox (Claude Code chỉ cho chạy đúng
các lệnh build/test/lint đã cấu hình). Giới hạn của Codex là sandbox hệ điều hành: ghi trong workspace, không mạng.
Một run Claude hoặc Gemini có bật engine Codex vẫn develop bằng Codex trên model OpenAI.

#### Antigravity CLI cho Gemini (`coding_engine: antigravity`)

Chuẩn bị máy worker (một lần):

1. Cài Antigravity CLI theo Quickstart: antigravity.google/docs/getting-started. Lệnh `agy` phải có trên PATH, hoặc
   đặt `AWF_ANTIGRAVITY_BIN=<đường dẫn>` trong `.env`.
2. Xác thực, **chọn một** trong hai cách:
   - **Gói Google AI Pro/Ultra:** đăng nhập ngay trên máy worker, bằng đúng tài khoản Windows/Linux sẽ chạy worker.
     Chạy `agy`, đăng nhập tài khoản Google trong trình duyệt, rồi thoát. Thông tin đăng nhập nằm trong keyring
     của hệ điều hành, và chế độ headless dùng lại nó. Phải đăng nhập trực tiếp trên máy: qua SSH thì CLI đòi dán
     mã bằng tay. Worker chạy dịch vụ bằng tài khoản khác (ví dụ systemd với user khác) sẽ không thấy phiên đăng
     nhập này.
   - **API key:** đặt `"modelProvider": "gemini"` trong `~/.gemini/antigravity-cli/settings.json` và
     `GEMINI_API_KEY` trong `.env` của worker. Chỉ đặt `GEMINI_API_KEY` mà không có `modelProvider` thì không có
     tác dụng.
3. Khởi động lại worker. **Workers** phải thấy capability `antigravity`. Engine không cần `llm-google`, vì nó tự
   xác thực.

Worker chạy `agy -p "<đọc file nhiệm vụ>" --output-format stream-json --dangerously-skip-permissions --print-timeout
<timeout của contract>`. Prompt đầy đủ (role, quy tắc platform, task) nằm trong file `.awf-task.md` trong workspace.
File này không bao giờ được commit, và bị xoá trước khi platform commit. Model: Antigravity tự chọn (mặc định của
CLI). Muốn cố định model thì đặt `AWF_ANTIGRAVITY_MODEL=<tên trong agy models>` trên worker. Chế độ headless không
tự đổi sang model khác khi tên sai. Với gói Google AI Pro, quota tính theo tài khoản, refresh theo chu kỳ của gói.
Chi phí factory hiển thị là ước tính theo giá API, không phải số tiền thật bạn trả.

#### Qwen Code (`coding_engine: qwen-code`)

1. Cài Node.js, rồi `npm i -g @qwen-code/qwen-code` (lệnh `qwen`; hoặc `AWF_QWEN_CODE_BIN`).
2. Đặt `DASHSCOPE_API_KEY` trên worker. Worker truyền key cho Qwen Code qua `OPENAI_API_KEY`, `OPENAI_BASE_URL`
   (endpoint DashScope, theo `AWF_QWEN_BASE_URL` nếu có) và `OPENAI_MODEL`. Gói Qwen OAuth miễn phí đã ngừng từ
   15/4/2026, nên phải dùng API key.
3. Khởi động lại worker. Cần capability `qwen-code` và `llm-qwen`.

Worker chạy `qwen -p "<đọc file nhiệm vụ>" --output-format stream-json --approval-mode yolo --max-session-turns <max_turns>
--max-wall-time <timeout> --model <model>`. Model lấy từ bảng `providers.qwen` (class `CODE_SPECIALIST`). `yolo`
không bật sandbox. Muốn chạy lệnh trong sandbox của Qwen Code thì đặt `AWF_QWEN_CODE_SANDBOX=1` (cần Docker hoặc
Podman trên worker).

#### Kimi Code (`coding_engine: kimi-code`)

1. Cài Kimi Code CLI: Linux/macOS `curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash`, Windows (PowerShell)
   `irm https://code.kimi.com/kimi-code/install.ps1 | iex`. Windows cần Git for Windows; nếu Git Bash nằm chỗ
   khác chuẩn, đặt `KIMI_SHELL_PATH` trỏ tới `bash.exe`.
2. Đăng nhập một lần, bằng tài khoản sẽ chạy worker: chạy `kimi` → `/login` → *Kimi Platform (API key ·
   platform.kimi.ai)* → dán API key → chọn model. Lựa chọn được lưu và dùng lại cho các lần chạy sau.
3. Khởi động lại worker. Cần capability `kimi-code`.

Worker chạy `kimi -p "<đọc file nhiệm vụ>" --output-format stream-json`. Chế độ không tương tác tự duyệt mọi
tool call. Model: lựa chọn đã lưu khi `/login`, hoặc `AWF_KIMI_CODE_MODEL` trên worker. Mã thoát 75 (lỗi tạm thời:
429, 5xx, timeout) thì job được chạy lại; mã 1 (cấu hình, xác thực, hết quota) thì không. Kimi Code không báo số
token, nên chi phí của engine này hiện là $0.

#### DeepSeek Harness (`coding_engine: deepseek-harness`)

DeepSeek Harness (`dsh`) là coding agent mã nguồn mở của DeepSeek (bản 0.1 tháng 8/2026, 0.2 ngày 1/10/2026).
Nó dựng trên các plugin, có Web UI và chế độ headless.

1. Cài: `npm i -g @deepseek-ai/dsh` (lệnh `dsh`; hoặc `AWF_DEEPSEEK_HARNESS_BIN`). Kiểm tra bằng `dsh --version`.
2. Đặt `DEEPSEEK_API_KEY` trong `.env` của worker. Headless đọc key từ môi trường, bỏ qua key và đăng nhập đã
   lưu trong Web UI.
3. Khởi động lại worker. Cần capability `deepseek-harness` và `llm-deepseek`.

Worker chạy `dsh --profile headless --json --permission-mode auto --approval-policy never -`, với prompt qua stdin.
Ở chế độ auto (DeepSeek ghi là thử nghiệm), một bộ review tự động duyệt hoặc từ chối từng tool call. Lần bị từ chối
(`AUTO_REVIEW_DENIED`) được ghi là `engine_denied` trong tool call của agent run. `never` nghĩa là không chờ người
duyệt. Chế độ auto **không có sandbox file**. Model là model mặc định của Harness (`agentDefaultModel` trong cấu
hình của `dsh`, đặt trong Web UI → Settings → Models). Job thành công khi `dsh` thoát 0 **và** sự kiện `final` có
`reason.kind = completed`. Harness không báo số token, nên chi phí của engine này hiện là $0.

#### MiniMax Code (`coding_engine: minimax-code`)

MiniMax Code là coding agent của MiniMax, huấn luyện cùng MiniMax M3. CLI của nó là `mcode`; chế độ headless là
`mcode exec`.

1. Cài Node.js 22.19 trở lên (hoặc 24, 25, 26), rồi `npm i -g @minimax-ai/code` (lệnh `mcode`; hoặc
   `AWF_MINIMAX_CODE_BIN`). Kiểm tra bằng `mcode --version`.
2. Xác thực một lần, bằng tài khoản hệ điều hành sẽ chạy worker, **chọn một** cách:
   - đăng nhập tài khoản MiniMax: `mcode login --region global` (bỏ `--region global` nếu là tài khoản Trung
     Quốc; mặc định là vùng Trung Quốc);
   - API key: đặt biến `MCODE_PROVIDER_API_KEY=<key>` trong cửa sổ lệnh, rồi chạy `mcode provider set-minimax-key`.
     Key được lưu trong thư mục dữ liệu của `mcode` (mặc định `~/.minimax`, đổi bằng `MINIMAX_DATA_DIR`).
3. Khởi động lại worker. Cần capability `minimax-code`.

Worker chạy `mcode exec --output-format json --permission full --timeout <timeout> --max-steps <max_turns> --input -`,
với prompt qua stdin. Model: model đã chọn trong cấu hình `mcode`, hoặc `AWF_MINIMAX_CODE_MODEL` trên worker. Job
thành công khi `mcode` thoát 0 **và** `status` là `succeeded`. Mã thoát 2 (lệnh sai) và 3 (lỗi cấu hình, ví dụ chưa
đăng nhập) thì không chạy lại; mã 4 (lỗi khi chạy), 6 (timeout), 70 (lỗi nội bộ) thì được chạy lại. Số token lấy từ
`usage`, chi phí ước tính theo giá `MiniMax-M3`.

#### Engine "lỏng": cần biết gì

Antigravity, Qwen Code, Kimi Code và MiniMax Code đều **tự duyệt mọi tool call** ở chế độ headless; DeepSeek Harness
để bộ review tự động của nó duyệt. Engine có thể chạy bất kỳ lệnh
nào và truy cập mạng (Qwen Code thì trừ khi bật sandbox). Những gì vẫn giữ an toàn:

- workspace là bản clone dùng một lần, xoá sau job;
- môi trường đã gỡ token của máy (`AWF_*`...), chỉ còn key của chính engine;
- giới hạn thời gian theo contract;
- platform tự build/test, Critic chấm trên đó, platform tự commit; engine đụng git history thì job fail.

Nên dùng các engine này trên máy worker riêng cho factory, không phải máy chứa dữ liệu quan trọng. Nếu cần kiểm
soát chặt, dùng `claude-code` hoặc `codex`.

## 7. Dashboard

`http://<host>:8000/` — đăng nhập bằng `AWF_API_TOKEN` một lần (giữ 30 ngày), hoặc chạy `awf open` trên máy trung tâm để vào thẳng. Xem [mục 21](#đăng-nhập-dashboard-không-phải-nhập-token-mỗi-lần).

| Trang | Nội dung |
|---|---|
| Overview | card số liệu (run đang chạy, chờ duyệt, worker online, chi phí, task lỗi, project; card đổi màu khi cần bạn xử lý), yêu cầu đang chờ duyệt, run đang chạy (tiến độ, stage hiện tại, máy), run vừa kết thúc |
| Projects | danh sách (project đã lưu trữ nằm trong mục *Archived projects*), tạo project (platform, engine, engine dùng cho được **gợi ý sẵn** theo các máy đang online), chi tiết project, bắt đầu run (máy, model, coding engine, **Engine dùng cho**, **Độ khó**; để trống = mặc định của project) |
| Project → Settings | sửa tên, URL repository, base branch, lệnh build/test/lint, confidentiality, budget (để trống = không giới hạn), mô tả. Run đã bắt đầu giữ cấu hình lúc nó bắt đầu |
| Project → Danger zone | project **chưa có run**: *Delete project* (xoá hẳn, audit vẫn ghi lại). Project **đã có run**: *Archive project* (ẩn khỏi danh sách, chặn run mới; run, artifact, audit vẫn xem được; *Restore project* để dùng lại). Run đang chạy phải huỷ trước khi lưu trữ |
| Run | pipeline stage (iteration, trạng thái), độ khó, bảng task có cột **Model · effort**, khối **Đang làm gì** khi run đang chạy (lần thử, tin mới nhất từ worker như model đang thử lại, lỗi của lần thử trước kèm gợi ý sửa), bảng task (critic decision + verdict), approval chờ, artifact |
| Task (click dòng) | tiêu chí chấp nhận, feedback, agent run (model, route reason, token, chi phí, critic findings, tool calls), log job |
| Artifact (click dòng) | nội dung, checksum, quan hệ trace, impact analysis |
| Approvals | hàng đợi duyệt + lịch sử; `approve_effort` ghi rõ task, model và effort xin dùng |
| Lỗi (nút ở đầu trang) | nhật ký lỗi của trình duyệt này: thời gian, trang, nội dung đầy đủ; Sao chép từng lỗi hoặc tất cả; Xoá hết |
| Workers | platform, capability, tải, heartbeat |
| Agents | KPI theo role: số run, success, retry, critic rejection, latency, chi phí |
| Audit | toàn bộ sự kiện |
| Notifications | kênh Telegram/WhatsApp/Zalo, tin đã gửi, gửi tin thử |

### Ngôn ngữ giao diện (English / Tiếng Việt)

Dashboard có hai ngôn ngữ: **English** và **Tiếng Việt**. Chọn ở ô ngôn ngữ trên thanh tiêu đề, cạnh nút
Đăng xuất. Lựa chọn được lưu trong trình duyệt (localStorage) và dùng lại mỗi lần mở sau, kể cả ở ứng dụng đã cài
trên điện thoại. Mỗi trình duyệt/thiết bị nhớ riêng. Lần đầu mở, dashboard theo ngôn ngữ của trình duyệt (tiếng
Việt nếu trình duyệt đặt tiếng Việt, còn lại là English).

Phần được dịch là mọi chữ của giao diện: menu, tiêu đề, nút, nhãn, trạng thái, gợi ý sửa lỗi. Phần giữ nguyên:
- dữ liệu: tên project, objective, tên stage trong workflow YAML, nội dung artifact;
- mã định danh: `approve_architecture`, loại sự kiện audit, tên capability;
- thông báo lỗi do API trả về (tiếng Anh). Dưới các lỗi hay gặp, dashboard thêm một dòng giải thích bằng ngôn ngữ
  đã chọn.

**Quy tắc khi sửa giao diện** (bắt buộc với mọi thay đổi về giao diện, ghi cả trong `CLAUDE.md` ở gốc repo):

1. Không viết chữ hiển thị trực tiếp trong `app.js`/`index.html`. Thêm key vào **cả hai** bộ `en` và `vi` trong
   `apps/control_api/awf_api/static/i18n.js`, rồi dùng `t("key")`. Chuỗi theo số lượng dùng `tn("key", n)` với
   hai key `key.one`/`key.other`. Trong `index.html` dùng thuộc tính `data-i18n="key"` (hoặc `data-i18n-title`
   cho tooltip).
2. Giá trị chèn vào bằng `{tên}` được `t()` escape. Chuỗi trong từ điển được phép chứa markup (`<code>`, `<b>`).
3. Trạng thái mới, platform mới, engine mới: thêm key `status.*`, `platform.*`, `engine.*` cho cả hai ngôn ngữ.
4. Chạy `pytest tests/integration/test_i18n.py`. Test fail khi một ngôn ngữ thiếu key, khi có `t("…")` chưa được
   dịch, hoặc khi `{placeholder}` giữa hai ngôn ngữ khác nhau.

### Cài dashboard lên điện thoại (ứng dụng PWA)

Dashboard cài được như một ứng dụng: có biểu tượng trên màn hình chính, mở toàn màn hình (không thanh địa chỉ),
số yêu cầu chờ duyệt hiện trên biểu tượng, và vẫn mở được khi mất mạng (hiện thông báo thay vì trang trắng).

Điều kiện: điện thoại mở dashboard qua **HTTPS**. Cách đơn giản nhất là Tailscale + `tailscale serve` (mục 21,
"Cách A"): `https://factory.<tailnet>.ts.net`. Với `http://...:8000` vẫn dùng được trên trình duyệt nhưng không cài
được thành ứng dụng (trình duyệt chỉ cho phép trên HTTPS).

- **Android (Chrome):** mở URL → đăng nhập → trên trang Overview bấm **Install app** (hoặc menu ⋮ → *Cài đặt ứng
  dụng*). Giữ lâu biểu tượng để vào nhanh *Duyệt* hoặc *Máy*.
- **iPhone/iPad (Safari):** mở URL → nút **Chia sẻ** → **Thêm vào MH chính**. Ứng dụng trên màn hình chính có bộ
  nhớ riêng, nên **đăng nhập lại một lần** trong ứng dụng (phiên giữ 30 ngày).
- **Máy tính (Chrome/Edge):** biểu tượng cài đặt trên thanh địa chỉ.

Cách hoạt động:

- Ứng dụng chỉ lưu trên máy phần "vỏ" (trang, script, CSS, biểu tượng). Dữ liệu run, artifact, yêu cầu duyệt
  **không bao giờ** được lưu trên điện thoại: luôn lấy trực tiếp từ máy chủ và cần đăng nhập.
- Đang mở: tự cập nhật mỗi 4 giây. Chuyển sang ứng dụng khác hoặc tắt màn hình: dừng cập nhật (đỡ tốn pin), mở
  lại là cập nhật ngay.
- Mất kết nối (mất mạng, Tailscale tắt, máy chủ khởi động lại): dữ liệu đang xem vẫn giữ, kèm dải vàng báo mất
  kết nối, tự thử lại.
- Cập nhật phiên bản mới (sau `git pull` + khởi động lại `awf` trên máy chủ): ứng dụng tự lấy bản mới ở lần mở kế
  tiếp, không cần cài lại.
- Thông báo đẩy khi có việc cần duyệt: dùng Telegram/Zalo/WhatsApp (mục 8); tin có link mở thẳng trang duyệt.

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
→ `CHANGES_REQUIRED` → task chạy lại với danh sách lỗi. Critic `BLOCK` (khoá thật trong output, liên tục
vượt quyền) → dừng run và chờ người.

**Secret trong diff:** Critic đọc diff theo từng dòng.

| Trong diff | Kết quả |
|---|---|
| Dòng **bị xoá** có mật khẩu (ví dụ gỡ `AdminPassword` khỏi `appsettings.json`) | Bỏ qua: đó là việc sửa lỗi, không phải rò rỉ |
| Mật khẩu mẫu trong dấu nháy, ở **file test** (`tests/`, `*Tests.cs`, `test_*.py`…) | Ghi chú `MINOR`, task vẫn qua |
| Mật khẩu hoặc token trong dấu nháy, ở **code chạy thật** | `MAJOR`: task được gửi lại, kèm yêu cầu đọc giá trị từ cấu hình hoặc biến môi trường |
| Khoá có định dạng thật (`sk-ant-…`, `AIza…`, `ghp_…`, private key) hoặc giá trị secret của máy | `BLOCK`: run dừng |

Trong artifact và log, khoá thật hiện thành `[REDACTED]`, giá trị trong dấu nháy hiện thành `[REDACTED-VALUE]`.

**Chạy lại run đã fail:** trang run có nút **Chạy lại run**. Task đã fail chạy lại, rồi tới các task bị huỷ
theo nó. Các task đã xong giữ nguyên kết quả và code đã tích hợp. API: `POST /api/v1/workflow-runs/{id}/retry`,
hoặc `POST /api/v1/tasks/{id}/retry` cho một task. Run đã **huỷ** (Cancel) thì không chạy lại được.
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
chứa nhánh nguồn/đích, diffstat, danh sách commit, trace artifact.

Merge vào nhánh chính:

- **Repo chung trên máy trung tâm** (mục 5a): sau khi bạn duyệt `approve_merge`, stage *Merge into the base
  branch* fast-forward `main`. Trên PC: `git pull factory main`.
- **Repo ở nơi khác** (GitHub...): stage đó chỉ ghi "merge it yourself". Merge vẫn do bạn tự làm, qua PR (policy
  level 2).

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
| DELETE | `/projects/{id}` | xoá project chưa có run nào (409 nếu đã có run: dùng archive) |
| POST | `/projects/{id}/archive`, `/projects/{id}/restore` | lưu trữ / khôi phục; `GET /projects?archived=true` liệt kê project đã lưu trữ |
| GET | `/workflows`, `/agents` | định nghĩa workflow, agent |
| GET | `/models` | nhà cung cấp model (Claude, Gemini) và model của từng class |
| POST | `/projects/{id}/workflow-runs` | `{workflow_id, inputs: {objective}, placement?: {worker}, llm?: "anthropic"\|"google", coding_engine?: "native"\|"claude-code"}` |
| GET | `/projects/{id}/workflow-runs`, `/workflow-runs/{id}` | run + stage overview + chi phí + máy được giao (`assigned_worker`, `requirements`, `placement.wait`) |
| POST | `/workflow-runs/{id}/reassign` | `{worker}` chuyển run sang máy đó; `{}` để orchestrator chọn lại |
| POST | `/workflow-runs/{id}/cancel` | huỷ |
| GET | `/workflow-runs/{id}/tasks`, `/tasks/{id}` | task, agent run, job, approval, artifact |
| POST | `/workflow-runs/{id}/retry` | chạy lại run FAILED (task fail và các task bị huỷ theo nó) |
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
.\.venv\Scripts\python -m pytest -q -p no:logging            # toàn bộ (276 test)
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
| `critic blocked: ... credential-like material in <file>` | Diff thêm một khoá có định dạng thật vào `<file>`. Gỡ khoá khỏi repo, dùng biến môi trường, rồi bấm **Chạy lại run**. Bản cũ (trước 29/10/2026) chặn cả việc xoá mật khẩu cũ và mật khẩu mẫu trong test: cập nhật máy trung tâm rồi bấm **Chạy lại run**. |
| Chạy lại run thì task báo `[MAJOR/NO_CHANGES] task requires source changes but none were committed` | Lần chạy trước của task đã đưa code vào nhánh run, nên lần này không còn gì để sửa. Từ 29/10/2026, platform tính phần code đó là thay đổi của task (ghi chú `CHANGES_FROM_EARLIER_ATTEMPT`). Cập nhật máy trung tâm rồi bấm **Chạy lại run**. |
| Mirror báo `rejected` / `fetch first` | Ai đó đã push thẳng lên GitHub. Trên PC: `git pull <github> main`, rồi `git push factory main`. Mirror tự thử lại sau 5 phút, hoặc bấm **Đẩy ngay**. |
| Mirror báo `401` / `403` / `Authentication failed` | `AWF_MIRROR_TOKEN` trên máy trung tâm thiếu, hết hạn hoặc không có quyền *Contents: write* với repo đó. Sửa `.env` rồi khởi động lại. |
| Release báo `conflict in: …` | `main` có hotfix chạm cùng chỗ với `develop`. Merge trên PC theo lệnh dashboard in ra, rồi push `main` lên factory. |
| `reference .awf-refs/<tên>: NOT AVAILABLE` trong log job | URL hoặc nhánh của repo tham khảo sai, hoặc worker không có quyền đọc repo đó. Repo trên GitHub cần worker tự có quyền (SSH key hoặc credential của máy đó). |
| Run fail, các task còn lại `CANCELLED` | Bấm **Chạy lại run** trên trang run: task fail chạy lại, các task bị huỷ chạy tiếp. |
| `INTEGRATION_CONFLICT` | Hai task song song sửa cùng chỗ; Planner nên đặt `depends_on`. |
| `no model available for class LOCAL_PRIVATE` | Project `restricted`; chưa có model cục bộ. |
| `model provider rejected the request` | Thiếu/sai `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` trên worker. |
| `no credit or quota left on the <nhà cung cấp> API account` | Tài khoản API hết credit (Claude: "credit balance is too low"), hết quota ngày, hoặc key free tier không được dùng model đó (Gemini: `limit: 0`, thường gặp với `gemini-4-argon`). Job fail ngay, không thử lại. Nạp credit hoặc bật billing cho key; gói Pro/Plus **không** trả tiền cho API key. Hoặc chạy cả run bằng gói (mục 6, "Chạy cả run bằng gói Pro"). |
| Task đứng ở `APPROVAL_REQUIRED`, có yêu cầu `approve_effort` | Task sắp chạy với effort high/xhigh (run *Khó*/*Rất khó*, hoặc task bị trả về rồi tự leo mức). Duyệt để chạy đúng mức; từ chối để chạy ở medium (mục 6, "Model và effort cho từng role"). |
| Không kịp đọc lỗi trên dashboard | Lỗi dưới form giờ được giữ tới khi bạn bấm ×. Mọi lỗi còn nằm trong **nhật ký lỗi**: nút **Lỗi** ở đầu trang, mới nhất ở trên, có nút **Sao chép** và **Chép tất cả**. |
| Start run báo `has no 'main' branch yet: push your code first` | Repo trên máy trung tâm còn trống: `git push factory main` từ PC (mục 5a). |
| `git push factory` hỏi mật khẩu, hoặc báo `401` | Tên người dùng tuỳ ý, mật khẩu là token ở mục **Git remote** của trang project. Token đã đổi thì xoá mật khẩu cũ trong Credential Manager của Windows (mục `git:https://<máy trung tâm>`). |
| Worker báo `403 ... workers may only push awf/* branches` | Worker cố ghi một nhánh khác `awf/*`. Chỉ job merge (sau khi duyệt) mới được ghi `main`. Nếu gặp ở job thường, hãy báo lỗi kèm log job. |
| Task "Resolve conflict: …" xuất hiện trong run | Hai task, hoặc run và `main`, cùng sửa một chỗ. Agent đang giải. Nếu nó không giải được, bạn nhận yêu cầu `resolve_conflict` kèm lệnh (mục 5a). |
| `after rebasing onto the latest main ... fails` | Một thay đổi bạn push lên `main` trong lúc run chạy làm hỏng build/test của run. Sửa trên `main`, hoặc chạy lại run. |
| Run đứng ở 0%, stage đầu `active` rất lâu | Mở trang run, xem khối **Đang làm gì**: nó hiện lần thử của task, tin mới nhất từ worker (ví dụ `[model] Gemini API 503 ... high demand; retrying in 4s`) và lỗi của lần thử trước. |
| `... did not answer within 240s (model busy or overloaded)` / `high demand` / `no time left for another attempt` | Model đang quá tải, không trả lời. Mỗi request chờ tối đa 240 giây; mọi lần thử lại nằm trong giới hạn thời gian của agent (mặc định 900 giây), rồi job được xếp chạy lại. Lặp lại nhiều lần: đổi model/nhà cung cấp, hoặc chờ hãng hết quá tải. |
| `engine result rejected: ...` | Engine chạy một role chỉ đọc (Engine dùng cho = Mọi role) nhưng khối `awf-result` thiếu hoặc sai contract, cả sau lần làm lại. Job được chạy lại; lặp lại thì dùng engine hoặc model khác cho run đó. |
| Engine Claude Code ở chế độ `login` báo `Not logged in` / `Invalid API key` | Chưa đăng nhập `claude` bằng đúng tài khoản hệ điều hành chạy worker; hoặc thiếu `AWF_CLAUDE_CODE_AUTH=login` nên engine vẫn dùng API key trong `.env`. |
| Tạo run lỗi `missing llm-google` (hoặc `llm-anthropic`) | Không máy nào có key của nhà cung cấp đó: đặt key trong `.env` của worker rồi khởi động lại worker. |
| Tạo run lỗi `missing llm-qwen` / `llm-kimi` / `llm-deepseek` / `llm-minimax` | Không máy nào có `DASHSCOPE_API_KEY` / `MOONSHOT_API_KEY` / `DEEPSEEK_API_KEY` / `MINIMAX_API_KEY` (mục 6). |
| Tạo run lỗi `missing antigravity` / `qwen-code` / `kimi-code` / `deepseek-harness` / `minimax-code` | Chưa cài CLI tương ứng (`agy`, `qwen`, `kimi`, `dsh`, `mcode`) trên worker, hoặc chưa khởi động lại worker sau khi cài (mục 6). |
| `minimax-code failed: ...` với mã thoát 3 | `mcode` chưa đăng nhập hoặc chưa có provider/key: chạy `mcode login` hoặc `mcode provider set-minimax-key` bằng đúng tài khoản chạy worker. |
| `antigravity ERROR: authentication required` | `agy` chưa đăng nhập trên máy worker, hoặc worker chạy bằng tài khoản hệ điều hành khác. Chạy `agy` một lần bằng đúng tài khoản đó. |
| `... insufficient_balance` / HTTP 402 | Hết số dư API của hãng (DeepSeek, Kimi...). Nạp tiền; job không tự chạy lại. |
| `... reasoning_content ... must be passed back` | Không xảy ra với adapter của factory (nó gửi lại `reasoning_content`). Nếu gặp, báo lỗi kèm log job. |
| Tạo run lỗi `missing llm-openai` | Không máy nào có `OPENAI_API_KEY`: đặt key trong `.env` của worker rồi khởi động lại worker (mục 6). |
| Tạo run lỗi `missing codex` | Run/project chọn engine Codex nhưng không máy nào có lệnh `codex` (`npm i -g @openai/codex`) hoặc `AWF_CODEX_BIN`. |
| `codex: ... Incorrect API key` / `insufficient_quota` | Sai `OPENAI_API_KEY` hoặc hết credit OpenAI; không retry. |
| Tạo run lỗi `missing claude-code` | Run/project chọn engine Claude Code nhưng không máy nào có `claude` trên PATH (hoặc `AWF_CLAUDE_CODE_BIN`). Cài Claude Code trên worker, đăng nhập hoặc đặt `ANTHROPIC_API_KEY`, rồi khởi động lại worker (mục 6). |
| Tạo run lỗi `engine scope 'all' needs a coding engine` | Đã chọn **Engine dùng cho = Mọi role** mà **Coding engine** vẫn là Native. Chọn Claude Code hoặc Antigravity. |
| `claude-code error_max_turns` / `timed out` | Task quá lớn so với `max_turns`/timeout của contract Developer; chia nhỏ objective hoặc tăng `timeout_policy` trong `agents/contracts/engineering.developer.yaml`. |
| `ENGINE_TOUCHED_GIT` | Engine tự commit hoặc đổi nhánh; chỉ platform được commit. Không retry: xem log job. |
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

Nên cập nhật máy trung tâm trước, rồi đến các worker. Thay đổi nào chỉ cần cập nhật máy trung tâm, thay đổi nào
cần cả worker, và restart worker lúc nào để không phải chạy lại job: mục 22.

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

### Thêm nhà cung cấp hoặc engine mới (cho người phát triển)

Danh sách nhà cung cấp và engine nằm ở một vài chỗ cố định. Thêm một hãng mới:

1. `packages/awf/agent_runtime/providers.py`: thêm `Provider(id, nhãn, api, biến key, nơi lấy key, base URL)`.
   Hãng nói Chat Completions tương thích OpenAI thì dùng `api="chat_completions"`, không cần viết adapter.
   Worker, router và doctor đều tự đọc từ bảng này.
2. `config/model-routing.yaml`: thêm bảng `providers.<id>` (model cho từng class, `extra` nếu cần) và giá trong
   `pricing`.
3. Dashboard: thêm vào `LLM_NAMES` và `KEY_HELP` trong `static/app.js`. Chuỗi hiển thị phải có đủ hai ngôn ngữ
   (`static/i18n.js`, xem `CLAUDE.md`).
4. Engine viết code (nếu hãng có CLI headless): thêm vào `CODING_ENGINES` (`packages/awf/orchestrator/task_router.py`),
   viết phần chạy CLI và đọc output trong `packages/awf/worker_sdk/` (mẫu: `cli_engines.py`), thêm tên engine vào
   `engines:` của `agents/profiles/engineering.developer.yaml`, `ENGINES`/`ENGINE_HELP` trong `app.js` và key
   `engine.<tên>` trong `i18n.js`.
5. Test: mẫu ở `tests/integration/test_more_providers.py` (API giả lập, CLI giả trong `tests/fakes/`).
6. Tài liệu: bảng tổng hợp và mục lấy key ở mục 6, bảng sự cố ở mục 19, và một ADR.

### Đổi dependency

Sửa `pyproject.toml`, rồi trên Python 3.12:

```bash
pip install -e ".[dev,postgres]" && pip freeze --exclude-editable > requirements.lock
```

Giữ dòng `colorama ... ; sys_platform == "win32"`, rồi commit. `awf doctor` báo `FAIL` nếu máy nào còn lệch
lock.

## 22. Câu hỏi thường gặp

### Factory có giống Claude Code, Codex, Antigravity chạy local không?

Không. Factory **giao việc rồi chờ kết quả**, không có session chat với agent:

- Bạn đưa một objective. Các agent tự chạy hết pipeline, không cần ai ngồi xem.
- Agent **không hỏi bạn giữa chừng.** Chỗ chưa rõ được ghi vào tài liệu agent sinh ra (requirement,
  architecture; với content là mục *Open questions*), và bạn đọc chúng khi tới gate. Vì vậy objective cần cụ thể,
  có phạm vi và tiêu chí kiểm chứng được (mục 5, Bước 5).
- Bạn can thiệp ở các gate (mục 8):
  - `approve_architecture`: **Approve**, hoặc **Reject kèm comment**. Architect làm lại với comment của bạn làm
    phản hồi. Đây là cách duy nhất để "nói" với agent trong một run.
  - `approve_merge`: Approve, hoặc Reject. Reject ở gate này kết thúc run (`FAILED`).
- Kênh duyệt:

  | Kênh | Làm được gì |
  |---|---|
  | Dashboard (cả trên điện thoại, mục 7) | Duyệt, Reject kèm comment, xem log, artifact, chi phí |
  | Telegram | Thông báo + nút Duyệt/Từ chối. Từ chối từ Telegram không kèm được ý kiến của bạn: muốn Architect sửa theo ý mình thì Reject trên dashboard |
  | Zalo, WhatsApp | Chỉ thông báo, có link mở trang duyệt trên dashboard |

Việc cần hỏi đáp qua lại liên tục thì vẫn nên dùng Claude Code/Codex local. Factory hợp với việc giao được bằng
một objective rõ ràng, chạy không cần người theo dõi, có quy trình và bằng chứng build/test.

### Project, run, task khác nhau thế nào? Mỗi project là một repo mới?

- **Project = một repository có sẵn** + lệnh build/test/lint, platform, ngân sách. Tạo **một lần** cho mỗi repo,
  không tạo repo mới. Ngoại lệ: workflow `content-production` không cần repository (mục 9b).
- **Mỗi objective (prompt) = một run**, chạy trọn quy trình từ requirement tới duyệt merge, trên nhánh
  `awf/run-<id>`. Một project có nhiều run.
- **Một run có nhiều task.** Planner chia objective thành các task phát triển, có thể chạy song song. Review, QA
  và verification có thể đẩy run quay lại sửa (fix loop). Objective nên cỡ một tính năng hoặc một thay đổi trọn
  vẹn, không vụn như "sửa dòng 42".
- **Run mới luôn bắt đầu từ `base_branch`.** Chưa merge `awf/run-<id>` của run trước vào `main` thì run sau
  **không thấy** các thay đổi đó. Không có kiểu "chat tiếp trên run cũ": muốn sửa thêm thì merge, rồi tạo run mới.
  Hai run song song trên cùng repo có thể xung đột khi bạn merge.

### Chỉ làm việc được với GitHub, không dùng được repo local?

Không phải. `repository.url` nhận mọi thứ `git clone` nhận: đường dẫn local, bare repo, ổ mạng, SSH, Gitea/GitLab
tự host, GitHub. Điều kiện duy nhất: **worker chạy job phải clone và push được URL đó** (worker clone mới cho mỗi
job, rồi đẩy nhánh `awf/run-*`, `awf/tasks/*` về đó).

| Tình huống | Dùng gì |
|---|---|
| Worker và repo trên cùng một máy | Đường dẫn local, ví dụ `D:/factory/repos/X.git`. Nên dùng bare mirror (mục 5, Bước 1) |
| Nhiều máy | Git remote, vì `D:/...` chỉ đúng trên một máy: GitHub/GitLab, hoặc không cần GitHub: bare repo trên máy trung tâm, truy cập qua SSH trong tailnet |

Ví dụ bare repo trên máy trung tâm, không dùng GitHub:

```bash
# máy trung tâm (Ubuntu): tạo bare repo từ repo có sẵn
git clone --bare <repo hiện có> ~/repos/X.git
# trên từng worker: kiểm tra clone được bằng SSH key (bật SSH trên máy trung tâm, hoặc `sudo tailscale up --ssh`)
git clone ssh://<user>@factory/home/<user>/repos/X.git
```

Rồi đặt `repository.url = ssh://<user>@factory/home/<user>/repos/X.git` cho project.

Factory **không gọi API của GitHub**: không tự mở PR, không tự merge, không bao giờ ghi vào base branch. Kết quả là
nhánh `awf/run-<id>`, file patch và `pull_request.md`; merge là việc của bạn (mục 9).

### Vừa chạy factory, vừa dùng app Claude/Codex/Antigravity local được không?

Được, chạy song song không ảnh hưởng nhau. Thường hai bên **không dùng chung một túi token**.

Factory gọi API bằng `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` trong `.env` của worker, tính tiền theo lượng dùng:

| Bên | Trả tiền bằng | Có ăn vào quota của app local không? |
|---|---|---|
| Factory, Claude | Credit của API key (Anthropic Console) | Không, nếu app Claude/Claude Code đăng nhập bằng gói Pro/Max: gói thuê bao và API là hai nguồn riêng |
| Factory, Gemini | Key từ AI Studio / Google Cloud | Antigravity dùng quota tài khoản Google riêng; chỉ chung khi dùng cùng key hoặc cùng project Google Cloud |
| Factory, ChatGPT | Credit của API key (platform.openai.com) | Không: gói ChatGPT Plus/Pro và OpenAI API là hai nguồn riêng |
| Engine Codex (mục 6) | `api-key`: credit API. `login`: gói ChatGPT Plus/Pro đã `codex login` | `login`: **có**, chung lượt dùng với Codex bạn dùng trên máy cùng tài khoản |
| Engine Antigravity (Gemini) | Quota gói Google AI Pro/Ultra nếu `agy` đăng nhập bằng tài khoản Google; credit API nếu dùng API key | **Có**, khi đăng nhập Google: chung quota với Antigravity bạn dùng trên máy cá nhân cùng tài khoản |
| Factory, Qwen / Kimi / DeepSeek / MiniMax | Số dư API của từng hãng | Không: API key riêng. Engine Kimi Code dùng API key Kimi đã `/login`; engine MiniMax Code dùng tài khoản `mcode login` hoặc key đã lưu |
| Factory, engine Claude Code (mục 6) | `api-key`: credit của API key, như dòng Claude ở trên. `login`: gói Claude Pro/Max đã đăng nhập bằng `claude` | `api-key`: không. `login`: **có**, chung lượt dùng với Claude Code bạn dùng trên máy cùng tài khoản |

- App local dùng **cùng API key / cùng tổ chức** trên Console thì hai bên chung credit và chung rate limit. Dùng
  nặng cùng lúc có thể bị giới hạn tốc độ. Factory tự thử lại (tối đa 4 lần mỗi request khi gặp 429/5xx hoặc
  quá 240 giây không trả lời, luôn trong giới hạn thời gian của agent; job lỗi tạm thời được chạy lại); app local
  thì có thể chậm hoặc báo lỗi.
- **Hết hẳn credit API:** provider từ chối, factory coi là lỗi không thử lại được, nên job **fail ngay**, không
  đứng chờ. Nạp credit rồi chạy lại. `budget_usd` của project là ngân sách **cộng dồn cho mọi run** của project: khi còn ít, router tự hạ xuống model rẻ hơn (mục 6).
- Worker clone repo vào `var/workspaces/<job_id>` và xoá sau khi xong. Nó **không động vào thư mục repo** bạn
  đang mở trong app local, chỉ đẩy các nhánh `awf/*`.
- Worker chạy trên chính PC bạn đang dùng thì job build/test chiếm CPU/RAM; giữ `AWF_WORKER_MAX_JOBS=1` nếu máy
  chậm.

### Máy worker không có `AWF_API_TOKEN`, dashboard lại đòi token?

Đúng thiết kế. `AWF_API_TOKEN` là token quản trị dashboard, **chỉ máy trung tâm có**. Worker chỉ cần
`AWF_WORKER_REGISTRATION_TOKEN` để đăng ký; sau đó máy trung tâm cấp token riêng cho worker (lưu trong `var/`).
Worker bị lộ cũng không vào được dashboard.

Dashboard chỉ có một, chạy trên máy trung tâm: mở `<URL>` từ trình duyệt nào (máy worker, laptop, điện thoại)
cũng là vào máy trung tâm. Lấy token **trên máy trung tâm**:

```bash
grep ^AWF_API_TOKEN= .env                # dán vào ô đăng nhập một lần, giữ 30 ngày
.venv/bin/awf open --print-only          # hoặc: link đăng nhập sẵn, mở trong vòng 2 phút
```

### Cập nhật source awf có bắt buộc pull và restart mọi máy không?

Không phải lúc nào cũng bắt buộc. Tùy thay đổi nằm ở đâu:

| Phần thay đổi | Ai đọc | Cập nhật máy nào |
|---|---|---|
| `workflows/`, `agents/` (contract, profile, prompt), `knowledge/`, `config/model-routing.yaml`, dashboard, API, orchestrator, migration | Máy trung tâm. Prompt và contract được gửi kèm từng job | Chỉ máy trung tâm |
| `config/policies.yaml` (command allowlist, timeout), `packages/awf/worker_sdk`, `tool_sdk`, `agent_runtime` (adapter Claude/Gemini) | Worker, đọc từ source của chính nó | Các worker |
| `requirements.lock` | Mọi máy | Mọi máy, chạy lại bootstrap (`awf doctor` báo FAIL nếu lệch) |
| Giao thức giữa API và worker | Cả hai | Tất cả, máy trung tâm trước |

Ví dụ: sửa prompt hoặc thêm workflow thì chỉ cập nhật máy trung tâm; thêm lệnh vào allowlist thì cập nhật worker.

Hai điều cần nhớ:

1. **Máy được cập nhật phải restart**: tiến trình Python không tự nạp lại code.
2. **Không có kiểm tra phiên bản giữa máy trung tâm và worker**: worker chạy code cũ vẫn đăng ký và nhận job, không
   có cảnh báo. Khi không chắc, **cập nhật tất cả, máy trung tâm trước** (mục 21, "Cập nhật mọi máy"):

```bash
git pull && scripts/bootstrap.sh && sudo systemctl restart awf               # Linux (systemd)
```

```powershell
git pull; .\scripts\bootstrap.ps1; .\scripts\install-task.ps1 -Restart       # Windows (Task Scheduler)
```

Restart lúc nào thì không mất việc:

- **Máy trung tâm:** lúc nào cũng được. Worker chạy tiếp job đang dở và nộp lại kết quả khi máy trung tâm lên lại
  (mục 21); migration tự chạy khi khởi động.
- **Worker:** nên restart **khi rảnh** (trang **Workers**). Job đang dở bị giao lại và **chạy lại từ đầu**, tốn
  thêm token.
