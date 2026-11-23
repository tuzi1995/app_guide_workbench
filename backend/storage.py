"""图文档（事实源）与媒体库的读写。

事实源：data/flows/<project_id>.json —— 一份 JSON 描述整个画布（节点 + 连线 + 路径）。
媒体库：data/media/<sha1前16位>.png —— 内容寻址，同一张图重复上传只存一份。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config


# ---------------------------------------------------------------- 通用工具

def now_iso() -> str:
    """本地时区的 ISO 时间戳（带时区偏移），用于记录更新时间。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_write(path: Path, data: bytes) -> None:
    """先写临时文件再原子改名，避免写一半崩溃导致文件损坏。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_json(path: Path, payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    _atomic_write(path, text.encode("utf-8"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 图文档

def empty_graph(project_id: str = config.DEFAULT_PROJECT_ID) -> dict:
    return {
        "project_id": project_id,
        "title": "APP 操作指引",
        "updated_at": now_iso(),
        "nodes": [],
        "edges": [],
        "paths": [],
    }


def flow_path(project_id: str = config.DEFAULT_PROJECT_ID) -> Path:
    return config.FLOW_DIR / f"{project_id}.json"


def load_graph(project_id: str = config.DEFAULT_PROJECT_ID) -> dict:
    """读取图文档；文件不存在时返回空图并落盘，保证前端永远拿到合法结构。"""
    path = flow_path(project_id)
    if not path.exists():
        graph = empty_graph(project_id)
        write_json(path, graph)
        return graph
    try:
        graph = read_json(path)
    except (json.JSONDecodeError, OSError):
        # 文件损坏时不要让整个服务挂掉：备份坏文件后重建空图
        backup = path.with_suffix(f".broken-{datetime.now().strftime('%Y%m%d%H%M%S')}.json")
        shutil.copy2(path, backup)
        graph = empty_graph(project_id)
        graph["recovered_from"] = str(backup)
        write_json(path, graph)
        return graph
    # 补齐缺失字段，兼容手工编辑过的文件
    graph.setdefault("project_id", project_id)
    graph.setdefault("title", "APP 操作指引")
    graph.setdefault("nodes", [])
    graph.setdefault("edges", [])
    graph.setdefault("paths", [])
    for node in graph["nodes"]:
        node.setdefault("id", "")
        node.setdefault("title", "")
        node.setdefault("action", "")
        node.setdefault("description", "")
        node.setdefault("screenshot", "")
        node.setdefault("screenshot_prev", "")
        node.setdefault("screenshot_version", 1)
        node.setdefault("changed", False)
        node.setdefault("diff_ratio", 0.0)
    return graph


def save_graph(graph: dict, project_id: str = config.DEFAULT_PROJECT_ID) -> dict:
    graph["project_id"] = project_id
    graph["updated_at"] = now_iso()
    write_json(flow_path(project_id), graph)
    return graph


# ---------------------------------------------------------------- 媒体库

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def media_path(filename: str) -> Path:
    """把外部传入的文件名解析成安全的绝对路径，挡住 ../ 之类的路径穿越。"""
    if not filename or not _SAFE_NAME.match(filename):
        raise ValueError(f"非法的文件名: {filename}")
    return config.MEDIA_DIR / filename


def save_media(data: bytes, suffix: str = ".png") -> dict:
    """存一张图到媒体库，返回 {filename, url, sha1, size}。

    文件名用内容 sha1 的前 16 位，内容寻址：同样的图重复上传不会产生新文件，
    这正是「局部改版只动一个节点、其余节点文件保持不变」的基础。
    """
    sha1 = hashlib.sha1(data).hexdigest()
    filename = f"{sha1[:16]}{suffix}"
    target = config.MEDIA_DIR / filename
    if not target.exists():
        _atomic_write(target, data)
    return {
        "filename": filename,
        "url": f"/media/{filename}",
        "sha1": sha1,
        "size": len(data),
    }


def list_media() -> list[dict]:
    """列出媒体库里的所有图，按修改时间倒序。"""
    items = []
    for p in sorted(config.MEDIA_DIR.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
        if not p.is_file() or p.suffix.lower() not in config.ALLOWED_UPLOAD_EXT:
            continue
        items.append({
            "filename": p.name,
            "url": f"/media/{p.name}",
            "size": p.stat().st_size,
            "created_at": datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
        })
    return items


def delete_media(filename: str) -> bool:
    """删除媒体文件。文件不存在或文件名非法时返回 False。"""
    try:
        path = media_path(filename)
    except ValueError:
        return False
    if not path.exists():
        return False
    path.unlink()
    return True


def media_abs_url(relative_url: str) -> str:
    """把 /media/xxx.png 转成绝对 http 地址，写进知识库 image_urls 用。"""
    if not relative_url:
        return ""
    if relative_url.startswith("http://") or relative_url.startswith("https://"):
        return relative_url
    return f"{config.PUBLIC_BASE_URL.rstrip('/')}/{relative_url.lstrip('/')}"
