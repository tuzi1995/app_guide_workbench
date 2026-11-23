"""工作台数据与预览回归测试；只使用临时目录，禁止访问知识库。"""
from __future__ import annotations

import copy
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
with patch.object(Path, "mkdir"):
    import config
import app as app_mod
import render
import storage
from PIL import Image


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="workbench-test-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        directories = {key: root / key for key in
                       ("DATA_DIR", "FLOW_DIR", "MEDIA_DIR", "EXPORT_DIR", "STATE_DIR")}
        for directory in directories.values():
            directory.mkdir()
        self.enterContext(patch.multiple(config, **directories))
        self.enterContext(patch.dict(app_mod.app.config, TESTING=True))
        self.enterContext(patch("requests.sessions.Session.request",
                                side_effect=AssertionError("禁止网络请求")))
        self.client = app_mod.app.test_client()
        self.graph = storage.empty_graph("test")
        self.graph["nodes"] = [{"id": "n1", "title": "步骤", "screenshot": "old.png"}]
        self.graph["paths"] = [{"id": "p1", "title": "路径", "node_ids": ["n1"],
                                "annotations": {"n1": [{"type": "rect", "x": .1, "y": .1,
                                                         "width": .3, "height": .2}]},
                                "render_version": 0, "stale": False}]
        for name, color in (("old.png", "white"), ("new.png", "blue")):
            Image.new("RGB", (40, 60), color).save(config.MEDIA_DIR / name)
        storage.save_graph(self.graph, "test")

    def url(self, path):
        return path + "?project_id=test"

    def test_library_replacement_invalidates_marks_and_tracks_screenshot_version(self):
        response = self.client.patch(self.url("/api/nodes/n1"), json={"screenshot": "new.png"})
        self.assertEqual(response.status_code, 200)
        graph = storage.load_graph("test")
        node, path = graph["nodes"][0], graph["paths"][0]
        self.assertEqual(node["screenshot_prev"], "old.png")
        self.assertEqual(node["screenshot_version"], 2)
        self.assertTrue(node["changed"])
        self.assertNotIn("n1", path["annotations"])
        self.assertIn("n1", path["annotation_invalidated_nodes"])

    def test_annotation_and_title_edits_mark_existing_output_stale(self):
        for patch_data in ({"annotations": {}}, {"title": "新路径标题"}):
            with self.subTest(patch_data=patch_data):
                storage.save_graph(copy.deepcopy(self.graph), "test")
                response = self.client.patch(self.url("/api/paths/p1"), json=patch_data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.get_json()["data"]["stale"])

    def test_published_long_image_survives_repeated_local_rendering(self):
        path = self.graph["paths"][0]
        first = render.render_path(self.graph, path)
        path["kb_managed_image_urls"] = [first["long_image_abs_url"]]
        path["kb_published_result"] = {"image_urls": [first["long_image_abs_url"]]}
        for _ in range(4):
            render.render_path(self.graph, path)
        self.assertTrue(Path(first["long_image_path"]).is_file(), "知识库仍引用的长图被清理了")

    def test_export_reads_saved_output_without_creating_or_deleting_versions(self):
        path = self.graph["paths"][0]
        result = render.render_path(self.graph, path)
        storage.save_graph(self.graph, "test")
        response = self.client.get(self.url("/api/paths/p1/export"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["version"], result["version"])
        self.assertEqual(storage.load_graph("test")["paths"][0]["render_version"], result["version"])
        self.assertEqual(len(list((config.EXPORT_DIR / "p1").iterdir())), 1)

    def test_publish_tracks_the_actual_version_and_old_pin_can_be_released(self):
        path = self.graph["paths"][0]
        client = Mock()
        client.get_item.return_value = {"answer": "人工原文", "image_urls": []}
        first = render.render_path(self.graph, path)
        with patch.object(render, "get_client", return_value=client):
            render.publish_path(self.graph, path, first)
        self.assertEqual(render.path_state(self.graph, path)["kb_published_version"], 1)
        for _ in range(4):
            latest = render.render_path(self.graph, path)
        self.assertEqual(render.path_state(self.graph, path)["kb_published_version"], 1)
        self.assertTrue(Path(first["long_image_path"]).exists())
        with patch.object(render, "get_client", return_value=client):
            render.publish_path(self.graph, path, latest)
        render.render_path(self.graph, path)
        self.assertFalse(Path(first["long_image_path"]).exists())
        self.assertTrue(Path(latest["long_image_path"]).exists())
        self.assertEqual(render.path_state(self.graph, path)["kb_published_version"], 5)

    def test_export_before_render_returns_clear_error_without_rendering(self):
        response = self.client.get(self.url("/api/paths/p1/export"))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(storage.load_graph("test")["paths"][0]["render_version"], 0)

    def test_in_use_material_cannot_be_deleted_even_from_another_project(self):
        response = self.client.delete("/api/media/old.png?project_id=other")
        self.assertEqual(response.status_code, 409)
        self.assertTrue((config.MEDIA_DIR / "old.png").exists())
        response = self.client.delete("/api/media/new.png?project_id=other")
        self.assertEqual(response.status_code, 200)

    def test_failed_publish_still_saves_the_new_render_version(self):
        with patch.object(render, "publish_path", side_effect=render.RenderError("目标条目不存在")):
            response = self.client.post(self.url("/api/paths/p1/publish"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(storage.load_graph("test")["paths"][0]["render_version"], 1)

    def test_concurrent_edits_do_not_overwrite_each_other(self):
        first_read = threading.Event()
        second_read = threading.Event()
        original_load = storage.load_graph
        responses = []

        def controlled_load(project_id):
            graph = original_load(project_id)
            if threading.current_thread().name == "first-edit":
                first_read.set()
                second_read.wait(.15)
            elif threading.current_thread().name == "second-edit":
                second_read.set()
            return graph

        def edit(payload):
            with app_mod.app.test_client() as client:
                responses.append(client.patch(self.url("/api/paths/p1"), json=payload).status_code)

        with patch.object(storage, "load_graph", side_effect=controlled_load):
            first = threading.Thread(target=edit, args=({"title": "修改后的标题"},), name="first-edit")
            second = threading.Thread(target=edit, args=({"question": "修改后的问题"},), name="second-edit")
            first.start()
            self.assertTrue(first_read.wait(2))
            second.start()
            first.join(3)
            second.join(3)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(responses, [200, 200])
        path = storage.load_graph("test")["paths"][0]
        self.assertEqual(path["title"], "修改后的标题")
        self.assertEqual(path["question"], "修改后的问题")


if __name__ == "__main__":
    unittest.main(verbosity=2)
