# Thiết kế: repo chung trên máy trung tâm, làm việc như một team dev

Trạng thái: **đề xuất, chờ bạn duyệt**. Viết ngày 29/10/2026 theo các lựa chọn của bạn: repo chung đặt trên máy
control-plane, gộp nhánh bằng rebase, agent giải conflict và bạn duyệt, GitHub chỉ là mirror tuỳ chọn.

## Vấn đề

Làm việc trực tiếp với GitHub chưa tối ưu, vì ba lý do:

- không tận dụng được ảnh chụp màn hình và các file kết quả khác do test sinh ra;
- không tham khảo được nhiều repo hay branch đang nằm ở máy local;
- không có luồng fetch, pull, rebase và giải conflict linh hoạt như một team dev làm việc với nhau.

## Tổng thể

```text
 PC của bạn (repo local)          Máy trung tâm (control-plane)                Worker A, B, C...
 ───────────────────────          ─────────────────────────────                ──────────────────
 git remote add factory  ───────► Git HTTPS: /git/<project>.git  ◄───────────  clone / fetch / push
 git push factory main            (bare repo, token, chỉ trong tailnet)          nhánh awf/*
 git fetch factory                Orchestrator: rebase, conflict, gate merge
 git switch awf/run-xxxx          Tuỳ chọn: mirror main + nhánh đã duyệt ──► GitHub
 git pull factory main
```

## Các phần

### 1. Git server trên control-plane

- Mỗi project có một bare repo: `var/git/<project-slug>.git`. API phục vụ Git Smart HTTP tại
  `https://<máy trung tâm>/git/<slug>.git`, dùng chính `git http-backend` cài sẵn cùng git (không phải viết lại
  giao thức).
- Xác thực:
  - worker dùng token của chính nó (đã có);
  - bạn dùng một **token git cá nhân** tạo trên dashboard, lưu bằng credential manager của git, nên không phải gõ
    lại mỗi lần.
- Tạo project chọn một trong ba nguồn:
  - "repo mới trên máy trung tâm";
  - "đẩy lên từ repo local" (dashboard in sẵn hai lệnh `git remote add factory …` và `git push factory main`);
  - "nhập từ URL" (clone một lần từ GitHub hoặc thư mục).
- Quyền ghi: bạn ghi được mọi nhánh. Worker chỉ ghi được nhánh `awf/*`. Nhánh `main` chỉ đổi khi bạn push, hoặc
  khi bạn duyệt **approve_merge**.

### 2. Nhánh và rebase, giống một team

- Mỗi run có nhánh tích hợp `awf/run-<id>`. Mỗi task viết code có nhánh riêng `awf/tasks/<run>/<task>`.
- Một task xong thì worker `fetch` rồi **rebase** nhánh task lên nhánh tích hợp mới nhất, build và test lại, rồi
  fast-forward nhánh tích hợp. Push bị từ chối (task khác vừa push trước) thì fetch, rebase lại và thử lại (hiện
  đã có vòng thử lại cho merge).
- Trước khi giao (stage delivery), và mỗi khi bạn push commit mới lên `main`: nhánh tích hợp **rebase lên `main`
  mới nhất**, build và test lại. Bạn thấy trên trang run: "đã cập nhật theo main@abc123".
- Duyệt **approve_merge** thì main được fast-forward tới nhánh tích hợp. Sau đó bạn `git pull factory main` là có
  code.

### 3. Conflict: agent giải, bạn duyệt

- Rebase gặp conflict thì platform không bỏ cuộc mà tạo task **Resolve conflict** cho Developer: workspace để
  nguyên các file có dấu conflict, kèm diff của hai phía và mục tiêu của từng task.
- Agent sửa xong thì **platform tự build và test**, và Critic chấm như mọi task.
- Có ba trường hợp dừng chờ bạn:
  - agent báo không chắc;
  - build hoặc test fail sau 2 lần;
  - conflict chạm file cấu hình nhạy cảm (danh sách khai báo được).

  Khi dừng, trang run ghi tên nhánh, các file conflict và lệnh để bạn tự giải ở máy local
  (`git fetch factory && git switch … && git rebase …`). Bạn push xong thì run chạy tiếp.
- Bạn luôn duyệt ở gate merge như hiện nay. Trang run ghi rõ commit nào là commit giải conflict.

### 4. Tham khảo nhiều repo và branch

- Project có thêm mục **References**, là danh sách repo hoặc nhánh **chỉ đọc**: repo khác trên máy trung tâm, một
  nhánh khác của chính repo, hoặc một URL.
- Worker clone các nguồn này vào `workspace/refs/<tên>` (fetch nông, chỉ đọc). Agent đọc được bằng `fs.read` và
  `fs.search`; engine (Claude Code...) đọc được như file thường. Platform không commit gì vào đó.
- Objective có thể nhắc tới chúng, ví dụ: "làm giống `refs/ui-kit` nhánh `feature/grid`".

### 5. Ảnh chụp màn hình và file kết quả của test

- Cấu hình project thêm `test_artifacts`, là danh sách mẫu file như `TestResults/**/*.png`, `playwright-report/**`
  hay `**/*.trx`.
- Sau `test.run`, worker gom các file khớp mẫu (có giới hạn dung lượng) và đẩy lên làm artifact của task, loại
  `test_screenshot` hoặc `test_report`.
- Dashboard hiện ảnh ngay trong task và trong artifact. Reviewer và QA nhận ảnh làm input (model đọc được ảnh),
  để so sánh với yêu cầu UI.

### 6. Mirror lên GitHub (tuỳ chọn)

- Project có thể bật `mirror: <URL GitHub>`. Khi đó control-plane đẩy `main` sau mỗi lần duyệt merge (và tuỳ chọn
  cả các nhánh `awf/run-*`) lên GitHub, dùng token GitHub đặt trên máy trung tâm.
- Mở PR trên GitHub là bước sau, không nằm trong phần này.

## Thứ tự làm và cách kiểm chứng

| # | Phần | Đạt khi |
|---|---|---|
| 1 | Git server + token git + tạo project từ repo local | Từ PC: `git push factory main`, rồi một run chạy trên 2 worker ở 2 máy khác nhau; `git fetch factory` thấy nhánh `awf/run-*` |
| 2 | Rebase task → tích hợp → main, cập nhật theo main mới | Hai task song song sửa hai file khác nhau, lịch sử thẳng; bạn push lên main giữa run, nhánh tích hợp tự rebase |
| 3 | Task giải conflict + dừng chờ người | Hai task sửa cùng một hàm: agent giải, build và test pass; trường hợp cố ý không giải được thì run dừng, kèm hướng dẫn |
| 4 | References chỉ đọc | Agent đọc được file trong `refs/…` và không commit gì vào đó |
| 5 | Gom ảnh và báo cáo test | Một test UI chụp màn hình; ảnh hiện trên dashboard, reviewer nhận được ảnh |
| 6 | Mirror GitHub | Duyệt merge xong, `main` trên GitHub khớp với repo trên máy trung tâm |

Mỗi phần đều có test tự động, hướng dẫn (mục 5, 9, 19 và một mục mới "Làm việc với repo chung") và ADR. Phần 1–3
là lõi; phần 4–6 làm sau, khi phần 1–3 đã chạy trơn tru với run thật của bạn.

## Rủi ro

- Máy trung tâm thành nơi giữ code chính, nên **cần sao lưu**. `awf db backup` sẽ sao lưu luôn `var/git`.
- Repo lớn (asset nặng): clone lần đầu trên mỗi worker sẽ chậm. Có thể giữ sẵn bản clone trên worker và chỉ
  `fetch` cho các lần sau (làm ở bước 1 nếu cần).
- Rebase viết lại commit của nhánh `awf/*`. Bạn chỉ nên làm việc trên các nhánh này sau khi run đã xong, hoặc tạo
  nhánh riêng từ chúng.
