# Implementation overview (Phase 0 → đầu Phase 1)

Tài liệu này ánh xạ bản thiết kế (`AI_WORK_FACTORY_INITIAL_TECHNICAL_DESIGN.md`) sang code thực tế.

## 1. Thành phần và vị trí code

| Thành phần (Design §3) | Code | Ghi chú |
|---|---|---|
| control-api | `apps/control_api/awf_api/` | FastAPI, `/api/v1`, dashboard tĩnh tại `/` |
| orchestrator | `packages/awf/orchestrator/orchestrator.py` | `tick()` 8 bước, chạy riêng (`awf orchestrator`) hoặc nhúng trong API |
| workflow-engine | `packages/awf/workflow_engine/` | schema Pydantic strict + `StageGraph` (topo, cycle, ancestors, loop span) |
| agent-runtime | `packages/awf/agent_runtime/` | contract/profile loader, vòng lặp tool-use có giới hạn, validate output |
| model-router | `packages/awf/model_router/router.py` + `config/model-routing.yaml` | class → provider/model, confidentiality, budget, giá |
| tool-service | `packages/awf/tool_sdk/` | `fs.*`, `git.*`, `build/test/lint.run`, `shell.exec` (deny) |
| policy engine | `packages/awf/policy/engine.py` + `config/policies.yaml` | RBAC + level 0..3 + command allowlist |
| worker-service | `packages/awf/worker_sdk/` + `workers/linux_worker/` | register/heartbeat/claim/execute/submit/cleanup |
| artifact-service | `packages/awf/orchestrator/artifacts.py` | local FS hoặc S3/MinIO, version, sha256, relations, impact |
| approval-service | `Orchestrator.resolve_approval` + `/api/v1/approvals` | gate stage + contract `human_approval` |
| critic | `packages/awf/orchestrator/critic.py` | rule + severity, evidence-based, không chấm điểm |
| task router | `packages/awf/orchestrator/task_router.py` | role, model route, worker requirements, sandbox write/read |
| run placement | `packages/awf/orchestrator/placement.py` | một run — một máy: yêu cầu của run, chọn máy theo tải, chuyển máy khi máy hỏng (ADR-008) |
| audit / logging | `packages/awf/orchestrator/audit.py`, `packages/awf/logging.py` | bảng append-only + JSON log có redaction |

## 2. Vòng đời một workflow run

```text
POST /projects/{id}/workflow-runs
  -> WorkflowRun RUNNING + artifact objective.md
orchestrator.tick() (lặp):
  1 jobs xong      -> AgentRun metrics, artifacts + relations, Critic -> DONE / APPROVAL_REQUIRED /
                      CHANGES_REQUIRED (retry task) / FAILED (BLOCK)
  2 approvals      -> DONE, hoặc chạy lại task với comment, hoặc FAILED (gate)
  3 stage verdict  -> reviewer/QA/tool trả CHANGES_REQUIRED => fix loop: tăng iteration cho
                      [goto .. stage hiện tại], gắn feedback vào stage goto
  4 failure        -> task FAILED ở iteration hiện tại => run FAILED, huỷ phần còn lại
  5 materialize    -> tạo task cho stage có depends_on đã done (foreach planned_task dùng task graph
                      của Planner; iteration > 1 tạo 1 task "Address review findings")
  6 promote        -> NEW -> READY khi task dependency DONE
  7 dispatch       -> gate: Approval; agent/tool: route + context + Job QUEUED (task WAITING)
  8 completion     -> mọi stage done => COMPLETED
Worker: claim (WAITING->RUNNING) -> clone sandbox -> agent loop/tool -> commit+merge vào awf/run-<id>
        -> POST /jobs/{id}/complete|fail
```

## 3. State machine

Task: `NEW → READY → WAITING → RUNNING → REVIEW → DONE`, với các nhánh
`REVIEW → APPROVAL_REQUIRED → DONE`, `REVIEW/APPROVAL_REQUIRED → CHANGES_REQUIRED → READY`,
`* → FAILED`, `FAILED → READY` (retry thủ công), `* → CANCELLED`. Mọi chuyển trạng thái đi qua
`awf/domain/state_machine.py` — chuyển sai sẽ raise.

Job: `QUEUED → CLAIMED → PREPARING → RUNNING → UPLOADING_RESULT → SUCCESS`,
lỗi: `FAILED → RETRYABLE → QUEUED` hoặc `TERMINAL`.

## 4. Evidence thay vì lời khai của agent

`build.run` / `test.run` / `lint.run` do worker thực thi; kết quả (exit code, số test pass/fail — hỗ trợ
pytest và `dotnet test`) được ghi vào `evidence`. Critic kiểm tra `completion_rules`
(`build_pass`, `unit_test_pass`, `lint_pass`, `has_changes`, `tests_executed`) trên evidence này. Một
verdict `APPROVE` mâu thuẫn với test fail bị đổi thành `CHANGES_REQUIRED`.

## 5. Bảo mật đã hiện thực (Design §22)

- Token admin (`AWF_API_TOKEN`), token đăng ký worker + token riêng từng worker (lưu sha256).
- Path traversal + chặn `.git` trong tool file; argv-only, allowlist, timeout, giới hạn output.
- Redaction secret trong: log, audit payload, kết quả job, artifact, **output tool gửi cho model**.
- Critic `BLOCK` khi output/diff chứa dữ liệu dạng credential hoặc agent liên tục vượt quyền.
- Env của subprocess loại bỏ biến chứa KEY/TOKEN/SECRET/PASSWORD.
- Không role nào có quyền production/merge protected/shell (có unit test khẳng định).
- `audit_events` append-only (trigger PostgreSQL).

## 6. Giới hạn hiện tại (có chủ đích)

- Queue dùng bảng `jobs` + polling (Redis có trong compose, chưa dùng — Design §13 cho phép).
- Critic là rule-based; chưa có LLM critic.
- Chưa mở PR trên GitHub/GitLab: delivery tạo `patch` + `pull_request.md` và đẩy nhánh integration.
- Sandbox là clone riêng trên host; container per job để Phase 2.
- Chưa có provider LOCAL_PRIVATE: project `restricted` sẽ fail rõ ràng thay vì gửi code ra ngoài.
- Dashboard là trang tĩnh (vanilla JS), chưa phải Next.js.
