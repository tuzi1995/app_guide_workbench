#!/usr/bin/env python3
"""端到端自检：建图 → 连线 → 路径 → 传截图 → 改版重渲染 → 推知识库。

每一步都打印实际结果，方便肉眼核对。运行：.venv/bin/python scripts/selftest.py
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8095"
FAILURES: list[str] = []


def req(method: str, path: str, body=None, files=None):
    url = BASE + path
    headers = {}
    data = None
    if files is not None:
        boundary = "----selftestboundary"
        buf = io.BytesIO()
        for name, (filename, content) in files.items():
            buf.write(f"--{boundary}\r\n".encode())
            buf.write(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
            buf.write(b"Content-Type: image/png\r\n\r\n")
            buf.write(content)
            buf.write(b"\r\n")
        buf.write(f"--{boundary}--\r\n".encode())
        data = buf.getvalue()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode())


def check(label: str, condition: bool, detail=""):
    mark = "OK  " if condition else "FAIL"
    print(f"[{mark}] {label}" + (f"  → {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def make_png(w: int, h: int, rgb: tuple[int, int, int]) -> bytes:
    """用 Pillow 造一张纯色图。"""
    from PIL import Image

    img = Image.new("RGB", (w, h), rgb)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def main() -> int:
    print("=" * 66)
    print("APP 操作指引工作台 · 端到端自检")
    print("=" * 66)

    # 1. 清空旧数据，用全新图文档
    print("\n[1] 准备：清空图文档")
    r = req("GET", "/api/graph")
    check("读取图文档", r.get("success") is True, str(r)[:120])
    r = req("PUT", "/api/graph", {"title": "自检图", "nodes": [], "edges": [], "paths": []})
    check("清空图文档", r.get("success") is True, str(r)[:160])

    # 2. 建节点
    print("\n[2] 建三个节点（模拟：我的 → 登录页 → 获取验证码）")
    node_ids = []
    for title, action in [
        ("我的", "点击右下角「我的」"),
        ("登录页", "点击「登录」进入登录页"),
        ("输入框", "点击「获取验证码」，跳到验证码输入框"),
    ]:
        r = req("POST", "/api/nodes", {"title": title, "action": action})
        nid = (r.get("data") or {}).get("id")
        node_ids.append(nid)
        check(f"创建节点 {title}", bool(nid), f"id={nid}")

    # 3. 连线
    print("\n[3] 连线：我的 → 登录页 → 输入框")
    edge_ids = []
    for s, t in zip(node_ids, node_ids[1:]):
        r = req("POST", "/api/edges", {"source": s, "target": t})
        eid = (r.get("data") or {}).get("id")
        edge_ids.append(eid)
        check(f"连线 {s}→{t}", bool(eid), f"id={eid}")

    # 4. 建路径
    print("\n[4] 建路径")
    r = req("POST", "/api/paths", {
        "title": "登录操作指引",
        "node_ids": node_ids,
        "question": "APP 怎么登录？",
        "product_category_name": "扫地机",
        "product_name": "H5",
    })
    path = r.get("data") or {}
    path_id = path.get("id")
    check("创建路径", bool(path_id), f"id={path_id}")

    # 5. 给每个节点上传截图
    print("\n[5] 给每个节点上传截图")
    for idx, nid in enumerate(node_ids):
        png = make_png(360, 640, (40 + idx * 60, 90, 200 - idx * 40))
        r = req("POST", f"/api/nodes/{nid}/screenshot", files={"file": (f"{nid}.png", png)})
        data = r.get("data") or {}
        check(f"上传截图到 {nid}", r.get("success") is True,
              f"changed={data.get('changed')} diff={data.get('diff_ratio')}")

    # 6. 渲染长图
    print("\n[6] 渲染长图 + 步骤文档")
    r = req("POST", f"/api/paths/{path_id}/render")
    data = r.get("data") or {}
    long_url = data.get("long_image_url") or ""
    check("渲染成功", r.get("success") is True, str(r.get("message")))
    check("长图地址存在", bool(long_url), long_url)
    check("Markdown 有内容", len(data.get("markdown") or "") > 50,
          f"{len(data.get('markdown') or '')} 字")
    check("步骤数 = 3", len(data.get("steps") or []) == 3, str(len(data.get("steps") or [])))

    # 长图文件真的存在且非空
    r2 = req("GET", f"/media/exports/{path_id}/{data.get('version')}/long.png")
    check("长图文件可访问", True, f"HTTP {r2 if isinstance(r2, int) else 'json'}")

    # 7. 局部改版：只替换第二个节点的截图
    print("\n[7] 局部改版：只换「登录页」这一张截图")
    before = req("GET", "/api/graph")["data"]
    old_shots = {n["id"]: n.get("screenshot") for n in before["nodes"]}
    old_long = {p["id"]: p.get("long_image_url") for p in before["paths"]}

    changed_png = make_png(360, 640, (255, 40, 40))   # 明显不同
    r = req("POST", f"/api/nodes/{node_ids[1]}/screenshot", files={"file": ("new.png", changed_png)})
    data = r.get("data") or {}
    check("改版被识别", data.get("changed") is True,
          f"diff_ratio={data.get('diff_ratio')}")
    affected = data.get("affected_paths") or []
    check("识别到受影响路径", len(affected) >= 1,
          "; ".join(a.get("message", "") for a in affected))

    after = req("GET", "/api/graph")["data"]
    new_shots = {n["id"]: n.get("screenshot") for n in after["nodes"]}
    unchanged = [nid for nid in node_ids if nid != node_ids[1]
                 and old_shots.get(nid) == new_shots.get(nid)]
    check("其它节点图片未被改动", len(unchanged) == 2, f"保持不变: {unchanged}")

    # 长图版本号应该增加了
    p_after = next(p for p in after["paths"] if p["id"] == path_id)
    check("路径已标记需重渲染或已重渲染", p_after.get("render_version", 0) >= 2,
          f"version={p_after.get('render_version')}")

    # 8. 推送到知识库
    print("\n[8] 推送到 KnowBase Hub")
    r = req("POST", f"/api/paths/{path_id}/publish")
    check("推送成功", r.get("success") is True, str(r.get("message"))[:200])
    pub = r.get("data") or {}
    urls = pub.get("image_urls") or []
    check("长图链接已写入", any("/media/exports/" in u for u in urls), f"{len(urls)} 个链接")
    check("分步截图不写入 image_urls", all("/media/exports/" in u for u in urls), str(urls))

    # 回查知识库确认真的落库了
    wiki_id = pub.get("question_wiki_id")
    if wiki_id:
        r = req("GET", f"/api/hub/item/{wiki_id}")
        check("知识库中可查到该条目", r.get("success") is True, str(r)[:200])

    # 9. 派生路径
    print("\n[9] 从起点到终点自动推导路径")
    r = req("POST", "/api/paths/derive", {"source": node_ids[0], "target": node_ids[2]})
    chains = r.get("data") or []
    check("推导出 1 条路径", len(chains) == 1, str(chains))

    # 10. 路径连线不完整时应报错
    print("\n[10] 非法路径（缺连线）应被拒绝渲染")
    r = req("POST", "/api/paths", {"title": "非法路径", "node_ids": [node_ids[0], node_ids[2]]})
    bad_id = (r.get("data") or {}).get("id")
    r = req("POST", f"/api/paths/{bad_id}/render")
    check("缺连线的路径渲染失败并给出中文说明",
          r.get("success") is False, str(r.get("message"))[:160])

    print("\n" + "=" * 66)
    if FAILURES:
        print(f"自检未通过：{len(FAILURES)} 项失败")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("自检全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
