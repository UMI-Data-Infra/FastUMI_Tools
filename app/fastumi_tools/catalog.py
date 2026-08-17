"""Versioned SDK and firmware catalog handling."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


class CatalogError(RuntimeError):
    pass


SUPPORTED_OS_CODENAMES = ("focal",)


def read_os_release(path: Path = Path("/etc/os-release")) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return values
    for line in lines:
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Catalog:
    def __init__(self, root: Optional[Path] = None, os_release: Optional[Dict[str, str]] = None):
        self.root = (root or self.find_root()).resolve()
        self.manifest_path = self.root / "manifest.json"
        self.os_release = os_release if os_release is not None else read_os_release()
        self.data = self._load()

    @staticmethod
    def find_root() -> Path:
        configured = os.environ.get("FASTUMI_PAYLOAD_ROOT")
        candidates: Iterable[Path] = (
            Path(configured).expanduser() if configured else Path("/__not_configured__"),
            Path("/var/lib/fastumi-tools/payloads"),
            Path("/usr/share/fastumi-tools/payloads"),
            Path(__file__).resolve().parents[2] / "payloads",
        )
        for candidate in candidates:
            if (candidate / "manifest.json").is_file():
                return candidate
        return Path(__file__).resolve().parents[2] / "payloads"

    def _load(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise CatalogError("找不到资源清单：%s" % self.manifest_path) from exc
        except json.JSONDecodeError as exc:
            raise CatalogError("资源清单格式错误：%s" % exc) from exc
        if data.get("schema_version") != 1:
            raise CatalogError("不支持的资源清单版本")
        for kind in ("sdk", "firmware"):
            if not isinstance(data.get(kind), list):
                raise CatalogError("资源清单缺少 %s 列表" % kind)
            ids = [str(item.get("id", "")) for item in data[kind]]
            if not all(ids) or len(ids) != len(set(ids)):
                raise CatalogError("%s 资源 ID 缺失或重复" % kind)
        return data

    @property
    def codename(self) -> str:
        return self.os_release.get("VERSION_CODENAME", "").lower()

    @property
    def host_supported(self) -> bool:
        return self.codename in SUPPORTED_OS_CODENAMES

    def _safe_path(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise CatalogError("资源路径越界：%s" % relative) from exc
        return candidate

    def items(self, kind: str) -> List[Dict[str, Any]]:
        if kind not in ("sdk", "firmware"):
            raise CatalogError("未知资源类型：%s" % kind)
        result: List[Dict[str, Any]] = []
        for raw in self.data[kind]:
            item = dict(raw)
            path = self._safe_path(str(item.get("path", "")))
            item["available"] = path.is_file()
            supported = item.get("os_codenames") or []
            item["compatible"] = self.host_supported and (not supported or self.codename in supported)
            result.append(item)
        return result

    def resolve(self, kind: str, artifact_id: str, verify: bool = True) -> Tuple[Dict[str, Any], Path]:
        for item in self.items(kind):
            if item.get("id") != artifact_id:
                continue
            path = self._safe_path(str(item["path"]))
            if not path.is_file():
                raise CatalogError("资源文件不存在：%s" % path.name)
            expected = str(item.get("sha256", "")).lower()
            if verify and (not expected or sha256_file(path) != expected):
                raise CatalogError("资源校验失败：%s" % path.name)
            if not item.get("compatible"):
                raise CatalogError("该资源不支持当前系统 %s" % (self.codename or "unknown"))
            return item, path
        raise CatalogError("找不到资源：%s" % artifact_id)

    def public(self) -> Dict[str, Any]:
        return {
            "project_version": self.data.get("project_version"),
            "catalog_version": self.data.get("catalog_version"),
            "os_codename": self.codename or None,
            "os_name": self.os_release.get("PRETTY_NAME"),
            "host_supported": self.host_supported,
            "supported_os_codenames": list(SUPPORTED_OS_CODENAMES),
            "sdk": self.items("sdk"),
            "firmware": self.items("firmware"),
        }
