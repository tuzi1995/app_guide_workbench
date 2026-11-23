"""渲染与 KB 局部同步规则的离线单元测试（不需要启动服务或连接 Hub）。

运行：cd backend && .venv/bin/python scripts/test_render_rules.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import imaging  # noqa: E402
import render  # noqa: E402
from PIL import Image  # noqa: E402


def fake_result(version: int = 1) -> dict:
    return {
        "long_image_abs_url": f"http://127.0.0.1:8095/media/exports/p1/{version}/long.png",
        "long_image_url": f"/media/exports/p1/{version}/long.png",
        "steps": [
            {"index": 1, "title": "我的", "action": "点击我的", "image_url": "http://127.0.0.1:8095/media/a.png"},
            {"index": 2, "title": "登录页", "action": "点击登录", "image_url": "http://127.0.0.1:8095/media/b.png"},
        ],
        "version": version,
        "changed_step": None,
        "generated_at": "2026-11-13 11:00",
    }


class FakeClient:
    def __init__(self):
        self.payload = None
        self.existing = {
            "question_wiki_id": "kb-login",
            "question": "人工维护的问题",
            "answer": "这是人工维护的答案前言。",
            "image_urls": ["https://example.com/manual.png"],
            "product_name": "H5",
            "product_category_name": "扫地机",
        }

    def get_item(self, wiki_id):
        return self.existing

    def upsert_item(self, payload):
        self.payload = payload
        return {"success": True, "message": "updated"}


class LongImageTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.steps = []
        for index, color in enumerate(((230, 60, 60), (60, 190, 90), (60, 90, 220)), start=1):
            file = self.root / f"{index}.png"
            Image.new("RGB", (360, 720), color).save(file)
            self.steps.append({
                "title": f"步骤{index}", "action": f"执行第{index}步", "image_path": str(file),
            })

    def tearDown(self):
        self.tempdir.cleanup()

    def test_horizontal_is_default_and_places_cards_left_to_right(self):
        image = imaging.build_long_image(self.steps)
        expected_width = config.HORIZONTAL_CARD_WIDTH * 3 + config.HORIZONTAL_STEP_GAP * 2
        self.assertEqual(config.LONG_IMAGE_LAYOUT, "horizontal")
        self.assertEqual(image.width, expected_width)
        self.assertGreater(image.width, image.height)

    def test_mixed_resolution_uses_equal_width_without_downsampling_largest(self):
        files = [self.root / 'large.png', self.root / 'small.png']
        colors = [(213, 51, 71), (31, 173, 119)]
        for file, size, color in zip(files, [(1200, 2400), (600, 1200)], colors):
            Image.new('RGB', size, color).save(file)
        steps = [{'title': '步骤', 'action': '', 'image_path': str(f)} for f in files]
        image = imaging.build_long_image(steps, layout='horizontal')
        card_width = 1248
        self.assertEqual(image.width, card_width * 2 + config.HORIZONTAL_STEP_GAP)
        # 两张同宽、同起点、同高度，最大源图保留1200px；原文件不变。
        for offset, color in zip([0, card_width + config.HORIZONTAL_STEP_GAP], colors):
            self.assertEqual(image.getpixel((offset + 25, 200)), color)
            self.assertEqual(image.getpixel((offset + 1222, 200)), color)
            self.assertEqual(image.getpixel((offset + 600, 2400)), color)
        with Image.open(files[1]) as source:
            self.assertEqual(source.size, (600, 1200))

    def test_vertical_layout_remains_available(self):
        image = imaging.build_long_image(self.steps, layout="vertical")
        self.assertEqual(image.width, config.LONG_IMAGE_WIDTH)
        self.assertGreater(image.height, image.width)

    def test_unknown_layout_is_rejected(self):
        with self.assertRaises(ValueError):
            imaging.build_long_image(self.steps, layout="diagonal")


class AnswerMergeTests(unittest.TestCase):
    def setUp(self):
        self.path = {"id": "p1", "kb_wiki_id": "kb-login", "title": "登录操作指引"}
        self.block1 = render.build_kb_answer_block(self.path, fake_result(1))
        self.block2 = render.build_kb_answer_block(self.path, fake_result(2))

    def test_marked_replace_preserves_everything_outside_markers(self):
        prefix = "人工前言\n\n## 其他业务说明\n不要改动这里。\n\n"
        suffix = "\n\n人工尾注：仍然保留。"
        merged = render.merge_kb_answer(prefix + self.block1 + suffix, self.block2, self.path)
        self.assertTrue(merged.startswith(prefix))
        self.assertTrue(merged.endswith(suffix))
        self.assertIn("v2", merged)
        self.assertNotIn("v1", merged)
        self.assertIn("## 登录操作指引", merged)
        self.assertIn("### 操作步骤", merged)

    def test_first_publish_appends_and_second_publish_does_not_duplicate(self):
        manual = "## 人工维护内容\n这部分不是 APP 指引，不能覆盖。"
        first = render.merge_kb_answer(manual, self.block1, self.path)
        second = render.merge_kb_answer(first, self.block2, self.path)
        self.assertTrue(first.startswith(manual))
        self.assertEqual(second.count(manual), 1)
        self.assertEqual(second.count("<!-- APP_GUIDE_BEGIN:kb-login -->"), 1)
        self.assertEqual(second.count("## 登录操作指引"), 1)
        self.assertNotIn("v1", second)

    def test_legacy_full_guide_is_migrated_without_duplication(self):
        legacy = (
            "# 登录操作指引\n\n"
            "![长图](http://127.0.0.1:8095/media/exports/p1/17/long.png)\n\n"
            "## 操作步骤\n\n1. 我的\n\n## 变更记录\n\n- v17\n"
        )
        merged = render.merge_kb_answer(legacy, self.block2, self.path)
        self.assertNotIn("v17", merged)
        self.assertEqual(merged.count("## 登录操作指引"), 1)
        self.assertEqual(merged.count("<!-- APP_GUIDE_BEGIN:kb-login -->"), 1)

    def test_broken_markers_stop_instead_of_overwriting_manual_answer(self):
        with self.assertRaisesRegex(render.RenderError, "标记不完整或重复"):
            render.merge_kb_answer("人工内容\n<!-- APP_GUIDE_BEGIN:kb-login -->", self.block2, self.path)


class ImageMergeTests(unittest.TestCase):
    def test_only_new_long_image_replaces_old_managed_images(self):
        path = {
            "id": "p1", "kb_wiki_id": "kb-login",
            "kb_published_result": {"image_urls": [
                "http://127.0.0.1:8095/media/exports/p1/1/long.png",
                "http://127.0.0.1:8095/media/a.png",
                "http://127.0.0.1:8095/media/b.png",
            ]},
        }
        existing = [
            "http://127.0.0.1:8095/media/exports/p1/1/long.png",
            "http://127.0.0.1:8095/media/a.png",
            "https://example.com/manual.png",
            "http://127.0.0.1:8095/media/b.png",
        ]
        merged = render.merge_kb_image_urls(
            existing, "http://127.0.0.1:8095/media/exports/p1/2/long.png", path,
        )
        self.assertEqual(merged, [
            "http://127.0.0.1:8095/media/exports/p1/2/long.png",
            "https://example.com/manual.png",
        ])

    def test_unrelated_images_survive_later_publishes(self):
        path = {
            "id": "p1", "kb_wiki_id": "kb-login",
            "kb_managed_image_urls": ["http://127.0.0.1:8095/media/exports/p1/2/long.png"],
            "kb_published_result": {"image_urls": [
                "http://127.0.0.1:8095/media/exports/p1/2/long.png",
                "https://example.com/manual.png",
            ]},
        }
        merged = render.merge_kb_image_urls(
            ["http://127.0.0.1:8095/media/exports/p1/2/long.png", "https://example.com/manual.png"],
            "http://127.0.0.1:8095/media/exports/p1/3/long.png", path,
        )
        self.assertEqual(merged, [
            "http://127.0.0.1:8095/media/exports/p1/3/long.png",
            "https://example.com/manual.png",
        ])


class PublishPayloadTests(unittest.TestCase):
    def test_payload_contains_only_id_answer_and_image_urls(self):
        client = FakeClient()
        path = {"id": "p1", "kb_wiki_id": "kb-login", "title": "登录操作指引", "question": "不应覆盖"}
        with patch.object(render, "get_client", return_value=client):
            result = render.publish_path({}, path, fake_result(3))
        self.assertEqual(set(client.payload), {"question_wiki_id", "answer", "image_urls"})
        self.assertEqual(client.payload["question_wiki_id"], "kb-login")
        self.assertTrue(client.payload["answer"].startswith("这是人工维护的答案前言。"))
        self.assertEqual(client.payload["image_urls"], [
            "http://127.0.0.1:8095/media/exports/p1/3/long.png",
            "https://example.com/manual.png",
        ])
        self.assertNotIn("question", client.payload)
        self.assertNotIn("product_name", client.payload)
        self.assertEqual(result["updated_fields"], ["answer", "image_urls"])
        self.assertEqual(path["kb_managed_image_urls"], [
            "http://127.0.0.1:8095/media/exports/p1/3/long.png",
        ])

    def test_missing_kb_entry_stops_instead_of_creating_incomplete_row(self):
        client = FakeClient()
        client.existing = None
        path = {"id": "p1", "kb_wiki_id": "kb-login", "title": "登录操作指引"}
        with patch.object(render, "get_client", return_value=client):
            with self.assertRaisesRegex(render.RenderError, "未找到知识库条目 kb-login"):
                render.publish_path({}, path, fake_result(3))
        self.assertIsNone(client.payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
