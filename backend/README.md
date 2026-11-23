# backend — APP 操作指引工作台后端

Flask + Python 3.12（uv 虚拟环境），端口 **8095**。

## 启动

```bash
cd /Users/tuzi/个人项目/One/app_guide_workbench
./start.sh          # 推荐：一键启动前后端
```

单独启动后端：

```bash
cd backend && .venv/bin/python app.py
```

首次运行若没有 `.venv`，脚本会自动 `uv venv --python 3.12` 并安装 `requirements.txt`。

## 模块

| 文件 | 职责 | 关键函数 |
|---|---|---|
| `app.py` | 全部 HTTP 路由 | `api_upload_node_screenshot`（局部改版入口）、`api_publish_path`（入库） |
| `config.py` | 路径、端口、阈值、Hub 地址 | 改 `PORT` / `PUBLIC_BASE_URL` / `DIFF_RATIO_THRESHOLD` 都在这里 |
| `storage.py` | 图文档读写、媒体库 | `load_graph` / `save_graph`（原子写）/ `save_media`（sha1 内容寻址） |
| `imaging.py` | 像素差、长图拼接、Markdown | `diff_ratio` / `build_long_image` / `render_markdown` |
| `graph.py` | 路径推导与合法性校验 | `validate_path` / `derive_paths` |
| `render.py` | 渲染与发布 | `resolve_steps` / `render_path` / `merge_kb_answer` / `merge_kb_image_urls` / `publish_path` |
| `kb_client.py` | KnowBase Hub 客户端 | `get_item`（物理表精确读取）/ `upsert_item` / `delete_item` / `product_catalog` |

## 局部同步规则

- 长图默认横向（`APP_GUIDE_IMAGE_LAYOUT=horizontal`），也支持 `vertical`。
- KB 答案用 `<!-- APP_GUIDE_BEGIN:<wiki_id> -->` / `<!-- APP_GUIDE_END:<wiki_id> -->` 标出本路径负责的区块；重复或残缺标记会停止写入，避免误伤人工答案。
- 推送 payload 除定位键 `question_wiki_id` 外只有 `answer`、`image_urls`；目标条目必须已经存在。
- `image_urls` 只维护本路径的拼接长图，保留无关外链，不再维护分步截图链接。

## 测试

```bash
.venv/bin/python scripts/test_render_rules.py
.venv/bin/python scripts/smoke.py
```

离线规则测试 11 项；端到端冒烟 51 项，使用独立的 `project_id=smoke_test`，不污染正式画布。

## 依赖说明

`requirements.txt`：`flask`、`flask-cors`、`numpy`、`pillow`、`requests`。
图像 Diff 用 numpy 自研（YIQ 亮度差），**不依赖 pixelmatch**；中文渲染用 macOS 自带字体 `/System/Library/Fonts/Hiragino Sans GB.ttc`（`config.FONT_CANDIDATES` 里还有几个备选）。

接口契约见电子脑洞仓库 `55_KnowBaseHub_8085/APP操作指引自动维护/20261112_APP操作指引工作台技术方案.md`。
