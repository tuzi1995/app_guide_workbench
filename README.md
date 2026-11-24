# APP 操作指引可视化工作台（app_guide_workbench）

把「APP 操作指引」从一堆手工维护的 Markdown，变成**一张可视化画布**：

- **节点** = 一张真实 APP 截图 + 一句操作文字
- **连线** = 页面跳转关系（点击「我的」→ 跳登录页 → 点击「获取验证码」→ 跳输入框）
- **功能路径** = 画布上一条按顺序串起来的节点链 = 一篇操作指引

APP 改版时**只替换那一张截图**：系统自动检测到该节点图片变了，沿连线找出包含它的路径，**重新读取路径上所有节点已有的图片**（其它节点一张都不动），重新拼成长图并同步更新文字步骤文档，需要时一键推送到 KnowBase Hub 知识库。

## 快速开始

```bash
cd "app_guide_workbench"
./start.sh
```

浏览器打开 <http://localhost:5178/>。停止用 `./stop.sh`。

| 服务 | 地址 | 说明 |
|---|---|---|
| 前端工作台 | http://localhost:5178/ | 三栏布局：素材池 / 路径画布 / 路径面板 |
| 后端 API | http://127.0.0.1:8095/api/health | Flask，前端通过 Vite 代理访问 |
| 媒体服务 | http://127.0.0.1:8095/media/... | 截图与导出长图的对外链接 |
| KnowBase Hub | http://127.0.0.1:8085/ | 推送目标；请通过本机配置获取/管理账号凭据，不要将密码写入文档 |

## 目录

```text
app_guide_workbench/
├── start.sh / stop.sh          # 一键启停
├── backend/                    # Flask 后端（Python 3.12 uv venv）
│   ├── app.py                  # 入口 + 全部路由
│   ├── config.py               # 路径、端口、阈值、Hub 地址
│   ├── storage.py              # 图文档读写、媒体库、原子写
│   ├── imaging.py              # 像素差检测、长图拼接、Markdown 渲染
│   ├── graph.py                # 路径推导与合法性校验
│   ├── kb_client.py            # KnowBase Hub 客户端
│   └── scripts/smoke.py        # 端到端冒烟测试（51 项断言）
├── frontend/                   # Vue 3 + Vite 8 + LogicFlow
└── data/                       # 运行时数据
    ├── flows/app_guide.json    # 事实源（唯一数据源）
    ├── media/                  # 内容寻址截图库
    └── exports/<路径ID>/<版本>/ # long.png + steps.md（保留最近 3 版）
```

## 核心机制

| 机制 | 实现位置 | 行为 |
|---|---|---|
| 局部改版检测 | `backend/imaging.py` `diff_ratio()` | YIQ 亮度差逐像素比较，差异像素占比 > 5% 判定为改版 |
| 只换一张图 | `backend/render.py` `resolve_steps()` | 只读现有图片文件，不复制、不改名 → 其它节点天然保持原样 |
| 自动重渲染 | `backend/app.py` `api_update_node` / `api_upload_node_screenshot` | 沿连线找出受影响路径 → 默认按步骤从左到右拼成长图 + 重生成 steps.md，版本号 +1 |
| 局部答案更新 | `backend/render.py` `merge_kb_answer()` | 用 `APP_GUIDE_BEGIN/END` 标记识别本路径负责的答案区块，标记外人工内容逐字保留 |
| 图片链接更新 | `backend/render.py` `merge_kb_image_urls()` | 只替换本路径长图，保留无关外链，不再把分步截图塞进 `image_urls` |
| 安全入库 | `backend/render.py` `publish_path()` | `question_wiki_id` 只用于定位已有条目；除该键外仅提交 `answer`、`image_urls`，不覆盖问题和型号 |

## 测试

```bash
backend/.venv/bin/python backend/scripts/test_render_rules.py  # 离线规则测试
backend/.venv/bin/python backend/scripts/smoke.py              # 端到端联调
```

离线测试覆盖横向/纵向长图、答案标记区局部替换、人工内容与无关图片保留、异常标记保护、推送字段白名单及目标条目不存在时停止写入。端到端冒烟覆盖建节点/连线/路径、局部换图、自动重渲染、Hub 两轮局部同步和清理。**当前结果：离线 11 项通过；端到端通过 51 项，失败 0 项。**

## 使用约定

- 素材池支持文件选择、拖放，以及在非输入框状态按 **Cmd / Ctrl + V** 直接粘贴截图。
- 长图默认横向；启动前设置环境变量 `APP_GUIDE_IMAGE_LAYOUT=vertical` 可恢复纵向（例如 `APP_GUIDE_IMAGE_LAYOUT=vertical ./start.sh`）。
- 推送只更新 Hub 中**已有** `question_wiki_id` 条目的 `answer` 与 `image_urls`。目标条目不存在时会停止，避免误建缺少问题和型号的空条目。

## 文档

完整技术方案（接口契约）与零基础使用说明在电子脑洞仓库：

- `/Volumes/ORICO/个人笔记记录/电子脑洞仓库/55_KnowBaseHub_8085/APP操作指引自动维护/20261112_APP操作指引工作台技术方案.md`
- `/Volumes/ORICO/个人笔记记录/电子脑洞仓库/55_KnowBaseHub_8085/APP操作指引自动维护/20261112_APP操作指引工作台使用说明与交付记录.md`

## 子项目说明

- 后端说明：[`backend/README.md`](backend/README.md)
- 前端说明：[`frontend/README.md`](frontend/README.md)
