"""Artifact store + metadata service: versioning, checksum, provenance and traceability relations."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from awf.domain.enums import EventType
from awf.domain.models import Artifact, ArtifactRelation
from awf.orchestrator import audit


class ArtifactStore(ABC):
    backend: str

    @abstractmethod
    def put(self, key: str, data: bytes, media_type: str) -> str: ...

    @abstractmethod
    def get(self, uri: str) -> bytes: ...


class LocalArtifactStore(ArtifactStore):
    backend = "local"

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes, media_type: str) -> str:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("artifact key escapes the store root")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path.as_uri()

    def get(self, uri: str) -> bytes:
        parsed = urlparse(uri)
        raw = unquote(parsed.path)
        if len(raw) > 2 and raw[0] == "/" and raw[2] == ":":  # file:///C:/...
            raw = raw[1:]
        path = Path(raw).resolve()
        if self.root not in path.parents:
            raise ValueError("artifact uri outside the store root")
        return path.read_bytes()


class S3ArtifactStore(ArtifactStore):
    """MinIO / S3-compatible store (requires the `s3` extra: boto3)."""

    backend = "s3"

    def __init__(self, bucket: str, endpoint_url: str | None = None):
        import boto3  # optional dependency

        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint_url)

    def put(self, key: str, data: bytes, media_type: str) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=media_type)
        return f"s3://{self.bucket}/{key}"

    def get(self, uri: str) -> bytes:
        parsed = urlparse(uri)
        return self.client.get_object(Bucket=parsed.netloc, Key=parsed.path.lstrip("/"))["Body"].read()


def build_store(settings) -> ArtifactStore:
    if settings.object_store == "s3":
        import os

        return S3ArtifactStore(os.environ.get("AWF_S3_BUCKET", "awf-artifacts"), os.environ.get("AWF_S3_ENDPOINT"))
    return LocalArtifactStore(settings.artifact_root)


class ArtifactService:
    def __init__(self, store: ArtifactStore):
        self.store = store

    def create(self, session: Session, *, project_id: str, name: str, type: str, content: bytes | str,
               media_type: str = "text/markdown", workflow_run_id: str | None = None, task_id: str | None = None,
               agent_run_id: str | None = None, stage_id: str | None = None,
               input_artifact_ids: list[str] | None = None, relation_type: str = "derived_from",
               metadata: dict[str, Any] | None = None) -> Artifact:
        data = content.encode("utf-8") if isinstance(content, str) else content
        checksum = "sha256:" + hashlib.sha256(data).hexdigest()
        latest = session.scalar(select(func.max(Artifact.version)).where(
            Artifact.project_id == project_id, Artifact.name == name))
        version = (latest or 0) + 1
        uri = self.store.put(f"{project_id}/{name}/v{version}", data, media_type)
        meta = dict(metadata or {})
        meta["input_artifact_ids"] = list(input_artifact_ids or [])
        artifact = Artifact(project_id=project_id, workflow_run_id=workflow_run_id, task_id=task_id,
                            agent_run_id=agent_run_id, stage_id=stage_id, type=type, name=name,
                            media_type=media_type, storage_backend=self.store.backend, uri=uri, version=version,
                            checksum=checksum, size_bytes=len(data), metadata_json=meta)
        session.add(artifact)
        session.flush()
        for target in input_artifact_ids or []:
            session.add(ArtifactRelation(from_artifact_id=artifact.id, to_artifact_id=target,
                                         relation_type=relation_type))
        audit.record(session, EventType.ARTIFACT_CREATED, project_id=project_id, workflow_run_id=workflow_run_id,
                     task_id=task_id, payload={"artifact_id": artifact.id, "name": name, "type": type,
                                               "version": version, "checksum": checksum})
        return artifact

    def read_text(self, artifact: Artifact) -> str:
        return self.store.get(artifact.uri).decode("utf-8", errors="replace")

    def read_bytes(self, artifact: Artifact) -> bytes:
        return self.store.get(artifact.uri)

    @staticmethod
    def relations(session: Session, artifact_id: str) -> dict[str, list[ArtifactRelation]]:
        outgoing = session.scalars(select(ArtifactRelation).where(ArtifactRelation.from_artifact_id == artifact_id)).all()
        incoming = session.scalars(select(ArtifactRelation).where(ArtifactRelation.to_artifact_id == artifact_id)).all()
        return {"outgoing": list(outgoing), "incoming": list(incoming)}

    @staticmethod
    def impact(session: Session, artifact_id: str, max_depth: int = 20) -> list[tuple[str, int, str]]:
        """Everything derived (transitively) from `artifact_id`: [(artifact_id, depth, relation)]."""
        seen: dict[str, tuple[int, str]] = {}
        frontier = [artifact_id]
        for depth in range(1, max_depth + 1):
            if not frontier:
                break
            rows = session.execute(select(ArtifactRelation.from_artifact_id, ArtifactRelation.relation_type)
                                   .where(ArtifactRelation.to_artifact_id.in_(frontier))).all()
            frontier = []
            for source, rel in rows:
                if source not in seen and source != artifact_id:
                    seen[source] = (depth, rel)
                    frontier.append(source)
        return [(aid, d, rel) for aid, (d, rel) in seen.items()]
