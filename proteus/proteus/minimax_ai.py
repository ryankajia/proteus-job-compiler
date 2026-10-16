"""Optional MiniMax writing assistant. Never stores a key or overwrites finals."""
from __future__ import annotations

import json
import os
import datetime as dt
import urllib.error
import urllib.request
from pathlib import Path

MODEL = "MiniMax-M2.7"
PRIVATE_CONFIG = Path(__file__).resolve().parents[2] / "my-materials" / "config" / "minimax.env"
API_HOSTS = {"https://api.minimax.io", "https://api.minimaxi.com"}


def _setting(name: str) -> str:
    """Environment wins; read only literal values from the ignored local config."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:
        lines = PRIVATE_CONFIG.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise RuntimeError("无法读取本机 MiniMax 配置文件。") from exc
    for line in lines:
        key, separator, raw = line.partition("=")
        if separator and key.strip() == name:
            value = raw.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    return ""


def available() -> bool:
    return bool(_setting("MINIMAX_API_KEY"))


def _api_host() -> str:
    host = _setting("MINIMAX_API_HOST").rstrip("/") or "https://api.minimax.io"
    if host not in API_HOSTS:
        raise RuntimeError("MINIMAX_API_HOST 只能是 MiniMax 国际区或中国区官方 API 地址。")
    return host


def draft_selection(worksheet: str) -> str:
    key = _setting("MINIMAX_API_KEY")
    if not key:
        raise RuntimeError("请在 my-materials/config/minimax.env 中设置 MINIMAX_API_KEY。")
    payload = {
        "model": _setting("MINIMAX_MODEL") or MODEL,
        "messages": [
            {"role": "system", "content": (
                "You draft Australian selection criteria in English. Use only facts and evidence "
                "explicitly present in the supplied worksheet. Copy each criterion heading verbatim. "
                "Never invent achievements, metrics, frequency, seniority or qualifications. "
                "Where evidence is missing, write [Evidence needed] and a short question. "
                "Keep the response style and page or word limit stated in the worksheet. "
                "Return Markdown only. This is a draft for human review, not a submitted application.")},
            {"role": "user", "content": worksheet[:50000]},
        ],
        "max_completion_tokens": 7000,
    }
    request = urllib.request.Request(
        _api_host() + "/v1/chat/completions", json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError("MiniMax 鉴权失败（401）。请核对 MINIMAX_API_HOST 与订阅 Key 所属地区是否一致。") from exc
        raise RuntimeError(f"MiniMax 返回 HTTP {exc.code}。请检查 API key、额度和模型权限。") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("无法连接 MiniMax，请稍后重试。") from exc
    if data.get("base_resp", {}).get("status_code", 0) != 0:
        raise RuntimeError("MiniMax 未完成本次请求。请检查账户额度。")
    try:
        result = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise RuntimeError("MiniMax 返回了无法识别的内容。") from exc
    if not result:
        raise RuntimeError("MiniMax 没有返回可用文本。")
    return result


def search_jobs(query: str) -> str:
    """Search the live web; return cited prose for review, never auto-import jobs."""
    key = _setting("MINIMAX_API_KEY")
    if not key:
        raise RuntimeError("请在 my-materials/config/minimax.env 中设置 MINIMAX_API_KEY。")
    payload = {
        "model": "MiniMax-M3",
        "input": ("Search current Australian job vacancies for: " + query[:1000] +
                  ". Return at most 10 candidate roles with title, employer, a direct official job-detail URL, "
                  "closing date when shown, citizenship/visa/clearance requirement when shown. "
                  "Mark unknown fields unknown. Do not use aggregators or listing pages as evidence for an individual role. "
                  "Exclude roles whose closing date is already past, missing, or contradictory. "
                  "Never claim a role is verified; these are leads requiring manual official-page review. "
                  "Do not invent a vacancy or deadline. Today's date is " + dt.date.today().isoformat()),
        "tools": [{"type": "web_search"}],
    }
    request = urllib.request.Request(
        _api_host() + "/v1/responses", json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError("MiniMax 鉴权失败（401）。请核对 MINIMAX_API_HOST 与订阅 Key 所属地区是否一致。") from exc
        raise RuntimeError(f"MiniMax 搜索返回 HTTP {exc.code}。请检查额度和模型权限。") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("无法完成 MiniMax 搜索，请稍后重试。") from exc
    if data.get("status") != "completed":
        raise RuntimeError("MiniMax 搜索未完成。")
    output = (data.get("output_text") or "").strip()
    if not output:
        raise RuntimeError("本次搜索没有返回岗位。")
    citations = []
    for item in data.get("output", []):
        for block in item.get("content", []):
            for annotation in block.get("annotations", []):
                url = annotation.get("url", "")
                if url.startswith("https://") and url not in citations:
                    citations.append(url)
    return output + "\n\n## 搜索来源（请打开雇主原文核对）\n" + "\n".join(f"- {u}" for u in citations)
