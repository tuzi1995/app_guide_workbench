"""Flask 入口：全部 HTTP 路由。

约定：
- 所有 JSON 响应带 success 布尔字段；失败时 message 是面向零基础用户的中文说明。
- 参数问题 400、资源不存在 404、知识库拒绝 502。
"""
from __future__ import annotations

import io
import os
import re
import shutil
import threading
import traceback
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory
from flask_cors import CORS

import config
import graph as graph_mod
import imaging
import render as render_mod
import storage
from kb_client import HubError, get_client

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}}, expose_headers=["*"])
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 单个上传上限 32MB

# 原子写文件只能防止文件损坏；整个「读→改→写」也必须串行，避免同时编辑丢数据。
_graph_lock = threading.RLock()


@app.before_request
def lock_graph_request():
    if (request.path == "/api/graph"
            or request.path.startswith(("/api/nodes", "/api/edges", "/api/paths"))
            or (request.method == "DELETE" and request.path.startswith("/api/media/"))):
        _graph_lock.acquire()
        g.graph_locked = True


@app.teardown_request
def unlock_graph_request(_error):
    if g.pop("graph_locked", False):
        _graph_lock.release()


def ok(data=None, message: str = ""):
    payload = {"success": True}
    if message:
        payload["message"] = message
    if data is not None:
        payload["data"] = data
    return jsonify(payload)


def fail(message: str, status: int = 400, **extra):
    payload = {"success": False, "message": message}
    payload.update(extra)
    return jsonify(payload), status


def project_id() -> str:
    """从查询参数或请求头拿项目 ID，默认 app_guide。"""
    pid = (request.args.get("project_id") or "").strip()
    return pid or config.DEFAULT_PROJECT_ID


# ================================================================ 健康检查

@app.get("/api/health")
def api_health():
    return ok({
        "version": "1.0.0",
        "hub_url": config.HUB_URL,
        "public_base_url": config.PUBLIC_BASE_URL,
        "backend_time": storage.now_iso(),
        "diff_ratio_threshold": config.DIFF_RATIO_THRESHOLD,
    })


# ================================================================ 图文档

@app.get("/api/graph")
def api_get_graph():
    graph = storage.load_graph(project_id())
    return ok(graph)


@app.put("/api/graph")
def api_put_graph():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return fail("请求体必须是 JSON 对象")
    graph = storage.load_graph(project_id())
    for key in ("title", "nodes", "edges", "paths"):
        if key in payload:
            graph[key] = payload[key]
    problems = graph_mod.validate_graph(graph)
    storage.save_graph(graph, project_id())
    result = {"saved_at": graph["updated_at"]}
    if problems:
        result["warnings"] = problems
        result["warning_text"] = "画布已保存，但有路径的连线不完整：" + "；".join(
            f"{pid} → {'；'.join(items)}" for pid, items in problems.items()
        )
    return ok(result, "画布已保存")


# ================================================================ 节点

def _find_node(graph: dict, node_id: str):
    for node in graph.get("nodes", []):
        if node.get("id") == node_id:
            return node
    return None


@app.post("/api/nodes")
def api_create_node():
    payload = request.get_json(silent=True) or {}
    graph = storage.load_graph(project_id())
    node_id = (payload.get("id") or "").strip() or graph_mod.next_id(
        (n.get("id") for n in graph["nodes"]), "n")
    if _find_node(graph, node_id):
        return fail(f"节点 ID {node_id} 已存在", 400)
    # 没指定坐标时自动错开摆放，避免新节点全叠在一起看不见
    if payload.get("x") is None or payload.get("y") is None:
        pos_x, pos_y = _auto_position(graph)
    else:
        pos_x = int(payload.get("x") or 120)
        pos_y = int(payload.get("y") or 120)
    node = {
        "id": node_id,
        "title": payload.get("title") or "新节点",
        "action": payload.get("action") or "",
        "description": payload.get("description") or "",
        "screenshot": payload.get("screenshot") or "",
        "screenshot_prev": "",
        "screenshot_version": 1,
        "changed": False,
        "diff_ratio": 0.0,
        "x": pos_x,
        "y": pos_y,
    }
    graph["nodes"].append(node)
    storage.save_graph(graph, project_id())
    return ok(node, "节点已创建")


def _auto_position(graph: dict) -> tuple[int, int]:
    """按网格自动排布新节点的位置。"""
    cols = 4
    step_x, step_y = 300, 420
    index = len(graph.get("nodes") or [])
    return 120 + (index % cols) * step_x, 120 + (index // cols) * step_y


@app.patch("/api/nodes/<node_id>")
def api_update_node(node_id: str):
    payload = request.get_json(silent=True) or {}
    graph = storage.load_graph(project_id())
    node = _find_node(graph, node_id)
    if not node:
        return fail(f"找不到节点 {node_id}", 404)
    if "screenshot" in payload and payload["screenshot"] != node.get("screenshot"):
        filename = payload["screenshot"]
        try:
            image = storage.media_path(filename).read_bytes() if filename else b""
        except (ValueError, OSError):
            return fail("选中的截图不存在，请刷新素材池后重试", 400)
        _replace_screenshot(graph, node, filename, image)
        screenshot_changed = True
    else:
        screenshot_changed = False
    # 这些字段会写进长图和文字步骤文档，改了之后要重新生成
    doc_fields = ("title", "action", "screenshot")
    doc_changed = screenshot_changed
    for key in ("title", "action", "description", "screenshot", "x", "y"):
        if key in payload:
            if key in doc_fields and node.get(key) != payload[key]:
                doc_changed = True
            node[key] = payload[key]

    affected = []
    if doc_changed:
        # 文字/截图变了 → 沿连线找出用到这个节点的路径，重新生成长图与文档
        for path in render_mod.affected_paths(graph, node_id):
            entry = {"id": path.get("id"), "title": path.get("title"),
                     "rendered": False, "message": ""}
            try:
                result = render_mod.render_path(graph, path)
                entry.update({
                    "rendered": True,
                    "version": result["version"],
                    "long_image_url": result["long_image_url"],
                    "message": "已自动重新生成拼接长图和文字步骤文档",
                })
            except render_mod.RenderError as exc:
                path["stale"] = True
                entry["message"] = str(exc)
            affected.append(entry)

    storage.save_graph(graph, project_id())
    return ok({**node, "affected_paths": affected}, "节点已更新")


@app.delete("/api/nodes/<node_id>")
def api_delete_node(node_id: str):
    graph = storage.load_graph(project_id())
    if not _find_node(graph, node_id):
        return fail(f"找不到节点 {node_id}", 404)
    graph["nodes"] = [n for n in graph["nodes"] if n.get("id") != node_id]
    # 连带删掉它身上的连线，并从路径里摘除
    graph["edges"] = [e for e in graph["edges"]
                      if e.get("source") != node_id and e.get("target") != node_id]
    for path in graph.get("paths", []):
        if node_id in (path.get("node_ids") or []):
            path["node_ids"] = [n for n in path["node_ids"] if n != node_id]
            path["stale"] = True
    storage.save_graph(graph, project_id())
    return ok({"id": node_id}, "节点已删除")


# ================================================================ 连线

@app.post("/api/edges")
def api_create_edge():
    payload = request.get_json(silent=True) or {}
    graph = storage.load_graph(project_id())
    source = (payload.get("source") or "").strip()
    target = (payload.get("target") or "").strip()
    if not source or not target:
        return fail("连线必须要有起点和终点", 400)
    if source == target:
        return fail("连线不能指向自己", 400)
    if not _find_node(graph, source):
        return fail(f"起点节点 {source} 不存在", 404)
    if not _find_node(graph, target):
        return fail(f"终点节点 {target} 不存在", 404)
    if any(e.get("source") == source and e.get("target") == target for e in graph["edges"]):
        return fail("这两个节点之间已经有连线了", 400)
    edge_id = (payload.get("id") or "").strip() or graph_mod.next_id(
        (e.get("id") for e in graph["edges"]), "e")
    edge = {"id": edge_id, "source": source, "target": target, "label": payload.get("label") or ""}
    graph["edges"].append(edge)
    storage.save_graph(graph, project_id())
    return ok(edge, "连线已创建")


@app.delete("/api/edges/<edge_id>")
def api_delete_edge(edge_id: str):
    graph = storage.load_graph(project_id())
    before = len(graph["edges"])
    graph["edges"] = [e for e in graph["edges"] if e.get("id") != edge_id]
    if len(graph["edges"]) == before:
        return fail(f"找不到连线 {edge_id}", 404)
    storage.save_graph(graph, project_id())
    return ok({"id": edge_id}, "连线已删除")


# ================================================================ 路径

def _find_path(graph: dict, path_id: str):
    for path in graph.get("paths", []):
        if path.get("id") == path_id:
            return path
    return None


@app.get("/api/paths")
def api_list_paths():
    graph = storage.load_graph(project_id())
    items = [render_mod.path_state(graph, p) for p in graph.get("paths", [])]
    return ok(items)


@app.post("/api/paths")
def api_create_path():
    payload = request.get_json(silent=True) or {}
    graph = storage.load_graph(project_id())
    path_id = (payload.get("id") or "").strip() or graph_mod.next_id(
        (p.get("id") for p in graph["paths"]), "p")
    if _find_path(graph, path_id):
        return fail(f"路径 ID {path_id} 已存在", 400)
    path = {
        "id": path_id,
        "title": payload.get("title") or "新路径",
        "description": payload.get("description") or "",
        "node_ids": list(payload.get("node_ids") or []),
        "question": payload.get("question") or "",
        "product_category_name": payload.get("product_category_name") or "",
        "product_name": payload.get("product_name") or "",
        "kb_wiki_id": payload.get("kb_wiki_id") or path_id,
        # 路径专属标注：{node_id: [{type:'rect'|'arrow'|'text', ...}]}
        # 标注只存配置，不修改原始截图；截图替换时会按节点清空。
        "annotations": payload.get("annotations") if isinstance(payload.get("annotations"), dict) else {},
        "annotation_invalidated_nodes": [],
        "annotations_stale": False,
        "stale": True,
        "render_version": 0,
    }
    graph["paths"].append(path)
    storage.save_graph(graph, project_id())
    return ok(path, "路径已创建")


@app.patch("/api/paths/<path_id>")
def api_update_path(path_id: str):
    payload = request.get_json(silent=True) or {}
    graph = storage.load_graph(project_id())
    path = _find_path(graph, path_id)
    if not path:
        return fail(f"找不到路径 {path_id}", 404)
    doc_changed = any(key in payload and payload[key] != path.get(key)
                      for key in ("title", "description", "node_ids", "annotations"))
    for key in ("title", "description", "node_ids", "question",
                "product_category_name", "product_name", "kb_wiki_id", "annotations", "annotation_invalidated_nodes"): 
        if key in payload:
            path[key] = payload[key]
    # 标题、步骤或标注变化都会影响已生成的图文档。
    if doc_changed:
        path["stale"] = True
    if "annotation_invalidated_nodes" in payload:
        path["annotations_stale"] = bool(path["annotation_invalidated_nodes"])
    storage.save_graph(graph, project_id())
    return ok(render_mod.path_state(graph, path), "路径已更新")


@app.delete("/api/paths/<path_id>")
def api_delete_path(path_id: str):
    graph = storage.load_graph(project_id())
    if not _find_path(graph, path_id):
        return fail(f"找不到路径 {path_id}", 404)
    graph["paths"] = [p for p in graph["paths"] if p.get("id") != path_id]
    storage.save_graph(graph, project_id())
    return ok({"id": path_id}, "路径已删除")


@app.post("/api/paths/derive")
def api_derive_paths():
    """从一个节点自动推导到另一个节点的所有路径（沿连线方向 BFS）。"""
    payload = request.get_json(silent=True) or {}
    source = (payload.get("source") or "").strip()
    target = (payload.get("target") or "").strip()
    if not source or not target:
        return fail("必须提供起点节点和终点节点", 400)
    graph = storage.load_graph(project_id())
    chains = graph_mod.derive_paths(graph, source, target)
    return ok([{"node_ids": c, "step_count": len(c)} for c in chains])


# ================================================================ 截图上传（局部改版核心）

def _allowed_upload(filename: str) -> bool:
    ext = Path(filename or "").suffix.lower()
    return ext in config.ALLOWED_UPLOAD_EXT


def _replace_screenshot(graph: dict, node: dict, filename: str, png_bytes: bytes) -> dict:
    """两种换图入口共用版本记录、改版检测与标注失效规则。"""
    node_id = node["id"]
    # 旧图做像素差对比
    old_filename = node.get("screenshot") or ""
    old_ratio = None
    old_bytes = b""
    if old_filename:
        try:
            old_path = storage.media_path(old_filename)
            if old_path.exists():
                old_bytes = old_path.read_bytes()
        except ValueError:
            old_bytes = b""
    if old_bytes:
        old_ratio = imaging.diff_ratio(old_bytes, png_bytes)
        changed = imaging.is_changed(old_ratio)
    else:
        changed = False   # 首次上传不算改版

    node["screenshot"] = filename
    node["screenshot_prev"] = old_filename
    node["screenshot_version"] = int(node.get("screenshot_version") or 1) + 1
    # 原图替换后，旧标注不再可信：只清除该节点的路径专属标注，保留同一路径其它节点的标注。
    cleared_annotation_paths = []
    for related_path in graph.get("paths", []):
        if node_id not in (related_path.get("node_ids") or []):
            continue
        annotations = related_path.get("annotations")
        if not isinstance(annotations, dict):
            annotations = {}
        had_annotations = bool(annotations.pop(node_id, None))
        invalidated = related_path.get("annotation_invalidated_nodes")
        if not isinstance(invalidated, list):
            invalidated = []
        if node_id not in invalidated:
            invalidated.append(node_id)
        related_path["annotations"] = annotations
        related_path["annotation_invalidated_nodes"] = invalidated
        related_path["annotations_stale"] = True
        related_path["stale"] = True
        if had_annotations:
            cleared_annotation_paths.append(related_path.get("id"))
    node["changed"] = changed
    node["diff_ratio"] = round(old_ratio, 4) if old_ratio is not None else 0.0

    return {"changed": changed, "cleared_annotation_paths": cleared_annotation_paths}


@app.post("/api/nodes/<node_id>/screenshot")
def api_upload_screenshot(node_id: str):
    """替换某个节点的截图 —— 需求里「局部改版」的入口。

    关键行为：只动这一个节点；其它节点的图片文件既不复制也不改名。
    随后沿连线找出受影响路径，自动重渲染长图与文字步骤文档。
    """
    graph = storage.load_graph(project_id())
    node = _find_node(graph, node_id)
    if not node:
        return fail(f"找不到节点 {node_id}", 404)

    file = request.files.get("file")
    if file is None or not file.filename:
        return fail("没有收到图片文件", 400)
    if not _allowed_upload(file.filename):
        return fail(f"不支持的图片格式（{Path(file.filename).suffix}），"
                    f"请上传 {', '.join(sorted(config.ALLOWED_UPLOAD_EXT))} 格式", 400)

    raw = file.read()
    if not raw:
        return fail("上传的图片是空的", 400)
    try:
        png_bytes = imaging.decode_to_png(raw)
    except Exception as exc:
        return fail(f"图片无法解析，请换一张：{exc}", 400)

    media = storage.save_media(png_bytes, ".png")
    replacement = _replace_screenshot(graph, node, media["filename"], png_bytes)
    changed = replacement["changed"]
    cleared_annotation_paths = replacement["cleared_annotation_paths"]

    # 沿连线找受影响路径，自动重渲染
    affected = []
    for path in render_mod.affected_paths(graph, node_id):
        entry = {"id": path.get("id"), "title": path.get("title"), "stale": True,
                 "rendered": False, "long_image_url": path.get("long_image_url", ""),
                 "message": "",
                  "annotations_stale": path.get("annotations_stale", False)}
        try:
            result = render_mod.render_path(graph, path, changed_step=_changed_step_index(path, node_id))
            entry.update({
                "stale": False,
                "rendered": True,
                "long_image_url": result["long_image_url"],
                "version": result["version"],
                "message": "已自动重新生成拼接长图和文字步骤文档",
            })
        except render_mod.RenderError as exc:
            entry["message"] = str(exc)
        affected.append(entry)

    storage.save_graph(graph, project_id())

    return ok({
        "filename": media["filename"],
        "url": media["url"],
        "abs_url": storage.media_abs_url(media["url"]),
        "changed": changed,
        "diff_ratio": node["diff_ratio"],
        "version": node["screenshot_version"],
        "node": node,
        "affected_paths": affected,
        "cleared_annotation_paths": cleared_annotation_paths,
    }, ("检测到页面改版，已自动更新相关路径" if changed else "截图已替换"))


def _changed_step_index(path: dict, node_id: str) -> int | None:
    node_ids = path.get("node_ids") or []
    if node_id in node_ids:
        return node_ids.index(node_id) + 1
    return None


@app.post("/api/media")
def api_upload_media():
    """上传截图到素材库（不绑定节点）。"""
    file = request.files.get("file")
    if file is None or not file.filename:
        return fail("没有收到图片文件", 400)
    if not _allowed_upload(file.filename):
        return fail(f"不支持的图片格式（{Path(file.filename).suffix}）", 400)
    raw = file.read()
    try:
        png_bytes = imaging.decode_to_png(raw)
    except Exception as exc:
        return fail(f"图片无法解析，请换一张：{exc}", 400)
    media = storage.save_media(png_bytes, ".png")
    media["abs_url"] = storage.media_abs_url(media["url"])
    return ok(media, "截图已上传")


@app.get("/api/media")
def api_list_media():
    return ok(storage.list_media())


@app.delete("/api/media/<path:filename>")
def api_delete_media(filename: str):
    # 素材库由所有项目共用，不能只检查当前画布。
    for flow_file in config.FLOW_DIR.glob("*.json"):
        graph = storage.read_json(flow_file)
        for node in graph.get("nodes", []):
            if filename in (node.get("screenshot"), node.get("screenshot_prev")):
                return fail(f"图片仍被「{graph.get('title') or flow_file.stem}」中的节点"
                            f"「{node.get('title') or node.get('id')}」引用，请先替换或移除引用", 409)
    if not storage.delete_media(filename):
        return fail(f"找不到图片 {filename}", 404)
    return ok({"filename": filename}, "图片已删除")


# ================================================================ 渲染 / 导出 / 推送

@app.post("/api/paths/<path_id>/render")
def api_render_path(path_id: str):
    graph = storage.load_graph(project_id())
    path = _find_path(graph, path_id)
    if not path:
        return fail(f"找不到路径 {path_id}", 404)
    try:
        result = render_mod.render_path(graph, path)
    except render_mod.RenderError as exc:
        return fail(str(exc), 400)
    storage.save_graph(graph, project_id())
    return ok({
        "long_image_url": result["long_image_url"],
        "long_image_abs_url": result["long_image_abs_url"],
        "long_image_path": result["long_image_path"],
        "markdown": result["markdown"],
        "steps": result["steps"],
        "version": result["version"],
    }, "长图与步骤文档已生成")

@app.get("/api/paths/<path_id>/export")
def api_export_path(path_id: str):
    """读取上次生成的长图与 Markdown，查看操作不生成新版本。"""
    graph = storage.load_graph(project_id())
    path = _find_path(graph, path_id)
    if not path:
        return fail(f"找不到路径 {path_id}", 404)
    try:
        result = render_mod.read_export(graph, path)
    except render_mod.RenderError as exc:
        return fail(str(exc), 404)
    return ok({
        "markdown": result["markdown"],
        "long_image_url": result["long_image_url"],
        "long_image_abs_url": result["long_image_abs_url"],
        "steps": result["steps"],
        "version": result["version"],
    })


@app.post("/api/paths/<path_id>/publish")
def api_publish_path(path_id: str):
    """重渲染 + 推送到 KnowBase Hub。"""
    graph = storage.load_graph(project_id())
    path = _find_path(graph, path_id)
    if not path:
        return fail(f"找不到路径 {path_id}", 404)
    try:
        render_result = render_mod.render_path(graph, path)
        result = render_mod.publish_path(graph, path, render_result)
    except render_mod.RenderError as exc:
        storage.save_graph(graph, project_id())
        return fail(str(exc), 400)
    except HubError as exc:
        storage.save_graph(graph, project_id())
        return fail(exc.message, 502, hub_status=exc.status)
    storage.save_graph(graph, project_id())
    return ok(result, result.get("message") or "已推送到知识库")


# ================================================================ 知识库相关

@app.get("/api/hub/status")
def api_hub_status():
    try:
        data = get_client().status()
    except HubError as exc:
        return fail(exc.message, 502)
    return ok(data)


@app.get("/api/hub/item/<question_wiki_id>")
def api_hub_item(question_wiki_id: str):
    """回查知识库里的一条，确认推送是否真的落库了。"""
    try:
        data = get_client().get_item(question_wiki_id)
    except HubError as exc:
        return fail(exc.message, 502)
    if not data:
        return fail(f"知识库里没有找到条目 {question_wiki_id}", 404)
    return ok(data)


@app.post("/api/hub/login")
def api_hub_login():
    payload = request.get_json(silent=True) or {}
    client = get_client()
    if payload.get("username"):
        client.username = payload["username"]
    if payload.get("password"):
        client.password = payload["password"]
    try:
        client.login()
    except HubError as exc:
        return fail(exc.message, 502)
    return ok({"username": client.username}, "已登录知识库")


@app.get("/api/models")
def api_models():
    """型号库，给前端「产品型号」下拉框用。取不到时返回空字典而不是报错。"""
    try:
        catalog = get_client().product_catalog()
    except HubError:
        catalog = {}
    return ok(catalog)


# ================================================================ 静态媒体服务

@app.get("/media/<path:filename>")
def serve_media(filename: str):
    """提供截图与导出的长图。

    知识库里的 image_urls 指向这里，所以这个地址必须能被浏览器直接打开。
    """
    media_root = config.MEDIA_DIR.resolve()
    target = (config.MEDIA_DIR / filename).resolve()
    if not str(target).startswith(str(media_root) + os.sep):
        return fail("非法路径", 400)
    if not target.is_file():
        return fail(f"找不到文件 {filename}", 404)
    # 用相对路径回传，保留子目录结构（例如 exports/p1/6/long.png）
    return send_from_directory(media_root, str(target.relative_to(media_root)))


@app.get("/media/exports/<path:relpath>")
def serve_export(relpath: str):
    """提供导出的长图与 Markdown。"""
    export_root = config.EXPORT_DIR.resolve()
    target = (config.EXPORT_DIR / relpath).resolve()
    if not str(target).startswith(str(export_root) + os.sep):
        return fail("非法路径", 400)
    if not target.is_file():
        return fail(f"找不到文件 {relpath}", 404)
    # 同上：必须传相对路径，否则 p1/6/long.png 会被当成 long.png 去根目录找
    return send_from_directory(export_root, str(target.relative_to(export_root)))


# ================================================================ 错误处理

@app.errorhandler(413)
def too_large(_):
    return fail("文件太大了，单张图片请控制在 32MB 以内", 413)


@app.errorhandler(404)
def not_found(_):
    return fail("接口不存在", 404)


@app.errorhandler(500)
def server_error(exc):  # pragma: no cover
    traceback.print_exc()
    return fail(f"服务端出错：{exc}", 500)


if __name__ == "__main__":
    print(f"[app_guide_workbench] 后端启动: http://{config.HOST}:{config.PORT}")
    print(f"[app_guide_workbench] 媒体服务: {config.PUBLIC_BASE_URL}/media/")
    print(f"[app_guide_workbench] 知识库: {config.HUB_URL}")
    app.run(host=config.HOST, port=config.PORT, debug=False, threaded=True)
