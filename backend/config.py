"""全局配置：路径、端口、阈值、KnowBase Hub 连接参数。

所有可调参数集中在这里，方便一处修改全局生效。
"""
from __future__ import annotations

import os
from pathlib import Path

# ---- 目录 ----
BASE_DIR = Path(__file__).resolve().parent.parent          # 项目根目录
BACKEND_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
FLOW_DIR = DATA_DIR / "flows"                              # 事实源：各项目的图文档 JSON
MEDIA_DIR = DATA_DIR / "media"                             # 截图库（内容寻址）
EXPORT_DIR = DATA_DIR / "exports"                          # 渲染产物：长图 + Markdown
STATE_DIR = DATA_DIR / "state"                             # 运行状态（如 Hub 会话缓存）

DEFAULT_PROJECT_ID = "app_guide"

# ---- 服务 ----
HOST = os.environ.get("APP_GUIDE_HOST", "127.0.0.1")
PORT = int(os.environ.get("APP_GUIDE_PORT", "8095"))

# 对外可访问的基础地址：写进知识库 image_urls 的图片链接用它拼。
# Hub 只存字符串，不校验可达性；但本机浏览器要能打开，所以默认用本机地址。
PUBLIC_BASE_URL = os.environ.get("APP_GUIDE_PUBLIC_BASE_URL", f"http://127.0.0.1:{PORT}")

# ---- KnowBase Hub ----
HUB_URL = os.environ.get("KNOWBASE_HUB_URL", "http://127.0.0.1:8085")
HUB_USERNAME = os.environ.get("KNOWBASE_HUB_USERNAME", "admin")
HUB_PASSWORD = os.environ.get("KNOWBASE_HUB_PASSWORD", "123456")
HUB_TIMEOUT = 20

# ---- 图像处理阈值 ----
# 像素差容差（pixelmatch 口径）：单像素色差小于该值视为相同
DIFF_COLOR_THRESHOLD = 0.1
# 差异像素占比超过该值判定为「页面改版」
DIFF_RATIO_THRESHOLD = 0.05
# 长图统一宽度（像素，纵向拼接时使用）
LONG_IMAGE_WIDTH = 1080
# 默认横向拼接：每个步骤一张卡片，从左到右排列；也可设为 vertical
LONG_IMAGE_LAYOUT = os.environ.get("APP_GUIDE_IMAGE_LAYOUT", "horizontal")
# 横向拼接时每张步骤卡片的宽度与卡片间距
HORIZONTAL_CARD_WIDTH = 480
HORIZONTAL_STEP_GAP = 48
# 缩略图/节点用图宽度
NODE_IMAGE_WIDTH = 480
# 长图每一步的留白与分隔
STEP_GAP = 12
STEP_DIVIDER = 1
# 步骤圆角角标直径
BADGE_SIZE = 56

# CJK 字体候选路径（macOS 自带，按顺序探测第一个可用的）
FONT_CANDIDATES = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]

FONT_SIZE_TITLE = 34          # 步骤标题
FONT_SIZE_ACTION = 26         # 操作说明
FONT_SIZE_META = 22           # 元信息

# 允许上传的图片类型
ALLOWED_UPLOAD_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

for _d in (DATA_DIR, FLOW_DIR, MEDIA_DIR, EXPORT_DIR, STATE_DIR):
    _d.mkdir(parents=True, exist_ok=True)
