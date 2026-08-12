"""ArtifactStore: 产物持久化 + 版本管理

保存 build() 产出的 Artifact,支持版本历史和回溯。
默认 LocalFileArtifactStore,可扩展 GitArtifactStore / S3ArtifactStore。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from goal_studio.core.contract import Artifact


@dataclass
class ArtifactVersion:
    """产物版本记录"""

    version: str
    artifact_id: str
    created_at: datetime = field(default_factory=datetime.now)
    note: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ArtifactStore:
    """产物存储抽象基类"""

    def save(self, artifact: Artifact, note: str = "") -> str:
        """保存产物,返回 artifact_id"""
        raise NotImplementedError

    def load(self, artifact_id: str, version: str | None = None) -> Artifact:
        """加载产物"""
        raise NotImplementedError

    def list_versions(self, artifact_id: str) -> list[ArtifactVersion]:
        """列出产物所有版本"""
        raise NotImplementedError

    def delete(self, artifact_id: str, version: str | None = None) -> bool:
        """删除产物或指定版本"""
        raise NotImplementedError


class LocalFileArtifactStore(ArtifactStore):
    """本地文件产物存储

    目录结构:
      {root}/{artifact_id}/
        ├── manifest.json        # 版本索引
        ├── v0.1.0/
        │   ├── artifact.json    # 完整产物(含 graph_snapshot)
        │   └── files/           # 多文件产物
        └── v0.2.0/
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, artifact: Artifact, note: str = "") -> str:
        import hashlib

        # artifact_id 由内容哈希 + product_type 生成
        content_hash = hashlib.sha256(
            f"{artifact.product_type}:{artifact.content}".encode()
        ).hexdigest()[:12]
        artifact_id = f"{artifact.product_type.value}-{content_hash}"

        art_dir = self.root / artifact_id / artifact.version
        art_dir.mkdir(parents=True, exist_ok=True)

        # 保存 artifact.json
        artifact_data = {
            "product_type": artifact.product_type.value,
            "content": artifact.content,
            "files": artifact.files,
            "metadata": artifact.metadata,
            "graph_snapshot": artifact.graph_snapshot,
            "version": artifact.version,
        }
        (art_dir / "artifact.json").write_text(
            json.dumps(artifact_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 保存多文件产物
        if artifact.files:
            files_dir = art_dir / "files"
            files_dir.mkdir(exist_ok=True)
            for path, content in artifact.files.items():
                fp = files_dir / path
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(content, encoding="utf-8")

        # 更新 manifest
        self._update_manifest(artifact_id, artifact.version, note, artifact.metadata)
        return artifact_id

    def load(self, artifact_id: str, version: str | None = None) -> Artifact:
        from goal_studio.core.contract import ProductType

        manifest = self._read_manifest(artifact_id)
        if version is None:
            # 取最新版本
            version = manifest["versions"][-1]["version"]

        art_path = self.root / artifact_id / version / "artifact.json"
        if not art_path.exists():
            raise FileNotFoundError(f"Artifact {artifact_id}@{version} not found")

        data = json.loads(art_path.read_text(encoding="utf-8"))
        return Artifact(
            product_type=ProductType(data["product_type"]),
            content=data["content"],
            files=data.get("files", {}),
            metadata=data.get("metadata", {}),
            graph_snapshot=data.get("graph_snapshot", {}),
            version=data.get("version", "0.1.0"),
        )

    def list_versions(self, artifact_id: str) -> list[ArtifactVersion]:
        manifest = self._read_manifest(artifact_id)
        return [
            ArtifactVersion(
                version=v["version"],
                artifact_id=artifact_id,
                created_at=datetime.fromisoformat(v["created_at"]),
                note=v.get("note", ""),
                metadata=v.get("metadata", {}),
            )
            for v in manifest["versions"]
        ]

    def delete(self, artifact_id: str, version: str | None = None) -> bool:
        if version is None:
            import shutil

            shutil.rmtree(self.root / artifact_id, ignore_errors=True)
            return True
        v_dir = self.root / artifact_id / version
        if v_dir.exists():
            import shutil

            shutil.rmtree(v_dir)
            return True
        return False

    def _update_manifest(
        self, artifact_id: str, version: str, note: str, metadata: dict[str, Any]
    ) -> None:
        manifest_path = self.root / artifact_id / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            manifest = {"artifact_id": artifact_id, "versions": []}
        manifest["versions"].append(
            {
                "version": version,
                "created_at": datetime.now().isoformat(),
                "note": note,
                "metadata": metadata,
            }
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _read_manifest(self, artifact_id: str) -> dict[str, Any]:
        manifest_path = self.root / artifact_id / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Artifact {artifact_id} not found")
        return json.loads(manifest_path.read_text(encoding="utf-8"))


_ = Any
