"""浏览器端联调前的后端接口冒烟测试。

覆盖需求里的关键闭环：
1. 建节点 / 建连线 / 建路径
2. 上传截图 → 触发像素差 → 判定改版
3. 只替换一个节点的截图，其它节点的文件名与 sha1 保持不变
4. 沿连线自动重渲染长图 + Markdown
5. 推送 KnowBase Hub → 用 question_wiki_id 回查，确认是同一条记录（幂等）
6. 重复推送 → 条目数不增加

用法：
    cd backend && .venv/bin/python scripts/smoke.py
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# 允许直接 `python scripts/smoke.py` 运行：把 backend/ 放进模块搜索路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://127.0.0.1:8095"
PROJECT = "smoke_test"
# 冒烟测试写进 Hub 的知识库条目 ID：加前缀，和正式条目（p1、p2…）分开
KB_WIKI_ID = "smoke_test_login_guide"

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [通过] {name}")
    else:
        FAIL += 1
        print(f"  [失败] {name}  {detail}")


def call(method: str, path: str, body=None, files=None):
    """发请求；返回 (状态码, 解析后的 JSON)。"""
    # 所有请求都带上 project_id，测试数据落在独立图文档里，不污染正式画布
    sep = "&" if "?" in path else "?"
    url = f"{BASE}{path}{sep}project_id={PROJECT}"
    data = None
    headers = {}
    if files:
        boundary = "----smokeboundary1234567890"
        buf = io.BytesIO()
        for field, (filename, content) in files.items():
            buf.write(f"--{boundary}\r\n".encode())
            buf.write(
                f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode()
            )
            buf.write(b"Content-Type: image/png\r\n\r\n")
            buf.write(content)
            buf.write(b"\r\n")
        buf.write(f"--{boundary}--\r\n".encode())
        data = buf.getvalue()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            if "json" in ctype:
                text = raw.decode("utf-8")
                return resp.status, (json.loads(text) if text else {})
            # 非 JSON 响应（长图 PNG 等）只返回字节长度，不做解码
            return resp.status, {"bytes": len(raw), "content_type": ctype}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def make_png(color: tuple[int, int, int], size=(360, 720)) -> bytes:
    """造一张纯色 PNG 当截图（没有真实 APP 截图时够用）。"""
    from PIL import Image

    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def seed_hub_entry(wiki_id: str) -> None:
    """预置一条含人工内容和外部图片的 KB 条目，验证推送不会误伤其他字段。"""
    from kb_client import get_client

    get_client().upsert_item({
        "question_wiki_id": wiki_id,
        "question": "人工维护的问题（推送不应覆盖）",
        "question_type": "人工维护类型",
        "answer": "## 人工维护说明\n这段文字不能丢。\n\n## 注意事项\n也不要改动。",
        "image_urls": ["https://example.com/manual.png"],
        "product_category_name": "扫地机",
        "product_name": "H5",
    })


def cleanup_hub_entry(wiki_id: str) -> None:
    """把冒烟测试写进 Hub 的条目删掉，不留测试数据在正式知识库里。

    Hub 的 /api/kb/delete 走软删除（review_status='deleting'）并写审计日志，
    这里直接调它的接口；失败只提示、不算测试失败。
    """
    try:
        from kb_client import get_client

        result = get_client().delete_item(wiki_id, "APP操作指引工作台-冒烟测试清理")
        print(f"  [清理] 知识库条目 {wiki_id} → {result}")
    except Exception as exc:  # noqa: BLE001 - 清理失败不该让测试挂掉
        print(f"  [清理] 知识库条目 {wiki_id} 清理失败（不影响测试结果）：{exc}")


def main() -> int:
    print("=" * 62)
    print("后端接口冒烟测试  —— 验证「局部改版只换一个节点」的核心闭环")
    print("=" * 62)

    # ---------------- 0. 健康检查 ----------------
    print("\n[0] 健康检查")
    status, data = call("GET", "/api/health")
    check("GET /api/health 返回 200", status == 200, f"实际 {status}")
    check("后端版本号存在", bool(data.get("data", {}).get("version")))

    # ---------------- 1. 建图 ----------------
    print("\n[1] 建节点 / 连线 / 路径")
    node_ids = []
    for i, (title, action) in enumerate(
        [("我的", "点击右下角「我的」"), ("登录页", "点击「登录」进入登录页"), ("输入框", "点击「获取验证码」")]
    ):
        status, data = call("POST", "/api/nodes", {"title": title, "action": action,
                                                   "x": 120 + i * 300, "y": 200})
        check(f"创建节点 {title}", status == 200, f"实际 {status} {data}")
        if status == 200:
            node_ids.append(data["data"]["id"])

    check("共创建 3 个节点", len(node_ids) == 3)
    edge_ids = []
    for src, tgt in zip(node_ids, node_ids[1:]):
        status, data = call("POST", "/api/edges", {"source": src, "target": tgt})
        check(f"创建连线 {src}→{tgt}", status == 200, f"实际 {status} {data}")
        if status == 200:
            edge_ids.append(data["data"]["id"])

    status, data = call("POST", "/api/paths", {
        "title": "冒烟测试路径",
        "node_ids": node_ids,
        "question": "APP 怎么登录？",
        "product_category_name": "扫地机",
        "product_name": "H5",
        # 用独立前缀的知识库条目 ID，避免和正式条目（p1、p2…）撞号
        "kb_wiki_id": "smoke_test_login_guide",
    })
    check("创建路径", status == 200, f"实际 {status} {data}")
    path_id = data.get("data", {}).get("id", "p1")
    check("路径 id 存在", bool(path_id))

    # ---------------- 2. 上传三张截图 ----------------
    print("\n[2] 上传初始截图（首版不算改版）")
    colors = [(43, 108, 255), (214, 69, 69), (140, 96, 140)]
    for node_id, color in zip(node_ids, colors):
        status, data = call("POST", f"/api/nodes/{node_id}/screenshot",
                            files={"file": ("shot.png", make_png(color))})
        check(f"上传截图到 {node_id}", status == 200, f"实际 {status} {data}")
        if status == 200:
            check(f"{node_id} 首版不算改版", data["data"]["changed"] is False,
                  f"changed={data['data'].get('changed')}")

    # ---------------- 3. 局部改版 ----------------
    print("\n[3] 只替换中间节点的截图（模拟登录页改版）")
    status, graph_before = call("GET", "/api/graph")
    shots_before = {
        n["id"]: n.get("screenshot")
        for n in graph_before["data"]["nodes"]
    }
    path_before = next(
        (p for p in graph_before["data"]["paths"] if p["id"] == path_id), {}
    )
    version_before = path_before.get("render_version", 0)

    new_color = (17, 200, 120)
    status, data = call("POST", f"/api/nodes/{node_ids[1]}/screenshot",
                        files={"file": ("shot.png", make_png(new_color))})
    check("替换截图成功", status == 200, f"实际 {status} {data}")
    changed = data.get("data", {}).get("changed")
    check("被系统识别为「页面改版」", changed is True, f"changed={changed}")
    check("返回了差异度", (data.get("data", {}).get("diff_ratio") or 0) > 0,
          f"diff_ratio={data.get('data', {}).get('diff_ratio')}")

    affected = data.get("data", {}).get("affected_paths") or []
    check("返回了受影响的路径", len(affected) >= 1)
    if affected:
        entry = affected[0]
        check("受影响路径已自动重新生成", entry.get("rendered") is True, str(entry))
        check("提示文案是中文说明",
              "自动重新生成" in (entry.get("message") or ""), str(entry.get("message")))

    # ---------------- 4. 其它节点未被改动 ----------------
    print("\n[4] 关键断言：其它节点的截图文件没有被碰过")
    status, graph_after = call("GET", "/api/graph")
    shots_after = {
        n["id"]: n.get("screenshot")
        for n in graph_after["data"]["nodes"]
    }
    for node_id in (node_ids[0], node_ids[2]):
        check(
            f"{node_id} 的截图文件保持原样",
            shots_before.get(node_id) == shots_after.get(node_id),
            f"{shots_before.get(node_id)} → {shots_after.get(node_id)}",
        )
    check(
        f"{node_ids[1]} 的截图文件确实换了",
        shots_before.get(node_ids[1]) != shots_after.get(node_ids[1]),
    )

    # ---------------- 5. 长图与文档重新生成 ----------------
    print("\n[5] 长图与文字步骤文档自动重新生成")
    path_after = next(
        (p for p in graph_after["data"]["paths"] if p["id"] == path_id), {}
    )
    check("渲染版本号递增",
          path_after.get("render_version", 0) > version_before,
          f"{version_before} → {path_after.get('render_version')}")
    check("路径不再标记为过期", path_after.get("stale") is False)

    long_url = path_after.get("long_image_url") or ""
    check("生成了长图地址", bool(long_url), long_url)
    if long_url:
        status, _ = call("GET", long_url)
        check("长图可以直接打开", status == 200, f"实际 {status}")

    status, exp = call("GET", f"/api/paths/{path_id}/export")
    check("导出口可用", status == 200, f"实际 {status}")
    md = exp.get("data", {}).get("markdown") or ""
    check("Markdown 步骤文档非空", len(md) > 100, f"长度 {len(md)}")
    check("Markdown 里含长图引用", ".png" in md)
    steps = exp.get("data", {}).get("steps") or []
    check("步骤数 = 3", len(steps) == 3, f"实际 {len(steps)}")

    # ---------------- 6. 推送知识库：只局部更新答案与长图 ----------------
    print("\n[6] 推送 KnowBase Hub（局部合并 + 幂等）")
    seed_hub_entry(KB_WIKI_ID)
    manual_answer = "## 人工维护说明\n这段文字不能丢。\n\n## 注意事项\n也不要改动。"
    status, pub = call("POST", f"/api/paths/{path_id}/publish")
    check("推送成功", status == 200, f"实际 {status} {pub}")
    check("声明只更新 answer 和 image_urls",
          pub.get("data", {}).get("updated_fields") == ["answer", "image_urls"],
          str(pub.get("data", {}).get("updated_fields")))
    image_urls = pub.get("data", {}).get("image_urls") or []
    check("只写本路径长图并保留外部图片", len(image_urls) == 2, str(image_urls))
    if image_urls:
        check("长图链接指向本工作台", "/media/exports/" in image_urls[0], image_urls[0])
        check("人工图片链接仍保留", "https://example.com/manual.png" in image_urls, str(image_urls))
        check("分步截图不再塞进 image_urls",
              all("/media/exports/" in url or url == "https://example.com/manual.png"
                  for url in image_urls), str(image_urls))

    status, item = call("GET", f"/api/hub/item/{KB_WIKI_ID}")
    check("知识库里能查到该条目", status == 200, f"实际 {status}")
    row = item.get("data") or {}
    answer1 = row.get("answer") or ""
    check("人工答案原文保留", answer1.startswith(manual_answer), answer1[:160])
    check("答案包含本路径的局部更新标记",
          f"<!-- APP_GUIDE_BEGIN:{KB_WIKI_ID} -->" in answer1
          and f"<!-- APP_GUIDE_END:{KB_WIKI_ID} -->" in answer1)
    check("问题字段没有被覆盖", row.get("question") == "人工维护的问题（推送不应覆盖）",
          str(row.get("question")))
    check("型号字段没有被覆盖", row.get("product_name") == "H5", str(row.get("product_name")))
    check("问题类型没有被覆盖", row.get("question_type") == "人工维护类型",
          str(row.get("question_type")))

    status, pub2 = call("POST", f"/api/paths/{path_id}/publish")
    check("重复推送仍成功（幂等）", status == 200, f"实际 {status} {pub2}")
    status, item2 = call("GET", f"/api/hub/item/{KB_WIKI_ID}")
    answer2 = (item2.get("data") or {}).get("answer") or ""
    check("重复推送不重复追加答案区块", answer2.count("## 冒烟测试路径") == 1,
          f"区块数 {answer2.count('## 冒烟测试路径')}")
    check("重复推送后人工答案仍只有一份", answer2.count(manual_answer) == 1)

    # ---------------- 7. 缺失连线时拒绝生成 ----------------
    print("\n[8] 路径连线不完整时拒绝渲染并给出中文提示")
    status, lone = call("POST", "/api/paths", {
        "title": "断链路径",
        "node_ids": [node_ids[0], node_ids[2]],
    })
    lone_id = lone.get("data", {}).get("id", "")
    if lone_id:
        status, broken = call("POST", f"/api/paths/{lone_id}/render")
        check("断链路径渲染被拒", status >= 400, f"实际 {status}")
        msg = broken.get("message") or ""
        check("提示说明了缺少连线", "缺少连线" in msg, msg)

    # ---------------- 8. 清理测试数据 ----------------
    print("\n[8] 清理测试写入知识库的条目")
    status, _ = call("DELETE", f"/api/paths/{path_id}")
    check("删除测试路径", status == 200, f"实际 {status}")
    cleanup_hub_entry(KB_WIKI_ID)

    # ---------------- 汇总 ----------------
    print("\n" + "=" * 62)
    print(f"结果：通过 {PASS} 项，失败 {FAIL} 项")
    print("=" * 62)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
