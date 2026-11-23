"""KnowBase Hub 客户端。

Hub 侧关键事实（已实测确认）：
- 所有知识库接口需要登录（POST /login 种 session cookie），未登录返回 401。
- POST /api/kb/item 的 body 只接受白名单字段：
  question_wiki_id, question_type, question, answer, answer_type, if_bm25,
  similar_questions, error_list, keyword_list, image_urls, video_urls,
  file_urls, link_type, link_url, product_category_name, product_name,
  review_status
- upsert 以 question_wiki_id 为幂等键：同一个 id 重复推送 = 更新，条目数不增加。
- product_name 非空时会做型号库校验，非法型号直接 400（错误文案是中文的）。
- Hub 没有图片上传接口，image_urls 只存外链 URL 字符串，所以长图必须由本工作台自己用 http URL 提供。
"""
from __future__ import annotations

from typing import Any

import requests

import config


class HubError(RuntimeError):
    """Hub 返回了错误。把 Hub 的中文原文带出去，前端直接显示给用户。"""

    def __init__(self, message: str, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.payload = payload


class KnowBaseHubClient:
    def __init__(self, base_url: str | None = None, username: str | None = None,
                 password: str | None = None):
        self.base_url = (base_url or config.HUB_URL).rstrip("/")
        self.username = username or config.HUB_USERNAME
        self.password = password or config.HUB_PASSWORD
        self._session = requests.Session()
        self._logged_in = False

    # ------------------------------------------------------------ 登录

    def login(self) -> bool:
        """登录 Hub。成功返回 True。"""
        try:
            resp = self._session.post(
                f"{self.base_url}/login",
                json={"username": self.username, "password": self.password},
                timeout=config.HUB_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise HubError(f"连不上知识库服务（{self.base_url}）：{exc}") from exc
        if resp.status_code != 200:
            raise HubError(f"登录失败（HTTP {resp.status_code}）", resp.status_code, resp.text)
        data = resp.json()
        if not data.get("success"):
            raise HubError(data.get("message") or "登录失败：账号或密码不正确", resp.status_code, data)
        self._logged_in = True
        return True

    def status(self) -> dict:
        """查询 Hub 登录状态。"""
        try:
            resp = self._session.get(f"{self.base_url}/api/status", timeout=config.HUB_TIMEOUT)
        except requests.RequestException as exc:
            raise HubError(f"连不上知识库服务（{self.base_url}）：{exc}") from exc
        try:
            data = resp.json()
        except ValueError:
            raise HubError("知识库服务返回了非 JSON 内容", resp.status_code, resp.text)
        if data.get("logged_in"):
            self._logged_in = True
        return data

    def ensure_login(self) -> None:
        """确保处于登录态，没登录就登录一次。"""
        if self._logged_in:
            return
        try:
            if self.status().get("logged_in"):
                self._logged_in = True
                return
        except HubError:
            pass
        self.login()

    # ------------------------------------------------------------ 型号库

    def product_catalog(self) -> dict[str, list[str]]:
        """取型号库：{品类: [型号, ...]}。给前端做「产品型号」下拉框。"""
        self.ensure_login()
        resp = self._request("GET", "/api/kb/product_catalog")
        data = resp.json()
        if not isinstance(data, dict):
            raise HubError("型号库返回格式异常", resp.status_code, data)
        return {k: list(v) for k, v in data.items() if isinstance(v, list)}

    # ------------------------------------------------------------ 知识库条目

    def upsert_item(self, payload: dict) -> dict:
        """写入/更新一条知识库条目。question_wiki_id 相同即为更新。"""
        self.ensure_login()
        resp = self._request("POST", "/api/kb/item", json=payload)
        data = resp.json()
        if isinstance(data, dict) and data.get("success") is False:
            raise HubError(data.get("message") or "知识库写入失败", resp.status_code, data)
        return data

    def get_item(self, question_wiki_id: str) -> dict | None:
        """按精确 id 查一条知识库条目，直接返回行数据，不存在返回 None。

        使用 /api/kb/data?ids=... 而不是 /api/kb/item：后者经过正式知识库视图，
        没有型号映射的条目会被过滤掉；局部合并必须读到物理表中的原答案和图片。
        """
        self.ensure_login()
        resp = self._request(
            "GET", "/api/kb/data",
            params={"ids": question_wiki_id, "page": 1, "pageSize": 1},
        )
        payload = resp.json()
        if isinstance(payload, dict) and payload.get("success") is False:
            raise HubError(payload.get("message") or "知识库条目读取失败", resp.status_code, payload)
        rows = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(rows, list) or not rows:
            return None
        row = rows[0]
        return row if isinstance(row, dict) else None

    def delete_item(self, question_wiki_id: str, change_source: str = "APP操作指引工作台") -> dict:
        """删除一条知识库条目（Hub 侧是软删除：置 review_status='deleting'）。"""
        self.ensure_login()
        resp = self._request(
            "POST",
            "/api/kb/delete",
            json={"ids": [question_wiki_id], "change_source": change_source},
        )
        return resp.json()

    # ------------------------------------------------------------ 内部

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        try:
            resp = self._session.request(method, url, timeout=config.HUB_TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            raise HubError(f"调用知识库接口失败（{method} {path}）：{exc}") from exc
        if resp.status_code == 401:
            # 会话过期，重新登录再试一次
            self._logged_in = False
            self.login()
            resp = self._session.request(method, url, timeout=config.HUB_TIMEOUT, **kwargs)
        if resp.status_code >= 400:
            raise HubError(self._extract_message(resp), resp.status_code, resp.text)
        return resp

    @staticmethod
    def _extract_message(resp: requests.Response) -> str:
        """从错误响应里挖出中文错误原文（Hub 的错误都是中文的，直接透出最有用）。"""
        try:
            data = resp.json()
        except ValueError:
            return f"知识库接口返回 HTTP {resp.status_code}：{resp.text[:300]}"
        if isinstance(data, dict):
            msg = data.get("message") or data.get("error") or data.get("msg")
            if msg:
                return str(msg)
        return f"知识库接口返回 HTTP {resp.status_code}"


_client: KnowBaseHubClient | None = None


def get_client() -> KnowBaseHubClient:
    """进程内共用一个客户端（session cookie 复用，避免每次推送都重新登录）。"""
    global _client
    if _client is None:
        _client = KnowBaseHubClient()
    return _client
