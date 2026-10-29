# Lộ trình AWF (từ 28/10/2026)

Kế hoạch tổng hợp từ các lần thảo luận:
- đánh giá ba bài review (ChatGPT, Gemini, DeepSeek);
- so sánh với OpenClaw và shuanbao0/agent-factory;
- phương pháp SDD và bộ tiêu chuẩn SDD v1.1 ([`standards/sdd/`](../../standards/sdd/README.md)).

Từ nay, mọi thay đổi không thuộc diện miễn trừ của AWF đều đi theo SDD v1.1: spec → duyệt → design → plan → code
→ verify. Change folder nằm trong [`docs/sdd/changes/`](../sdd/changes/).

## Nguyên tắc định hướng

1. **AWF là lõi factory; giá trị nằm ở bằng chứng.** Không thay runtime bằng OpenClaw, không fork agent-factory.
   OpenClaw chỉ là ý tưởng tham khảo, và có thể là một cổng chat tuỳ chọn sau này.
2. **Quy trình của repo thắng; AWF thích nghi theo.** AWF là người thực thi và thu bằng chứng bên trong quy trình
   của repo đích. Nó chỉ dựng quy trình SDD khi repo chưa có quy trình nào.
3. **Một bộ tài liệu, một nơi khai báo gate, một checker.** Spec, design, plan và verification nằm trong repo đích;
   gate khai báo trong `project-profile.md`; checker giống nhau ở CI và trong AWF.
4. **Kiểm chứng trước khi mở rộng.** Chưa thêm stage hay tính năng mới lên trên những phần chưa từng chạy thật.
5. **Bằng chứng do máy sinh ra.** `verification.md` và bảng truy vết được sinh từ kết quả chạy thật, không do agent
   tự khai.

## Các giai đoạn

Ký hiệu: **[bạn]** cần bạn làm hoặc duyệt; **[AWF]** mình làm. Mỗi mục có tiêu chí "đạt khi".

### Giai đoạn 0 — Chạy thật những gì đã có (song song, cần key và CLI của bạn)

| # | Việc | Đạt khi |
|---|---|---|
| 0.1 | [AWF] Viết ma trận kiểm thử thật và lệnh `awf selftest`: chạy một run nhỏ cho mỗi nhà cung cấp/engine mà worker có, rồi in bảng kết quả | lệnh in ra bảng nhà cung cấp × engine với COMPLETED/FAILED kèm lý do |
| 0.2 | [bạn] Chạy ma trận với những key/CLI bạn có (ưu tiên Claude, Antigravity với gói Gemini Pro, Claude Code) | mỗi ô COMPLETED, hoặc có lỗi đã ghi lại |
| 0.3 | [AWF] Sửa các lỗi lộ ra (dễ gặp nhất: định dạng output của `agy`, `dsh`, `mcode`) | các ô fail được chạy lại và COMPLETED |
| 0.4 | [bạn] Tắt worker giữa một run để thử phục hồi | run tự chạy tiếp, không mất kết quả |

### Giai đoạn 1 — Nền SDD

| # | Việc | Phụ thuộc | Đạt khi |
|---|---|---|---|
| 1.1 | [AWF] Dựng SDD v1.1 cho chính repo AWF: `AGENTS.md`, `docs/sdd/project-profile.md`, `testing.md`, danh sách area | — | repo có đủ file nền; quy tắc SDD được ghi vào `CLAUDE.md` |
| 1.2 | [AWF] **Change 0001 — checker `awf sdd check`** (bản đầu) | 1.1 | checker kiểm tra được ngữ pháp, ID, liên kết spec → design → tasks → test, bằng chứng; sinh ra `verification.md` |
| 1.3 | [AWF] Change 0002 — `awf sdd init` / `awf sdd new-change`: dựng cấu trúc và mở change folder mới | 1.2 | lệnh tạo được nền SDD cho một repo trống và một change folder theo template |
| 1.4 | [bạn] Duyệt SDD v1.1 sau khi dùng thật cho 0001 và 0002; sửa những chỗ vướng | 1.2, 1.3 | v1.1 chuyển từ "Draft" sang "Approved" |

### Giai đoạn 2 — AWF hoà vào quy trình của repo (trước khi mở rộng workflow)

| # | Việc | Phụ thuộc | Đạt khi |
|---|---|---|---|
| 2.1 | [AWF] Change 0003 — **chế độ quy trình theo project**: `awf-default`, `repo-sdd` (đọc profile và gate của repo), `repo-custom` (khai báo ánh xạ: spec ở đâu, có cần design không, CI nào, gate nào do PR review lo). Stage nào repo đã lo thì AWF bỏ qua | 1.2 | cùng một objective chạy được trên ba repo theo ba chế độ, không có stage trùng lặp |
| 2.2 | [AWF] Change 0004 — **spec sống trong repo**: AWF ghi change folder vào nhánh integration của repo đích; Analyst và Architect đọc spec hiện hành của repo; mọi role đọc `AGENTS.md`/quy ước của repo | 2.1 | sau một run, PR có `docs/sdd/changes/<NNNN>-…/` và checker của repo cho kết quả sạch |
| 2.3 | [AWF] Change 0005 — **bằng chứng từ CI và PR thật**: mở PR qua API GitHub/GitLab; lấy kết quả CI và JUnit của PR làm bằng chứng chính, build/test trên worker chỉ là kiểm tra nhanh; gate merge của AWF trở thành "chờ PR review của repo" | 2.2 | một run kết thúc bằng PR thật, có kết quả CI và verification sinh từ CI |

### Giai đoạn 3 — Workflow theo SDD

| # | Việc | Phụ thuộc | Đạt khi |
|---|---|---|---|
| 3.1 | [AWF] Change 0006 — spec có ID (`FR-<AREA>-NNN`, AC `…n`), mục câu hỏi mở và **gate làm rõ**; duyệt **gộp G1–G3** trước khi code; Developer được nhận spec | 2.2 | spec thiếu ID bị trả lại; câu hỏi mở làm run dừng chờ bạn trả lời |
| 3.2 | [AWF] Change 0007 — truy vết: `covers` cho task, bảng ánh xạ trong design, test gắn ID của AC, verification và bảng truy vết do platform sinh qua checker; QA dùng bảng đó | 3.1, 1.2 | bỏ test của một AC thì run không qua QA, kèm thông báo chỉ đúng AC đó |
| 3.3 | [AWF] Change 0008 — vòng `SPEC_CHANGE` và promotion vào spec hiện hành ở G6 | 3.2 | review trả `SPEC_CHANGE` thì run quay lại spec và chờ duyệt lại |

### Giai đoạn 4 — Tính năng (mỗi tính năng là một change theo SDD)

| # | Việc | Ghi chú |
|---|---|---|
| 4.1 | Bot Telegram nhận objective: `/run`, `/status`, `/cancel` | khoảng trống lớn nhất so với OpenClaw |
| 4.2 | Độ tin cậy: lỗi lặp lại → `BLOCKED`; ngân sách theo run; circuit breaker theo nhà cung cấp | từ ba bài review và agent-factory |
| 4.3 | Chính sách duyệt thường trực (kiểu standing orders) | gate nào tự duyệt, theo mức rủi ro và lịch sử |
| 4.4 | Lịch chạy và webhook (GitHub issue → run) | |
| 4.5 | Sandbox container cho các engine "lỏng" | ý tưởng từ OpenClaw |
| 4.6 | Bộ nhớ theo project | sổ quyết định đưa vào context |
| 4.7 | Workflow ảnh và video | cần thiết kế riêng |
| 4.8 | (Tuỳ chọn) Cổng OpenClaw gọi API của AWF | chỉ khi cần Zalo/WhatsApp hai chiều hoặc chat tự nhiên |

### Giai đoạn 5 — Tách bộ tiêu chuẩn

| # | Việc | Đạt khi |
|---|---|---|
| 5.1 | [bạn + AWF] Chạy thử SDD trên một repo thật (ví dụ InternalFileServer) | có ít nhất một change hoàn chỉnh trên repo đó |
| 5.2 | [bạn] Quyết định: công khai hay riêng tư, giấy phép, nơi chứa repo | — |
| 5.3 | [AWF] Phát hành v1.1.0 và tách `standards/sdd/` thành repo `sdd-standard` kèm lịch sử: tiêu chuẩn, template, checker (gói Python), reusable CI workflow; AWF dùng checker từ gói đã ghim phiên bản | repo đích chỉ cần ghim `sdd: 1.1` và workflow CI `@v1.1.0` |

## Thứ tự thực hiện

```text
Giai đoạn 0 (song song, cần bạn) ─────────────────────────────────────────────►
1.1 → 1.2 (checker) → 1.3 → 2.1 → 2.2 → 2.3 → 3.1 → 3.2 → 3.3 → giai đoạn 4 …
                         └─ 1.4 duyệt v1.1                     └─ 5.1 → 5.2 → 5.3
```

## Trạng thái

**29/10/2026: tạm dừng SDD.** Bạn quyết định chạy thật cho trơn tru những gì đã có (giai đoạn 0) trước khi làm
tiếp. Tài liệu SDD giữ nguyên ở dạng nháp và không áp dụng; giai đoạn 1–3 bàn lại sau. Đã có một run hoàn chỉnh
bằng Claude Code trên gói Claude Pro (engine cho mọi role, ADR-014).

| Mục | Trạng thái |
|---|---|
| SDD v1.1 (`standards/sdd/`) | Draft, chờ duyệt; đã bổ sung 2 quy tắc ngữ pháp phát hiện khi viết change 0001 |
| 1.1 Nền SDD cho AWF | đã dựng (`AGENTS.md`, `docs/sdd/project-profile.md`, `testing.md`, `specs/sdd.md`), chờ duyệt cùng change 0001 |
| 1.2 Change 0001 — checker | spec, design, tasks chờ duyệt gộp G1–G3; còn câu hỏi mở `Q-0001-01`; chưa code |
| Giai đoạn 0 — chạy thật | đang làm: run đầu tiên COMPLETED (Claude Code, gói Pro); đang sửa các điểm vướng khi vận hành |
