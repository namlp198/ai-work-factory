# Trial run — InternalFileServer (repo thật)

**Ngày:** 2026-10-10 · **Repo:** `D:\src\ai-workspace\06_Repositories\InternalFileServer` (.NET 8 minimal API, 7 file, chưa có test)
**Kết quả:** `COMPLETED` trong ~1,5 phút, 1 vòng fix loop, 2 gate được duyệt.

## Cách chạy

- Control plane: `awf db upgrade` (Alembic, SQLite riêng cho trial) + `awf api --embedded-orchestrator`.
- Worker: `awf worker` native trên Windows — tự phát hiện `windows-amd64` và capability
  `cmake, ctest, cuda, docker, dotnet, git, node, npm, python`.
- Project: `labels.platform=windows`, `required_capabilities=[dotnet]`, build `dotnet build -warnaserror`,
  test `dotnet test tests/InternalFileServer.Tests`, budget $20.
- **Repo gốc không bị đụng:** origin của factory là bare mirror `var/trial/InternalFileServer.git`.
- **Model:** chế độ scripted (máy không có `ANTHROPIC_API_KEY`). Nội dung của agent lấy từ
  `examples/internal-file-server/scripted-responses.yaml`; **mọi thứ khác là thật**: clone sandbox, nhánh git,
  merge, `dotnet build`, `dotnet test`, critic, gate, fix loop, artifact, audit.
  Tái lập: `examples/internal-file-server/run_trial.ps1`.

Objective:

> Add GET /api/health (status, version, storage writable, admin configured) and remove the insecure change-me
> admin password fallback: deletion is disabled when no admin password is configured, and password comparison
> is constant-time. Add an xUnit test project.

## Diễn biến

| Stage | Iter | Kết quả | Ghi chú |
|---|---|---|---|
| requirement | 1 | DONE (critic PASS) | FR/NFR/AC có ID |
| architecture | 1 | DONE sau **duyệt** `approve_architecture` | architecture + ADR + component diagram |
| planning | 1 | DONE | 1 task (T1) |
| development T1 | 1 | DONE | evidence: build pass, **11/11 test pass** |
| verification | 1 | DONE (test engineer APPROVE, static analysis APPROVE) | |
| review | 1 | **CHANGES_REQUIRED** | MAJOR: `/api/health` lộ đường dẫn tuyệt đối của thư mục upload |
| development fix | 2 | DONE | xoá `StoragePath`, thêm regression test; **12/12 pass** |
| verification / review | 2 | DONE / APPROVE | |
| qa | 1 | APPROVE | chạy lại test: 12/12 |
| delivery | 1 | DONE | `patch.patch` + `pull_request.md` |
| merge_approval | 1 | DONE sau **duyệt** `approve_merge` | |

Nhánh trên mirror: `awf/run-0af3d47f` (2 merge commit `--no-ff` từ `awf/tasks/0af3d47f/*`).
Thay đổi: 8 file, +201/−7 — `AdminAuth.cs`, `HealthCheck.cs`, `Program.cs`, `InternalFileServer.csproj`
(loại `tests/**` khỏi web project), `README.md`, project `tests/InternalFileServer.Tests` (xUnit).
Không có `bin/`/`obj/` nào bị commit.

## Kiểm chứng an toàn

- Repo gốc: không có nhánh hay thay đổi mới.
- `appsettings.json` của repo **đang chứa một mật khẩu admin thật được commit**. Đếm số lần giá trị này xuất hiện
  trong artifact store, database, log API và log worker của trial: **0**. Redaction được áp dụng cả với output
  tool trước khi gửi cho model.

## Vấn đề phát hiện trong quá trình trial và đã sửa trong nền tảng

| Vấn đề | Sửa |
|---|---|
| Redaction không nhận dạng `"AdminPassword": "..."` (JSON) | Pattern mới cho giá trị có quote (JSON/YAML/code) + dòng kiểu `.env`; không redact code như `adminPassword = config[...]` (tránh critic BLOCK oan) |
| Output tool (vd. `fs.read appsettings.json`) có thể đưa secret vào prompt | `ToolResult.for_model()` luôn redact |
| `dotnet test` không được đếm số test | Parser hỗ trợ `Passed: N, Failed: N` |
| `bin/`, `obj/` của .NET có thể bị commit | Thêm vào `.git/info/exclude` của sandbox |
| `fs.replace` trên Windows đổi line ending | Đọc/ghi dạng bytes, tự khớp CRLF |

## Khuyến nghị cho repo InternalFileServer

1. **Xoay vòng mật khẩu admin đang commit** trong `appsettings.json`, xoá nó khỏi file và cấp qua biến môi trường
   `FileServer__AdminPassword` (sau thay đổi này, thiếu mật khẩu = tắt xoá file, không còn fallback `change-me`).
   Mật khẩu cũ vẫn nằm trong lịch sử git → coi như đã lộ.
2. Thêm `.gitignore` cho `bin/`, `obj/`, `artifacts/` (hiện là untracked).
3. Nếu muốn dùng thay đổi: `git fetch D:/src/ai-work-factory/var/trial/InternalFileServer.git awf/run-0af3d47f`
   rồi review như một PR bình thường.
4. Chạy lại với Claude thật (đặt `ANTHROPIC_API_KEY` trên worker, bỏ `AWF_MODEL_PROVIDER=scripted`) để
   đánh giá chất lượng agent trên cùng objective.
