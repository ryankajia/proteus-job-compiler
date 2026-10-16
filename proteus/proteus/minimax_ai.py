"""Optional MiniMax writing assistant. Never stores a key or overwrites finals."""
from __future__ import annotations

import json
import os
import datetime as dt
import urllib.error
import urllib.request

URL = "https://api.minimax.io/v1/chat/completions"
MODEL = "MiniMax-M2.7"


def available() -> bool:
    return bool(os.environ.get("MINIMAX_API_KEY"))


def draft_selection(worksheet: str) -> str:
    key = os.environ.get("MINIMAX_API_KEY")
    if not key:
        raise RuntimeError("请先在启动系统的终端设置 MINIMAX_API_KEY。")
    payload = {
        "model": os.environ.get("MINIMAX_MODEL", MODEL),
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
        URL, json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
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
    key = os.environ.get("MINIMAX_API_KEY")
    if not key:
        raise RuntimeError("请先在启动系统的终端设置 MINIMAX_API_KEY。")
    payload = {
        "model": "MiniMax-M3",
        "input": ("Search current Australian job vacancies for: " + query[:1000] +
                  ". Return at most 10 roles with title, employer, source URL, "
                  "closing date when shown, citizenship/visa/clearance requirement when shown. "
                  "Mark unknown fields unknown. Exclude roles whose closing date is already past. "
                  "Do not invent a vacancy or deadline. Today's date is " + dt.date.today().isoformat()),
        "tools": [{"type": "web_search"}],
    }
    request = urllib.request.Request(
        "https://api.minimax.io/v1/responses", json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
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
