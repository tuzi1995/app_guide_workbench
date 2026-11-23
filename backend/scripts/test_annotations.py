"""标注绘制、路径隔离及截图替换规则的离线 unittest。

运行：python3 -B backend/scripts/test_annotations.py
仅使用临时图片、内存图文档和 Flask test client，不读写真实 data，不连接 Hub。
"""
from __future__ import annotations

import copy
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# config 导入时会 mkdir 真实 data；测试不允许触发这一副作用。
with patch.object(Path, "mkdir"):
    import config

import app as app_mod  # noqa: E402
import imaging  # noqa: E402
import render  # noqa: E402
import storage  # noqa: E402
from PIL import Image, ImageChops, ImageFont  # noqa: E402


RED = (255, 48, 48)
WHITE = (255, 255, 255)
RECT = {"type": "rect", "x": 0.1, "y": 0.1,
        "width": 0.3, "height": 0.2, "strokeWidth": 4}
ARROW = {"type": "arrow", "x": 0.1, "y": 0.5,
         "x2": 0.8, "y2": 0.5, "strokeWidth": 4}
TEXT = {"type": "text", "x": 0.1, "y": 0.7,
        "text": "Click here", "fontSize": 0.08}


class AnnotationDrawingTests(unittest.TestCase):
    def setUp(self):
        self.source = Image.new("RGB", (200, 200), WHITE)
        self.before = self.source.tobytes()

    def apply(self, annotation):
        result = imaging._apply_annotations(self.source, [annotation])
        self.assertIsNot(result, self.source)
        self.assertEqual(result.size, self.source.size)
        self.assertEqual(self.source.tobytes(), self.before)
        return result

    def test_red_rectangle_has_outline_and_no_fill(self):
        result = self.apply(RECT)
        for point in ((20, 20), (50, 20), (20, 40), (80, 60)):
            self.assertEqual(result.getpixel(point), RED)
        self.assertEqual(result.getpixel((50, 40)), WHITE)
        self.assertEqual(result.getpixel((10, 10)), WHITE)

    def test_red_arrow_has_shaft_tip_and_head(self):
        result = self.apply(ARROW)
        for point in ((20, 100), (90, 100), (160, 100), (149, 105)):
            self.assertEqual(result.getpixel(point), RED)
        self.assertEqual(result.getpixel((90, 110)), WHITE)

    def test_red_text_draws_visible_glyphs_only_near_its_position(self):
        # 固定使用 Pillow 字体，避免测试依赖操作系统中文字体。
        with patch.object(imaging, "_load_font", return_value=ImageFont.load_default()):
            result = self.apply(TEXT)
        delta = ImageChops.difference(result, self.source)
        bbox = delta.getbbox()
        self.assertIsNotNone(bbox)
        self.assertGreaterEqual(bbox[0], 20)
        self.assertGreaterEqual(bbox[1], 140)
        self.assertLess(bbox[2], 160)
        self.assertLess(bbox[3], 180)
        self.assertTrue(any(r > g and r > b for r, g, b in result.crop(bbox).getdata()))

    def test_empty_annotations_leave_pixels_unchanged(self):
        result = imaging._apply_annotations(self.source, [])
        self.assertEqual(result.tobytes(), self.before)
        self.assertEqual(self.source.tobytes(), self.before)


class AnnotationApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="annotations-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        directories = {name: self.root / name.lower() for name in
                       ("DATA_DIR", "FLOW_DIR", "MEDIA_DIR", "EXPORT_DIR", "STATE_DIR")}
        for directory in directories.values():
            directory.mkdir()
        self.enterContext(patch.multiple(config, **directories))
        self.enterContext(patch.dict(app_mod.app.config, TESTING=True))
        self.client = app_mod.app.test_client()
        self.graph = {
            "project_id": "annotation-test", "title": "Offline test", "updated_at": "test",
            "nodes": [{"id": nid, "title": nid, "action": "", "screenshot": f"{nid}.png",
                       "screenshot_version": 1} for nid in ("n1", "n2")],
            "edges": [{"id": "e1", "source": "n1", "target": "n2"}],
            "paths": [
                {"id": "p1", "title": "One", "node_ids": ["n1", "n2"],
                 "annotations": {"n1": [copy.deepcopy(RECT)], "n2": [copy.deepcopy(TEXT)]},
                 "annotation_invalidated_nodes": ["n2"], "annotations_stale": False},
                {"id": "p2", "title": "Two", "node_ids": ["n1"],
                 "annotations": {"n1": [copy.deepcopy(ARROW)]},
                 "annotation_invalidated_nodes": [], "annotations_stale": False},
                {"id": "p3", "title": "Other", "node_ids": ["n2"],
                 "annotations": {"n2": [copy.deepcopy(RECT)]},
                 "annotation_invalidated_nodes": [], "annotations_stale": False},
            ],
        }
        self.original_files = {}
        for nid in ("n1", "n2"):
            file = config.MEDIA_DIR / f"{nid}.png"
            Image.new("RGB", (200, 200), WHITE).save(file)
            self.original_files[nid] = file.read_bytes()
        self.load_graph = self.enterContext(patch.object(
            storage, "load_graph", side_effect=lambda _pid: copy.deepcopy(self.graph)))
        self.save_graph = self.enterContext(patch.object(
            storage, "save_graph", side_effect=self._save_graph))
        self.enterContext(patch.object(storage, "media_path",
                                      side_effect=lambda filename: config.MEDIA_DIR / filename))
        self.save_media = self.enterContext(patch.object(
            storage, "save_media", side_effect=self._save_media))
        # 即使未来代码误走发布分支，也必须立即失败，不能发出网络请求。
        self.hub = self.enterContext(patch("requests.sessions.Session.request",
                                           side_effect=AssertionError("离线测试禁止网络请求")))
        self.enterContext(patch.object(app_mod, "get_client",
                                      side_effect=AssertionError("离线测试禁止 Hub")))
        self.enterContext(patch.object(render, "get_client",
                                      side_effect=AssertionError("离线测试禁止 Hub")))

    def _save_graph(self, graph, project_id):
        self.assertEqual(project_id, "annotation-test")
        self.graph = copy.deepcopy(graph)

    def _save_media(self, data, extension):
        self.assertEqual(extension, ".png")
        filename = f"upload-{self.save_media.call_count}.png"
        (config.MEDIA_DIR / filename).write_bytes(data)
        return {"filename": filename, "url": f"/media/{filename}"}

    def path(self, path_id):
        return next(path for path in self.graph["paths"] if path["id"] == path_id)

    def upload(self, data=None):
        if data is None:
            buffer = io.BytesIO()
            Image.new("RGB", (200, 200), (20, 80, 180)).save(buffer, "PNG")
            data = buffer.getvalue()
        response = self.client.post(
            "/api/nodes/n1/screenshot?project_id=annotation-test",
            data={"file": (io.BytesIO(data), "replacement.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()["success"])
        return response.get_json()["data"]

    def assert_original_files_unchanged(self):
        for nid, original in self.original_files.items():
            self.assertEqual((config.MEDIA_DIR / f"{nid}.png").read_bytes(), original)
        self.hub.assert_not_called()

    def test_patch_and_render_keep_annotations_scoped_to_each_path(self):
        other = copy.deepcopy(self.path("p2"))
        annotations = {"n1": [copy.deepcopy(TEXT)]}
        response = self.client.patch(
            "/api/paths/p1?project_id=annotation-test", json={"annotations": annotations})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["annotations"], annotations)
        self.assertEqual(self.path("p2"), other)
        rendered = {}
        for path_id in ("p1", "p2"):
            response = self.client.post(f"/api/paths/{path_id}/render?project_id=annotation-test")
            self.assertEqual(response.status_code, 200, response.get_json())
            result = response.get_json()["data"]
            self.assertEqual(result["steps"][0]["annotations"], self.path(path_id)["annotations"]["n1"])
            self.assertEqual(result["steps"][0]["screenshot"], "n1.png")
            with Image.open(result["long_image_path"]) as image:
                rendered[path_id] = image.copy()
        # p1 第二步没有标注，不能继承其它路径的配置。
        self.assertEqual(render.resolve_steps(self.graph, self.path("p1"))[1]["annotations"], [])
        self.assertNotEqual(rendered["p1"].tobytes(), rendered["p2"].tobytes())
        self.assert_original_files_unchanged()

    def test_same_source_renders_different_pixels_for_different_paths(self):
        # 同一截图、相同步骤信息，仅标注不同，长图必须不同。
        first = copy.deepcopy(self.path("p2"))
        second = copy.deepcopy(first)
        second["annotations"] = {"n1": [copy.deepcopy(RECT)]}
        first_image = imaging.build_long_image(render.resolve_steps(self.graph, first))
        second_image = imaging.build_long_image(render.resolve_steps(self.graph, second))
        self.assertEqual(first_image.size, second_image.size)
        self.assertIsNotNone(ImageChops.difference(first_image, second_image).getbbox())
        self.assert_original_files_unchanged()

    def test_replacement_clears_only_target_node_and_preserves_other_paths(self):
        unaffected = copy.deepcopy(self.path("p3"))
        retained = copy.deepcopy(self.path("p1")["annotations"]["n2"])
        other_node = copy.deepcopy(self.graph["nodes"][1])
        result = self.upload()
        self.assertEqual(result["cleared_annotation_paths"], ["p1", "p2"])
        self.assertEqual({item["id"] for item in result["affected_paths"]}, {"p1", "p2"})
        self.assertTrue(all(item["rendered"] for item in result["affected_paths"]))
        for path_id in ("p1", "p2"):
            path = self.path(path_id)
            self.assertNotIn("n1", path["annotations"])
            self.assertIn("n1", path["annotation_invalidated_nodes"])
            self.assertTrue(path["annotations_stale"])
            steps = render.resolve_steps(self.graph, path)
            self.assertEqual(steps[0]["annotations"], [])
        self.assertEqual(self.path("p1")["annotations"]["n2"], retained)
        self.assertEqual(self.path("p1")["annotation_invalidated_nodes"], ["n2", "n1"])
        self.assertEqual(self.path("p3"), unaffected)
        self.assertEqual(self.graph["nodes"][1], other_node)
        self.assertEqual(result["node"]["screenshot_prev"], "n1.png")
        self.assertEqual(result["version"], 2)
        self.assertTrue(result["changed"])
        self.assert_original_files_unchanged()

    def test_identical_repeated_upload_clears_annotations_without_duplicate_invalidations(self):
        original = self.original_files["n1"]
        first = self.upload(original)
        second = self.upload(original)
        self.assertFalse(first["changed"])
        self.assertFalse(second["changed"])
        self.assertEqual(first["cleared_annotation_paths"], ["p1", "p2"])
        self.assertEqual(second["cleared_annotation_paths"], [])
        for path_id in ("p1", "p2"):
            self.assertEqual(self.path(path_id)["annotation_invalidated_nodes"].count("n1"), 1)
        self.assert_original_files_unchanged()

    def test_empty_or_legacy_annotations_are_invalidated_but_not_reported_as_cleared(self):
        self.path("p1")["annotations"]["n1"] = []
        self.path("p2")["annotations"] = None
        self.path("p2")["annotation_invalidated_nodes"] = None
        result = self.upload()
        self.assertEqual(result["cleared_annotation_paths"], [])
        for path_id in ("p1", "p2"):
            self.assertNotIn("n1", self.path(path_id)["annotations"])
            self.assertTrue(self.path(path_id)["annotations_stale"])
            self.assertIn("n1", self.path(path_id)["annotation_invalidated_nodes"])
        self.assert_original_files_unchanged()


if __name__ == "__main__":
    unittest.main(verbosity=2)
