"""ArtifactStore: 产物持久化 + 版本管理

保存 build() 产出的 Artifact,支持版本历史和回溯。
默认 LocalFileArtifactStore,可扩展 GitArtifactStore / S3ArtifactStore。
"""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from goal_studio.core.contract import Artifact

# PR-G/G-2: artifact_id 白名单 - 格式 {product_type}-{12位hex},如 skill-2bd3993641ad
_ARTIFACT_ID_PATTERN = re.compile(r"^[a-z]+-[a-f0-9]{12}$")
# PR-G/G-2: version 白名单 - 语义化版本 x.y.z
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _validate_artifact_id(artifact_id: str) -> str:
    """校验 artifact_id 格式,防止路径遍历"""
    if not artifact_id or ".." in artifact_id or "/" in artifact_id or "\\" in artifact_id:
        raise ValueError(f"Invalid artifact_id: {artifact_id}")
    if not _ARTIFACT_ID_PATTERN.match(artifact_id):
        raise ValueError(f"Invalid artifact_id format: {artifact_id}")
    return artifact_id


def _validate_version(version: str) -> str:
    """校验 version 格式,防止路径遍历"""
    if not version or ".." in version or "/" in version or "\\" in version:
        raise ValueError(f"Invalid version: {version}")
    if not _VERSION_PATTERN.match(version):
        raise ValueError(f"Invalid version format: {version}")
    return version


@dataclass
class ArtifactVersion:
    """产物版本记录"""

    version: str
    artifact_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
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

    @staticmethod
    def _safe_join(base: Path, relative: str) -> Path:
        """安全拼接路径,防止路径遍历

        校验解析后的路径仍属于 base,否则 raise ValueError。
        """
        base_resolved = base.resolve()
        target = (base_resolved / relative).resolve()
        try:
            target.relative_to(base_resolved)
        except ValueError:
            raise ValueError(f"路径遍历: {relative} 不在 {base} 内")
        return target

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
        art_json_path = art_dir / "artifact.json"
        art_json_path.write_text(
            json.dumps(artifact_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.chmod(str(art_json_path), stat.S_IRUSR | stat.S_IWUSR)

        # 保存多文件产物
        if artifact.files:
            files_dir = art_dir / "files"
            files_dir.mkdir(exist_ok=True)
            for path, content in artifact.files.items():
                fp = self._safe_join(files_dir, path)
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(content, encoding="utf-8")
                os.chmod(str(fp), stat.S_IRUSR | stat.S_IWUSR)

        # 更新 manifest
        self._update_manifest(artifact_id, artifact.version, note, artifact.metadata)
        return artifact_id

    def load(self, artifact_id: str, version: str | None = None) -> Artifact:
        from goal_studio.core.contract import ProductType

        _validate_artifact_id(artifact_id)
        if version is not None:
            _validate_version(version)
        manifest = self._read_manifest(artifact_id)
        if version is None:
            # PR-ROUND-2/C-06: 按 created_at 排序取最新版本,
            # 不依赖 append 顺序(中间版本可能被 delete)
            sorted_versions = sorted(
                manifest["versions"],
                key=lambda v: v.get("created_at", ""),
            )
            if not sorted_versions:
                raise FileNotFoundError(f"Artifact {artifact_id} has no versions")
            version = sorted_versions[-1]["version"]

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
        _validate_artifact_id(artifact_id)
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
        _validate_artifact_id(artifact_id)
        if version is not None:
            _validate_version(version)
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
                "created_at": datetime.now(timezone.utc).isoformat(),
                "note": note,
                "metadata": metadata,
            }
        )
        # PR-ROUND-2/C-05: 原子写入(tmp + rename),防止并发 save 导致 manifest 损坏
        tmp_path = manifest_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.chmod(str(tmp_path), stat.S_IRUSR | stat.S_IWUSR)
        os.replace(str(tmp_path), str(manifest_path))

    def _read_manifest(self, artifact_id: str) -> dict[str, Any]:
        _validate_artifact_id(artifact_id)
        manifest_path = self.root / artifact_id / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Artifact {artifact_id} not found")
        manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
        return manifest


_ = Any
