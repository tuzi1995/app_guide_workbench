"""图像处理：像素差检测（判断页面是否改版）、长图拼接、Markdown 步骤文档渲染。

这三个能力对应需求的三个关键动作：
1. 替换节点截图 → 检测是否真的改版（imaging.diff_ratio）
2. 沿连线取出路径上的图 → 重新拼成一张长图（imaging.build_long_image）
3. 长图 + 步骤文字 → 同步更新文字步骤文档（imaging.render_markdown）
"""
from __future__ import annotations

import io
import math
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import config
import storage

Image.MAX_IMAGE_PIXELS = 200_000_000  # 允许大图但防止解压炸弹


# ---------------------------------------------------------------- 字体

_font_cache: dict[int, ImageFont.FreeTypeFont] = {}


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """按配置里的候选路径找第一个能用的中文字体，找不到退回 PIL 内置字体。"""
    if size in _font_cache:
        return _font_cache[size]
    for path in config.FONT_CANDIDATES:
        if Path(path).exists():
            try:
                font = ImageFont.truetype(path, size)
                _font_cache[size] = font
                return font
            except OSError:
                continue
    font = ImageFont.load_default()
    _font_cache[size] = font
    return font


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    if not text:
        return 0
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int) -> list[str]:
    """按像素宽度折行（中文按字断行，英文按空格优先）。"""
    text = (text or "").strip()
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for ch in text:
        if ch == "\n":
            lines.append(current)
            current = ""
            if len(lines) >= max_lines:
                break
            continue
        probe = current + ch
        if _text_width(draw, probe, font) <= max_width or not current:
            current = probe
        else:
            lines.append(current)
            current = ch
            if len(lines) >= max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) >= max_lines and lines:
        # 末行超长就加省略号
        last = lines[-1]
        if _text_width(draw, last + "…", font) > max_width:
            while last and _text_width(draw, last + "…", font) > max_width:
                last = last[:-1]
            lines[-1] = last + "…"
    return lines


# ---------------------------------------------------------------- 像素差检测

def diff_ratio(old_bytes: bytes, new_bytes: bytes,
               color_threshold: float = config.DIFF_COLOR_THRESHOLD) -> float:
    """返回两张图的差异像素占比（0~1）。

    做法（pixelmatch 的核心口径，简化版）：
    - 两图缩放到相同尺寸后逐像素比较
    - 用 YIQ 亮度差公式算感知色差，小于 color_threshold 视为「看不出区别」
    - 差异像素数 / 总像素数 = 差异占比
    """
    if not old_bytes or not new_bytes:
        return 1.0 if new_bytes else 0.0
    try:
        old = Image.open(io.BytesIO(old_bytes)).convert("RGB")
        new = Image.open(io.BytesIO(new_bytes)).convert("RGB")
    except OSError:
        # 图片坏了没法比较，视为完全改版，交给人工判断
        return 1.0

    size = (min(old.width, new.width), min(old.height, new.height))
    if size[0] <= 0 or size[1] <= 0:
        return 1.0
    old = old.resize(size, Image.LANCZOS)
    new = new.resize(size, Image.LANCZOS)

    a = np.asarray(old, dtype=np.float64)
    b = np.asarray(new, dtype=np.float64)

    # YIQ 亮度差（pixelmatch 的 delta 算法）
    y1 = np.dot(a[..., :3], [0.29889531, 0.58662247, 0.11448223])
    y2 = np.dot(b[..., :3], [0.29889531, 0.58662247, 0.11448223])
    i1 = np.dot(a[..., :3], [0.59597799, -0.27417610, -0.32180189])
    i2 = np.dot(b[..., :3], [0.59597799, -0.27417610, -0.32180189])
    q1 = np.dot(a[..., :3], [0.21147017, -0.52261711, 0.31114694])
    q2 = np.dot(b[..., :3], [0.21147017, -0.52261711, 0.31114694])

    delta = 0.5053 * np.square(y1 - y2) + 0.299 * np.square(i1 - i2) + 0.1957 * np.square(q1 - q2)
    # 归一化到 0~1 的色差距离
    max_delta = 35215.0
    distance = np.sqrt(delta) / (max_delta ** 0.5)
    # pixelmatch 用 0.1 阈值对应这里的大约 0.0153，这里允许直接传色差阈值
    threshold = max(0.0, color_threshold) * 0.153
    diff_mask = distance > threshold

    return float(np.count_nonzero(diff_mask)) / float(diff_mask.size)


def is_changed(ratio: float, threshold: float = config.DIFF_RATIO_THRESHOLD) -> bool:
    """差异占比超过阈值即判定为页面改版。"""
    return ratio > threshold


# ---------------------------------------------------------------- 图片读写

def load_image(path: Path) -> Image.Image:
    return Image.open(path).convert("RGB")


def decode_to_png(data: bytes) -> bytes:
    """把上传的任意图片统一转成 PNG 字节，保证后面拼接和 diff 的口径一致。"""
    img = Image.open(io.BytesIO(data))
    if img.mode in ("RGBA", "LA", "P"):
        # 透明底转白底，否则拼到白底长图上会出现黑块
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    else:
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------- 长图拼接

def _fit_width(img: Image.Image, target_width: int) -> Image.Image:
    """等比缩放到目标宽度，不裁切、不拉伸。"""
    if img.width == target_width:
        return img
    ratio = target_width / float(img.width)
    new_h = max(1, int(round(img.height * ratio)))
    return img.resize((target_width, new_h), Image.LANCZOS)


def _apply_annotations(img: Image.Image, annotations: Sequence[dict]) -> Image.Image:
    """绘制路径专属标注到副本，绝不修改素材池原图。"""
    if not annotations:
        return img
    out = img.copy()
    draw = ImageDraw.Draw(out)
    for item in annotations:
        kind = item.get("type")
        color = item.get("color", "#ff3030")
        width = max(1, int(item.get("strokeWidth", 4)))
        x = int(float(item.get("x", 0)) * out.width)
        y = int(float(item.get("y", 0)) * out.height)
        if kind == "rect":
            w = int(float(item.get("width", 0)) * out.width)
            h = int(float(item.get("height", 0)) * out.height)
            draw.rectangle((x, y, x + w, y + h), outline=color, width=width)
        elif kind == "arrow":
            x2 = int(float(item.get("x2", 0)) * out.width)
            y2 = int(float(item.get("y2", 0)) * out.height)
            draw.line((x, y, x2, y2), fill=color, width=width)
            angle = math.atan2(y2 - y, x2 - x)
            head = max(10, width * 4)
            p1 = (x2 - head * math.cos(angle - math.pi / 6), y2 - head * math.sin(angle - math.pi / 6))
            p2 = (x2 - head * math.cos(angle + math.pi / 6), y2 - head * math.sin(angle + math.pi / 6))
            draw.polygon([(x2, y2), p1, p2], fill=color)
        elif kind == "text":
            size = max(12, int(float(item.get("fontSize", 0.04)) * out.width))
            draw.text((x, y), str(item.get("text", "")), fill=color, font=_load_font(size))
    return out


def _step_block(index: int, title: str, action: str, img: Image.Image,
                width: int, draw: ImageDraw.ImageDraw) -> tuple[Image.Image, int]:
    """渲染一个步骤块：序号角标 + 标题 + 操作文字 + 截图。返回图像和总高度。"""
    pad = 24
    title_font = _load_font(config.FONT_SIZE_TITLE)
    action_font = _load_font(config.FONT_SIZE_ACTION)
    inner_w = width - pad * 2

    title_lines = _wrap_text(draw, title, title_font, inner_w - config.BADGE_SIZE - 16, 2)
    action_lines = _wrap_text(draw, action, action_font, inner_w, 3) if action else []

    title_h = len(title_lines) * (config.FONT_SIZE_TITLE + 8)
    action_h = len(action_lines) * (config.FONT_SIZE_ACTION + 8) if action_lines else 0
    img_h = img.height

    block_h = pad + max(title_h, config.BADGE_SIZE) + 12 + action_h + 12 + img_h + pad
    block = Image.new("RGB", (width, block_h), (255, 255, 255))
    bdraw = ImageDraw.Draw(block)

    y = pad
    # 序号角标（实心圆 + 白字）
    cx = pad + config.BADGE_SIZE // 2
    cy = y + config.BADGE_SIZE // 2
    bdraw.ellipse([cx - config.BADGE_SIZE // 2, cy - config.BADGE_SIZE // 2,
                   cx + config.BADGE_SIZE // 2, cy + config.BADGE_SIZE // 2],
                  fill=(43, 108, 255))
    badge_font = _load_font(int(config.FONT_SIZE_TITLE * 0.9))
    badge_text = str(index)
    bw = _text_width(bdraw, badge_text, badge_font)
    bdraw.text((cx - bw / 2, cy - config.FONT_SIZE_TITLE * 0.58), badge_text,
               font=badge_font, fill=(255, 255, 255))

    # 标题
    ty = y + max(0, (config.BADGE_SIZE - title_h) // 2)
    for line in title_lines:
        bdraw.text((pad + config.BADGE_SIZE + 16, ty), line, font=title_font, fill=(24, 28, 36))
        ty += config.FONT_SIZE_TITLE + 8

    y = pad + max(title_h, config.BADGE_SIZE) + 12
    if action_lines:
        for line in action_lines:
            bdraw.text((pad, y), line, font=action_font, fill=(90, 96, 110))
            y += config.FONT_SIZE_ACTION + 8
        y += 4

    # 截图
    block.paste(img, (pad, y))
    bdraw.rectangle([pad, y, pad + img.width - 1, y + img.height - 1],
                    outline=(228, 231, 237), width=1)
    return block, block_h


def build_long_image(steps: Sequence[dict], width: int = config.LONG_IMAGE_WIDTH,
                     layout: str = config.LONG_IMAGE_LAYOUT) -> Image.Image:
    """把路径上的多个步骤拼成一张长图，默认按操作顺序从左到右横向排列。

    steps: [{'title':..., 'action':..., 'image_path': Path}, ...]
    layout: ``horizontal`` 为横向步骤卡片，``vertical`` 保留纵向拼接能力。
    所有截图只缩放、不裁切；每个步骤的文字始终和自己的截图放在同一卡片里。
    """
    usable = [s for s in steps if s.get("image_path") and Path(s["image_path"]).exists()]
    if not usable:
        # 没有图也产出一张占位长图，保证文档里的引用不悬空
        canvas = Image.new("RGB", (width, 240), (247, 248, 250))
        d = ImageDraw.Draw(canvas)
        font = _load_font(config.FONT_SIZE_ACTION)
        d.text((width // 2 - 120, 100), "该路径暂无截图", font=font, fill=(140, 146, 160))
        return canvas

    if layout not in {"horizontal", "vertical"}:
        raise ValueError(f"不支持的长图拼接方向：{layout}")

    if layout == "horizontal":
        card_width = config.HORIZONTAL_CARD_WIDTH
        gap = config.HORIZONTAL_STEP_GAP
        blocks: list[Image.Image] = []
        # 路径内统一截图宽度，以最宽源图为基准，避免一大一小；
        # 不向下压缩高清截图，小图等比放大但不会凭空增加细节。
        source_images = [load_image(Path(step["image_path"])) for step in usable]
        actual_width = max(card_width, max(img.width for img in source_images) + 48)
        for i, (step, source_img) in enumerate(zip(usable, source_images), start=1):
            source_img = _apply_annotations(source_img, step.get("annotations") or [])
            img = _fit_width(source_img, actual_width - 48)
            probe = ImageDraw.Draw(Image.new("RGB", (actual_width, 10)))
            block, _ = _step_block(
                i, step.get("title", ""), step.get("action", ""), img, actual_width, probe,
            )
            blocks.append(block)

        total_w = sum(b.width for b in blocks) + gap * (len(blocks) - 1)
        total_h = max(b.height for b in blocks)
        canvas = Image.new("RGB", (total_w, total_h), (247, 248, 250))
        x = 0
        for i, block in enumerate(blocks):
            canvas.paste(block, (x, 0))
            x += block.width
            if i < len(blocks) - 1:
                # 卡片之间画一条不压住截图的右箭头，让阅读方向一目了然。
                d = ImageDraw.Draw(canvas)
                cy = total_h // 2
                start_x, end_x = x + 10, x + gap - 10
                d.line((start_x, cy, end_x, cy), fill=(43, 108, 255), width=4)
                d.polygon([(end_x, cy), (end_x - 11, cy - 8), (end_x - 11, cy + 8)],
                          fill=(43, 108, 255))
                x += gap
        return canvas

    blocks = []
    for i, step in enumerate(usable, start=1):
        img = _fit_width(_apply_annotations(load_image(Path(step["image_path"])), step.get("annotations") or []), width - 48)
        probe = ImageDraw.Draw(Image.new("RGB", (width, 10)))
        block, _ = _step_block(i, step.get("title", ""), step.get("action", ""), img, width, probe)
        blocks.append(block)

    total_h = sum(b.height for b in blocks) + config.STEP_GAP * (len(blocks) - 1)
    canvas = Image.new("RGB", (width, total_h), (255, 255, 255))
    y = 0
    for i, block in enumerate(blocks):
        canvas.paste(block, (0, y))
        y += block.height
        if i < len(blocks) - 1:
            d = ImageDraw.Draw(canvas)
            d.line([(0, y + config.STEP_GAP // 2), (width, y + config.STEP_GAP // 2)],
                   fill=(235, 237, 241), width=config.STEP_DIVIDER)
            y += config.STEP_GAP
    return canvas


# ---------------------------------------------------------------- Markdown 渲染

def render_markdown(path: dict, steps: list[dict], long_image_url: str,
                    version: int = 1, changed_step: int | None = None,
                    generated_at: str | None = None, heading_level: int = 1) -> str:
    """生成文字步骤文档（Markdown）。

    path:  路径定义（title/description/question...）
    steps: [{'index':1,'title':..,'action':..,'image_url':..,'changed':bool}, ...]
    heading_level: 独立导出用 1；嵌入 KB 答案的局部区块用 2，避免抢走整篇答案的主标题。
    """
    heading_level = min(5, max(1, int(heading_level)))
    heading = "#" * heading_level
    subheading = heading + "#"
    generated_at = generated_at or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = []
    lines.append(f"{heading} {path.get('title') or '操作指引'}")
    lines.append("")
    if path.get("description"):
        lines.append(f"> {path['description']}")
        lines.append("")

    lines.append("| 项目 | 内容 |")
    lines.append("|---|---|")
    lines.append(f"| 步骤数 | {len(steps)} |")
    lines.append(f"| 版本 | v{version} |")
    lines.append(f"| 生成时间 | {generated_at} |")
    lines.append("")

    if long_image_url:
        lines.append(f"![{path.get('title') or '操作步骤'}长图]({long_image_url})")
        lines.append("")

    lines.append(f"{subheading} 操作步骤")
    lines.append("")
    for step in steps:
        lines.append(f"{step['index']}. **{step.get('title') or ''}**")
        if step.get("action"):
            lines.append(f"   {step['action']}")
        if step.get("image_url"):
            lines.append(f"   - 截图：`{step['image_url']}`")
        lines.append("")

    lines.append(f"{subheading} 变更记录")
    lines.append("")
    if changed_step:
        lines.append(f"- v{version} · {generated_at} · 第 {changed_step} 步截图更新")
    else:
        lines.append(f"- v{version} · {generated_at} · 首次生成")
    lines.append("")
    return "\n".join(lines)


def save_long_image(img: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG", optimize=True)
    return path
