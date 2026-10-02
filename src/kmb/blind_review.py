"""Readable, self-contained human review cards with no automatic verdicts."""
from __future__ import annotations

import html
import json
from pathlib import Path


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _json(value) -> str:
    return _esc(json.dumps(value, ensure_ascii=False, indent=2))


def render_blind_cards(document: dict, output: Path) -> Path:
    cards = []
    for number, item in enumerate(document["items"], 1):
        criteria = "".join(
            f"<tr><td>{_esc(c['id'])}</td><td>{_esc(c['description'])}</td>"
            f"<td><pre>{_json({'kind': c['kind'], 'path': c.get('path'), 'expected': c.get('expected')})}</pre></td></tr>"
            for c in item["criteria"])
        files = "".join(f"<details open><summary>{_esc(path)}</summary><pre>{_esc(text)}</pre></details>"
                        for path, text in item["files_after"].items() if not path.startswith(".git/"))
        timeline = []
        for event in item["events"]:
            data = event.get("data", {})
            if event["kind"] == "model_request":
                messages = data.get("request", {}).get("messages", [])
                if messages and messages[-1].get("role") == "user":
                    timeline.append(f"<li><b>输入</b><pre>{_json(messages[-1].get('content'))}</pre></li>")
            elif event["kind"] == "native_session":
                parsed = data.get("parsed", {})
                outputs = ([r for r in parsed if r.get("type") in {"text", "tool_use", "tool_result", "result", "kmb_settle"}]
                           if isinstance(parsed, list) else parsed.get("payloads", []))
                timeline.append(f"<li><b>会话 {_esc(data.get('input_session_label', ''))}</b>"
                                f"<pre>{_json(outputs)}</pre></li>")
        cards.append(f"""<article id="item-{number}"><h2>第 {number} 项</h2>
<p class="muted">口述时说明本页编号与判据编号。记录标识：{_esc(item['item_id'])}</p>
<p>证据来源：{_esc(item['source'])} · 执行状态：{_esc(item['status'])}</p>
<table><thead><tr><th>判据编号</th><th>要求</th><th>检查对象</th></tr></thead><tbody>{criteria}</tbody></table>
<p class="notice">请对每项独立给出“通过／失败／无法判定”，并说明依据。口头声称完成不等于文件完成；看不到内部记忆不等于它不存在。本页没有预填判断。</p>
<h3>对话与执行过程</h3><ol>{''.join(timeline) or '<li>没有可摘要的原生记录，请检查下方完整证据。</li>'}</ol>
<h3>交付文件</h3>{files or '<p>未采集到文本文件。</p>'}
<details><summary>目录清单与完整性</summary><pre>{_json({'inventory':item.get('file_inventory_after'), 'inventory_complete':item.get('file_inventory_complete', False), 'text_collection_complete':item['files_complete']})}</pre></details>
<details><summary>可观察记忆</summary><pre>{_json({'observable':item.get('memory_observable',False), 'snapshot':item.get('memory_snapshot',{})})}</pre></details>
<details><summary>完整原始卡片（含未摘要的事件与文件）</summary><pre>{_json(item)}</pre></details>
<a href="#top">返回目录</a></article>""")
    links = " ".join(f'<a href="#item-{n}">第 {n} 项</a>' for n in range(1, len(cards) + 1))
    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>独立盲审 · {_esc(document['reviewer'])}</title><style>
body{{background:#f3f5f8;color:#17283c;font:17px/1.65 system-ui,-apple-system,sans-serif;margin:0}}main{{max-width:1000px;margin:auto;padding:32px 24px}}article{{background:white;margin:28px 0;padding:28px;border:1px solid #d5dee9;border-radius:12px}}h1{{font-size:30px}}h2{{font-size:25px}}a{{color:#1659b0}}nav{{display:flex;gap:14px;flex-wrap:wrap}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.6 ui-monospace,monospace;background:#f5f7fa;padding:12px}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;vertical-align:top;padding:10px;border-bottom:1px solid #dde4ec}}summary{{cursor:pointer;font-weight:600;padding:10px 0}}.muted{{color:#5e7087;overflow-wrap:anywhere}}.notice{{background:#fff5db;padding:16px;border-left:4px solid #c18b13}}@media(max-width:600px){{main,article{{padding:16px}}table{{font-size:14px}}}}@media print{{article{{break-before:page}}details{{display:block}}}}</style></head>
<body><main id="top"><h1>独立盲审 · {_esc(document['reviewer'])}</h1><p>{_esc(document['instructions'])}</p>
<p class="notice">这份材料不含自动评分结论，也不含另一位审阅者的答案。阅读或打开页面不会生成任何人工标签。请口述判断，由操作员逐项记录；第二位审阅者完成前不进行共同讨论。</p>
<nav>{links}</nav>{''.join(cards)}</main></body></html>"""
    output.write_text(page, encoding="utf-8")
    return output
