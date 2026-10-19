"""E2E: content production without a repository - research -> plan -> draft -> review (fix loop) -> edit -> SEO
-> publish approval - on one machine that has no git at all."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

from awf.domain.models import Job
from awf.worker_sdk import ControlPlaneClient
from awf.worker_sdk import Worker as WorkerDaemon
from tests.e2e.test_software_workflow import drive

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]
BRIEF = ("Viết bài blog 800 chữ giới thiệu AI Work Factory cho trưởng nhóm kỹ thuật ở doanh nghiệp vừa; "
         "giọng văn thân thiện, tiếng Việt.")


def _md(title: str, *sections: str) -> str:
    return f"# {title}\n\n" + "\n\n".join(f"## {s}\nNội dung {s.lower()}." for s in sections)


SCRIPT = {"roles": {
    "content.researcher": [{"turns": [{"submit": {"summary": "Brief ready", "outputs": {
        "research_brief": _md("Research brief", "Audience", "Key facts", "Sources", "Open questions")}}}]}],
    "content.strategist": [{"turns": [{"submit": {"summary": "Plan ready", "outputs": {
        "content_plan": _md("Plan", "Angle", "Key messages", "Outline", "Call to action", "Format")}}}]}],
    "content.copywriter": [
        {"when": {"iteration": 1}, "turns": [{"submit": {"summary": "First draft", "outputs": {
            "draft": "# AI Work Factory\n\nNền tảng tốt nhất thế giới, tăng năng suất 300%."}}}]},
        {"when": {"iteration": 2}, "turns": [{"submit": {"summary": "Fixed the unsupported claims", "outputs": {
            "draft": "# AI Work Factory\n\nNền tảng giúp nhóm kỹ thuật giao việc cho agent có kiểm soát."}}}]},
    ],
    "content.reviewer": [
        {"when": {"iteration": 1}, "turns": [{"submit": {
            "summary": "Unsupported claims", "decision": "CHANGES_REQUIRED",
            "findings": [{"severity": "MAJOR", "message": "'tốt nhất thế giới' and '300%' have no source"}],
            "outputs": {"review_report": _md("Review", "Summary", "Findings", "Verdict")}}}]},
        {"when": {"iteration": 2}, "turns": [{"submit": {
            "summary": "Good to go", "decision": "APPROVE",
            "outputs": {"review_report": _md("Re-review", "Summary", "Findings", "Verdict")}}}]},
    ],
    "content.editor": [{"turns": [{"submit": {"summary": "Polished", "outputs": {
        "final_article": "# AI Work Factory\n\nNền tảng giúp nhóm kỹ thuật giao việc cho agent có kiểm soát.",
        "editor_notes": _md("Notes", "Changes", "Before publishing")}}}]}],
    "content.seo": [{"turns": [
        {"submit": {"summary": "SEO v1", "outputs": {"seo_package": {   # slug with diacritics: rejected
            "title": "AI Work Factory cho nhóm kỹ thuật", "slug": "giới-thiệu",
            "meta_description": "AI Work Factory giúp nhóm kỹ thuật giao việc cho agent AI có kiểm soát và minh bạch.",
            "primary_keyword": "AI Work Factory", "secondary_keywords": ["agent AI"],
            "social_post": "Bài mới: AI Work Factory là gì và giúp nhóm kỹ thuật thế nào?"}}}},
        {"submit": {"summary": "SEO fixed", "outputs": {"seo_package": {
            "title": "AI Work Factory cho nhóm kỹ thuật", "slug": "gioi-thieu-ai-work-factory",
            "meta_description": "AI Work Factory giúp nhóm kỹ thuật giao việc cho agent AI có kiểm soát và minh bạch.",
            "primary_keyword": "AI Work Factory", "secondary_keywords": ["agent AI"],
            "social_post": "Bài mới: AI Work Factory là gì và giúp nhóm kỹ thuật thế nào?"}}}},
    ]}],
}}


@pytest.fixture
def writer_box(client, tmp_path):
    """A machine with no git and no repositories: content work must not need either."""
    w = WorkerDaemon(ControlPlaneClient(http=client), name="writer-box", workspace_root=tmp_path / "ws",
                     policy_path=ROOT / "config" / "policies.yaml", capabilities=["python"],
                     platform_name="windows-amd64")
    w.register("")
    return w


def _content_project(client, **extra):
    resp = client.post("/api/v1/projects", json={"name": "Blog", "labels": {"platform": "any"}, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_content_run_without_repository(client, platform, orchestrator, writer_box, tmp_path, monkeypatch):
    path = tmp_path / "content-script.yaml"
    path.write_text(yaml.safe_dump(SCRIPT, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("AWF_SCRIPTED_RESPONSES", str(path))
    project = _content_project(client)
    run = client.post(f"/api/v1/projects/{project['id']}/workflow-runs",
                      json={"workflow_id": "content-production", "inputs": {"objective": BRIEF}})
    assert run.status_code == 201, run.text
    assert run.json()["requirements"] == {"platform": "any", "capabilities": []}

    final = drive(client, orchestrator, writer_box, run.json()["id"])
    assert final["status"] == "COMPLETED", final.get("error")
    assert final["approved_actions"] == ["approve_publish"]
    approval = client.get("/api/v1/approvals", params={"state": "approved"}).json()[0]
    shown = {x["type"] for x in client.get(f"/api/v1/approvals/{approval['id']}").json()["artifacts"]}
    assert shown == {"content_final", "editor_notes", "seo_package"}  # what the approver needs to read
    assert final["fix_loops"] == {"review": 1}
    assert [s["id"] for s in final["stages"]] == ["research", "plan", "draft", "review", "edit", "seo", "publish"]

    artifacts = client.get(f"/api/v1/projects/{project['id']}/artifacts", params={"run_id": run.json()["id"]}).json()
    by_type: dict[str, list] = {}
    for a in artifacts:
        by_type.setdefault(a["type"], []).append(a)
    assert {"research_brief", "content_plan", "content_draft", "content_review", "content_final", "editor_notes",
            "seo_package"} <= set(by_type)
    assert len(by_type["content_draft"]) == 2 and len(by_type["content_review"]) == 2  # the fix loop
    final_article = client.get(f"/api/v1/artifacts/{by_type['content_final'][0]['id']}/content").text
    assert "có kiểm soát" in final_article and "300%" not in final_article
    seo = client.get(f"/api/v1/artifacts/{by_type['seo_package'][0]['id']}/content").json()
    assert seo["slug"] == "gioi-thieu-ai-work-factory"

    seo_task = next(t for t in client.get(f"/api/v1/workflow-runs/{run.json()['id']}/tasks").json()
                    if t["stage_id"] == "seo")
    calls = client.get(f"/api/v1/tasks/{seo_task['id']}").json()["agent_runs"][0]["tool_calls"]
    assert any(c.get("status") == "rejected" for c in calls)  # the diacritics slug was refused, then fixed

    with platform.db.session() as s:
        job = s.scalars(select(Job).where(Job.workflow_run_id == run.json()["id"])).first()
    logs = client.get(f"/api/v1/jobs/{job.id}/logs").json()
    assert any("empty workspace" in line["line"] for line in logs)


def test_automatic_content_run(client, orchestrator, writer_box):
    """No script for the content roles: the contract-derived automatic outputs carry a run to the end."""
    project = _content_project(client)
    run_id = client.post(f"/api/v1/projects/{project['id']}/workflow-runs",
                         json={"workflow_id": "content-production", "inputs": {"objective": BRIEF}}).json()["id"]
    assert drive(client, orchestrator, writer_box, run_id)["status"] == "COMPLETED"


def test_content_with_reference_material_in_a_repository(client, orchestrator, worker, repository_config,
                                                          tmp_path, monkeypatch):
    """With a repository the agents can read reference files, and the run then needs git on its machine."""
    path = tmp_path / "s.yaml"
    path.write_text(yaml.safe_dump({"roles": {"content.researcher": [{"turns": [
        {"tool_calls": [{"name": "fs.read", "input": {"path": "README.md"}}]},
        {"submit": {"summary": "Used the README", "outputs": {
            "research_brief": _md("Brief", "Audience", "Key facts", "Sources", "Open questions")}}}]}]}}),
        encoding="utf-8")
    monkeypatch.setenv("AWF_SCRIPTED_RESPONSES", str(path))
    project = _content_project(client, name="Docs", repository=repository_config)
    run = client.post(f"/api/v1/projects/{project['id']}/workflow-runs",
                      json={"workflow_id": "content-production", "inputs": {"objective": BRIEF}}).json()
    assert run["requirements"]["capabilities"] == ["git"]
    final = drive(client, orchestrator, worker, run["id"])
    assert final["status"] == "COMPLETED"
    research = next(t for t in client.get(f"/api/v1/workflow-runs/{run['id']}/tasks").json()
                    if t["stage_id"] == "research")
    calls = client.get(f"/api/v1/tasks/{research['id']}").json()["agent_runs"][0]["tool_calls"]
    assert any(c["tool"] == "fs.read" and c["status"] == "ok" for c in calls)
