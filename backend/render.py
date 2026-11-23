"""渲染与发布：把一条路径渲染成长图 + Markdown，并推送到知识库。

这是需求的核心动作链：
  替换某个节点的截图
    → 沿连线找出受影响的路径
    → 重新读取路径上所有节点已有的图片
    → 重新拼接长图 + 重新生成文字步骤文档
    → （可选）推送知识库
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import config
import graph as graph_mod
import imaging
import storage
from kb_client import HubError, get_client


class RenderError(RuntimeError):
    """渲染失败（路径非法、缺图等）。message 是给用户看的中文说明。"""


def _node_map(graph: dict) -> dict[str, dict]:
    return {n.get("id"): n for n in graph.get("nodes", [])}


def export_dir(path_id: str, version: int) -> Path:
    return config.EXPORT_DIR / path_id / str(version)


def resolve_steps(graph: dict, path: dict) -> list[dict]:
    """按路径顺序取出每一步的节点、截图绝对路径、对外 URL。

    这里是「沿连线关系提取路径上所有节点图片」的实现点：
    只读取现有图片文件，不复制、不改名 —— 所以其它节点天然保持原样。
    """
    nodes = _node_map(graph)
    steps: list[dict] = []
    for i, nid in enumerate(path.get("node_ids") or [], start=1):
        node = nodes.get(nid)
        if not node:
            raise RenderError(f"路径里的第 {i} 个节点（{nid}）在画布上找不到，可能已被删除")
        shot = node.get("screenshot") or ""
        abs_path = None
        image_url = ""
        if shot:
            try:
                abs_path = storage.media_path(shot)
            except ValueError:
                abs_path = None
            if abs_path and not abs_path.exists():
                abs_path = None
            image_url = storage.media_abs_url(f"/media/{shot}")
        steps.append({
            "index": i,
            "node_id": nid,
            "title": node.get("title", ""),
            "action": node.get("action", ""),
            "description": node.get("description", ""),
            "screenshot": shot,
            # 统一转成字符串：Path 对象不能直接塞进 JSON 响应
            "image_path": str(abs_path) if abs_path else "",
            "image_url": image_url,
            "changed": bool(node.get("changed")),
            "version": node.get("screenshot_version", 1),
            "annotations": (path.get("annotations") or {}).get(nid, []),
        })
    return steps


def render_path(graph: dict, path: dict, *, changed_step: int | None = None,
                force: bool = False) -> dict:
    """渲染一条路径：生成 long.png + steps.md。

    返回 {long_image_url, long_image_path, markdown, steps, version, dir}
    """
    problems = graph_mod.validate_path(path, graph.get("edges", []))
    if problems:
        raise RenderError("路径的连线不完整，无法生成步骤文档：" + "；".join(problems))

    steps = resolve_steps(graph, path)
    if not steps:
        raise RenderError("路径里没有节点，无法生成步骤文档")

    version = int(path.get("render_version") or 0) + 1
    out_dir = export_dir(path.get("id", "path"), version)

    # 长图
    img = imaging.build_long_image(steps)
    long_path = imaging.save_long_image(img, out_dir / "long.png")
    long_rel = f"/media/exports/{path.get('id', 'path')}/{version}/long.png"

    # Markdown
    generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    markdown = imaging.render_markdown(
        path, steps, storage.media_abs_url(long_rel),
        version=version, changed_step=changed_step, generated_at=generated_at,
    )
    (out_dir / "steps.md").write_text(markdown, encoding="utf-8")

    # 清理旧版本，只保留最近 3 版（避免磁盘无限增长）
    _cleanup_old_versions(path.get("id", "path"), keep=3, current=version,
                          protected=_published_versions(path))

    path["render_version"] = version
    path["stale"] = False
    path["long_image_url"] = long_rel
    path["long_image_path"] = str(long_path)
    path["rendered_at"] = storage.now_iso()

    return {
        "long_image_url": long_rel,
        "long_image_path": str(long_path),
        "long_image_abs_url": storage.media_abs_url(long_rel),
        "markdown": markdown,
        "steps": steps,
        "version": version,
        "changed_step": changed_step,
        "generated_at": generated_at,
        "dir": str(out_dir),
    }


def read_export(graph: dict, path: dict) -> dict:
    """只读取已生成的文件；刷新页面、切换路径都不会改变版本或触发清理。"""
    version = int(path.get("render_version") or 0)
    directory = export_dir(path["id"], version)
    if not version or not (directory / "long.png").is_file() or not (directory / "steps.md").is_file():
        raise RenderError("还没有可查看的生成结果，请先生成长图与文档")
    long_rel = f"/media/exports/{path['id']}/{version}/long.png"
    return {
        "version": version,
        "long_image_url": long_rel,
        "long_image_abs_url": storage.media_abs_url(long_rel),
        "markdown": (directory / "steps.md").read_text(encoding="utf-8"),
        "steps": [],
    }


def _published_versions(path: dict) -> set[int]:
    urls = path.get("kb_managed_image_urls") or (path.get("kb_published_result") or {}).get("image_urls") or []
    prefix = f"/media/exports/{path.get('id')}/"
    versions = set()
    for url in urls:
        url_path = urlsplit(str(url)).path
        if url_path.startswith(prefix):
            part = url_path[len(prefix):].split("/", 1)[0]
            if part.isdigit():
                versions.add(int(part))
    return versions


def _cleanup_old_versions(path_id: str, keep: int = 3, current: int = 1,
                          protected: set[int] | None = None) -> None:
    base = config.EXPORT_DIR / path_id
    if not base.exists():
        return
    versions = []
    for child in base.iterdir():
        if child.is_dir() and child.name.isdigit():
            versions.append(int(child.name))
    for v in sorted(versions, reverse=True)[keep:]:
        if v == current or v in (protected or set()):
            continue
        shutil.rmtree(base / str(v), ignore_errors=True)


def path_state(graph: dict, path: dict) -> dict:
    """路径的展示状态：是否需要重渲染、是否已入库。"""
    return {
        "id": path.get("id"),
        "title": path.get("title"),
        "description": path.get("description"),
        "node_ids": list(path.get("node_ids") or []),
        "step_count": len(path.get("node_ids") or []),
        "annotations": path.get("annotations") or {},
        "annotation_invalidated_nodes": list(path.get("annotation_invalidated_nodes") or []),
        "question": path.get("question", ""),
        "product_category_name": path.get("product_category_name", ""),
        "product_name": path.get("product_name", ""),
        "kb_wiki_id": path.get("kb_wiki_id") or path.get("id"),
        "stale": bool(path.get("stale")),
        "render_version": path.get("render_version", 0),
        "rendered_at": path.get("rendered_at"),
        "long_image_url": path.get("long_image_url", ""),
        "kb_published_at": path.get("kb_published_at"),
        "kb_published_version": path.get("kb_published_version") or max(_published_versions(path), default=0),
        "kb_published_result": path.get("kb_published_result"),
    }


def _guide_markers(wiki_id: str) -> tuple[str, str]:
    """一条路径在 KB 答案里只负责这一对标记之间的内容。"""
    return f"<!-- APP_GUIDE_BEGIN:{wiki_id} -->", f"<!-- APP_GUIDE_END:{wiki_id} -->"


def build_kb_answer_block(path: dict, render_result: dict) -> str:
    """生成要嵌入 KB 答案的局部区块；标记是 HTML 注释，不影响 Markdown 阅读。"""
    wiki_id = str(path.get("kb_wiki_id") or path.get("id") or path.get("title"))
    begin, end = _guide_markers(wiki_id)
    long_abs = render_result.get("long_image_abs_url") or storage.media_abs_url(
        render_result.get("long_image_url", ""))
    markdown = imaging.render_markdown(
        path,
        render_result.get("steps", []),
        long_abs,
        version=int(render_result.get("version") or 1),
        changed_step=render_result.get("changed_step"),
        generated_at=render_result.get("generated_at"),
        heading_level=2,
    )
    return f"{begin}\n{markdown.rstrip()}\n{end}"


def merge_kb_answer(existing_answer: str | None, block: str, path: dict) -> str:
    """只更新当前路径负责的答案区块，标记外的原始文字逐字保留。

    - 已有标记：只替换标记对之间的内容。
    - 旧版本工作台生成的整篇答案：识别后迁移成带标记的区块，避免重复追加。
    - 普通人工答案：第一次推送在末尾追加本路径区块，之后走标记替换。
    - 标记残缺或重复：拒绝写入，避免误伤人工内容。
    """
    answer = existing_answer or ""
    wiki_id = str(path.get("kb_wiki_id") or path.get("id") or path.get("title"))
    begin, end = _guide_markers(wiki_id)
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)
    matches = list(pattern.finditer(answer))
    if len(matches) == 1 and answer.count(begin) == 1 and answer.count(end) == 1:
        match = matches[0]
        return answer[:match.start()] + block + answer[match.end():]
    if begin in answer or end in answer:
        raise RenderError(
            f"知识库答案中的 APP 指引标记不完整或重复（{wiki_id}），"
            "为避免覆盖人工内容，已停止推送；请先在 KB 中修正标记"
        )

    if answer:
        # 兼容旧版：它把整条 answer 覆盖成工作台文档，且没有标记。
        # 只有标题、长图地址和两个固定章节都吻合时才认定整篇都是旧指引。
        normalized = answer.lstrip()
        legacy_title = f"# {path.get('title') or '操作指引'}\n"
        export_keys = {
            f"/media/exports/{path.get('id')}/",
            f"/media/exports/{wiki_id}/",
        }
        if (normalized.startswith(legacy_title)
                and any(key in answer for key in export_keys)
                and "## 操作步骤" in answer and "## 变更记录" in answer):
            return block + "\n"
        separator = "" if answer.endswith("\n\n") else ("\n" if answer.endswith("\n") else "\n\n")
        return answer + separator + "---\n\n" + block + "\n"
    return block + "\n"


def merge_kb_image_urls(existing_urls: Any, long_abs: str, path: dict) -> list[str]:
    """图片字段只维护本路径的长图，其他人工维护的图片链接原样保留。

    ``kb_managed_image_urls`` 记录本工作台上次写入的图片。旧版没有该字段时，
    从旧的发布结果里识别并清理长图与分步截图，完成一次平滑迁移。
    """
    if not long_abs:
        raise RenderError("长图地址为空，无法更新知识库图片链接")
    wiki_id = str(path.get("kb_wiki_id") or path.get("id") or path.get("title"))
    previous_managed = path.get("kb_managed_image_urls")
    if previous_managed is None:
        previous_result = path.get("kb_published_result") or {}
        previous_managed = previous_result.get("image_urls") or []
    managed = {str(url) for url in previous_managed if url}
    managed.add(long_abs)
    export_keys = (
        f"/media/exports/{path.get('id')}/",
        f"/media/exports/{wiki_id}/",
    )

    preserved: list[str] = []
    for raw_url in existing_urls or []:
        url = str(raw_url)
        if not url or url in managed:
            continue
        url_path = urlsplit(url).path
        if any(key in url_path for key in export_keys):
            continue
        if url not in preserved:
            preserved.append(url)
    return [long_abs] + preserved


def publish_path(graph: dict, path: dict, render_result: dict | None = None) -> dict:
    """把路径推送到 KnowBase Hub。

    - question_wiki_id 仅作为定位条目的幂等键。
    - 除该键外，payload 只含 answer 与 image_urls，绝不覆盖问题、型号等字段。
    - answer 只替换本路径的标记区块；image_urls 只维护本路径的拼接长图。
    """
    wiki_id = path.get("kb_wiki_id") or path.get("id") or path.get("title")
    if not wiki_id:
        raise RenderError("这条路径还没有知识库 ID，无法入库")

    if render_result is None:
        render_result = render_path(graph, path)

    long_abs = render_result.get("long_image_abs_url") or storage.media_abs_url(
        render_result.get("long_image_url", ""))
    client = get_client()
    existing = client.get_item(str(wiki_id))
    if existing is None:
        raise RenderError(
            f"未找到知识库条目 {wiki_id}。局部同步只更新已有条目的答案和图片链接，"
            "请先在 KnowBase Hub 建好该条目，避免误建缺少问题和型号的空条目"
        )
    if not isinstance(existing, dict):
        raise RenderError("知识库条目读取格式异常，为避免覆盖原答案，已停止推送")

    block = build_kb_answer_block(path, render_result)
    answer = merge_kb_answer(existing.get("answer"), block, path)
    image_urls = merge_kb_image_urls(existing.get("image_urls"), long_abs, path)
    payload = {
        "question_wiki_id": wiki_id,
        "answer": answer,
        "image_urls": image_urls,
    }
    result = client.upsert_item(payload)

    path["kb_published_at"] = storage.now_iso()
    path["kb_published_version"] = render_result.get("version")
    path["kb_managed_image_urls"] = [long_abs]
    path["kb_published_result"] = {
        "success": True,
        "question_wiki_id": wiki_id,
        "updated_fields": ["answer", "image_urls"],
        "image_urls": image_urls,
        "message": _extract_message(result),
    }
    return path["kb_published_result"]


def _extract_message(result: Any) -> str:
    if isinstance(result, dict):
        for key in ("message", "msg", "status"):
            if result.get(key):
                return str(result[key])
        data = result.get("data")
        if isinstance(data, dict):
            for key in ("question_wiki_id", "id"):
                if data.get(key):
                    return f"已写入知识库，条目 ID：{data[key]}"
        return "已写入知识库"
    return "已写入知识库"


def affected_paths(graph: dict, node_id: str) -> list[dict]:
    """找出包含某个节点的所有路径（改版后需要重新渲染的那些）。"""
    return [p for p in graph.get("paths", []) if node_id in (p.get("node_ids") or [])]
