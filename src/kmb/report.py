"""Self-contained Chinese HTML reports, with visible simulation and missing-data labels."""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path

import plotly.graph_objects as go

from kmb.models import EvidenceBundle, ScoreResult
from kmb.review import comparison
from kmb.stability import repeated_run_summary
from kmb.storage import write_json

ABILITIES = {"retention": "长期保持", "recall": "记忆调用", "update": "动态更新",
             "distinction": "相近区分", "boundary": "边界识别", "reuse": "任务复用"}
VERDICTS = {"pass": "通过", "fail": "失败", "undetermined": "无法判定"}


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def reference_id(run_id: str, reference: str) -> str:
    return "ref-" + hashlib.sha256(f"{run_id}:{reference}".encode()).hexdigest()[:24]


def percent(value) -> str:
    return "未获得" if value is None else f"{value:.1%}"


def render_report(evidence: list[EvidenceBundle], scores: list[ScoreResult], output: Path,
                  human: dict | None = None, *, selection: dict | None = None,
                  dataset_root: Path | None = None, holdout_manifest: dict | None = None,
                  protocol: dict | None = None) -> Path:
    data = comparison(evidence, scores, human, selection=selection, dataset_root=dataset_root,
                      holdout_manifest=holdout_manifest, protocol=protocol)
    data["repeated_runs"] = repeated_run_summary(evidence, scores)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "comparison.json", data)
    score_map = {(s.run_id, s.scorer): s for s in scores}
    simulated = data["provenance"] == ["simulated"]
    source_label = "模拟流水线验证 · 不代表真实智能体表现" if simulated else "真实运行证据 · 平台与人审状态见下表"
    validation = data["independent_validation"]
    validation_notice = ""
    stability_rows = []
    for group in data["repeated_runs"]:
        for variant, stats in group["variants"].items():
            stability_rows.append(f"<tr><td>{esc(group['task_id'])} / {esc(group['agent'])}</td>"
                                  f"<td>{group['repeats']}</td><td>{variant}</td>"
                                  f"<td>{esc(json.dumps(stats['outcomes'], ensure_ascii=False))}</td>"
                                  f"<td>{percent(stats['pairwise_verdict_agreement'])}</td></tr>")
    stability_html = ("<table><thead><tr><th>同题同配置</th><th>重复数</th><th>评分器</th>"
                      "<th>结果分布</th><th>两两判定一致率</th></tr></thead><tbody>"
                      + "".join(stability_rows) + "</tbody></table>" if stability_rows
                      else "<p>尚无同题同配置的重复实验，不计算稳定性。</p>")
    if validation["selected"]:
        status = "协议绑定检查通过" if validation["eligible"] else "验证未完成，存在执行或评分错误"
        validation_notice = (f"<br>留出验证：冻结版本 {esc(validation['selected'])} · {status}。"
                             "A/B/C 仍同时展示；其他版本仅作探索展示，不得再次选型。"
                             "协议一致不代表评分准确率达标。")
    if protocol is not None:
        validation_notice = ("<br>工程验证：" + ("协议绑定完整" if validation["eligible"] else "协议绑定，但存在执行或评分错误")
                             + "；未进行独立人工验证，A/B/C 无获证赢家。模型与规则一致不等于人工准确率。")
    fig = go.Figure()
    agents = sorted({e.agent for e in evidence})
    for agent_index, agent in enumerate(agents):
        for variant, color in zip("ABC", ["#2563eb", "#a855f7", "#059669"]):
            values, hover = [], []
            for ability in ABILITIES:
                runs = [e for e in evidence if e.agent == agent and e.ability == ability and e.status in {"completed", "task_failed", "budget_exhausted"}]
                results = [score_map.get((e.run_id, variant)) for e in runs]
                has_scores = any(s and s.status == "scored" for s in results)
                passed = sum(bool(s and s.status == "scored" and s.verdict == "pass") for s in results)
                values.append(passed / len(runs) * 100 if runs and has_scores else None)
                hover.append(f"{passed}/{len(runs)}；未判定仍保留在分母，评分器未运行则空缺")
            fig.add_trace(go.Scatterpolar(r=values + values[:1], theta=list(ABILITIES.values()) + list(ABILITIES.values())[:1],
                                         text=hover + hover[:1], name=f"{agent} · {variant}", mode="lines+markers",
                                         line={"color": color, "dash": ["solid", "dot", "dash"][agent_index % 3]}, connectgaps=False,
                                         hovertemplate="%{theta}: %{r:.1f}%<br>%{text}<extra>%{fullData.name}</extra>"))
    fig.update_layout(template="plotly_white", height=430, margin={"l": 60, "r": 60, "t": 35, "b": 35},
                      polar={"radialaxis": {"range": [0, 100], "ticksuffix": "%"}},
                      font={"family": "system-ui, sans-serif"}, legend={"orientation": "h"})
    chart = fig.to_html(full_html=False, include_plotlyjs=True, config={"displaylogo": False})
    rows = []
    for v in data["variants"]:
        rows.append("<tr>" + "".join(f"<td>{esc(x)}</td>" for x in [
            v["variant"], f'{v["decided_runs"]}/{v["valid_runs"]}', percent(v["coverage"]),
            percent(v["human_agreement"]), v["critical_false_passes"] if v["critical_false_passes"] is not None else "未人审",
            v["tokens"] if v["tokens"] is not None else "未知", f'{v["elapsed_seconds"]:.2f}s',
            "达到内部目标" if v["meets_internal_target"] else "未证明达标",
        ]) + "</tr>")
    objective = data.get("objective_results")
    objective_rows = ""
    if objective:
        objective_rows = "".join(
            f"<tr><td>{esc(key)}</td><td>{value['pass']}/{value['runs']}</td>"
            f"<td>{value['fail']}</td><td>{value['undetermined']}</td></tr>"
            for key, value in objective["by_agent_ability"].items())
    engineering_section = ("<section id='engineering'><h2>可核验产物结果</h2>"
                           "<p class='muted'>只统计客观判据；未判定保留在分母。该表反映任务产物，不能证明语义评分准确。</p>"
                           "<div class='panel scroll'><table><thead><tr><th>智能体 / 能力</th><th>通过 / 总数</th>"
                           "<th>失败</th><th>无法判定</th></tr></thead><tbody>"
                           + objective_rows + "</tbody></table></div><p>三版总体结论不同的运行："
                           + str(data.get("scorer_differences", {}).get("disagreement_runs", 0))
                           + "；逐条差异见 comparison.json。</p></section>" if objective is not None else "")
    cards = []
    for e in evidence:
        write_json(output / "evidence" / f"{e.run_id}.json", e.model_dump())
        criterion_rows = []
        for variant in "ABC":
            result = score_map.get((e.run_id, variant))
            if not result:
                criterion_rows.append(f"<tr><td>{variant}</td><td colspan='4'>未运行</td></tr>")
                continue
            if result.status != "scored":
                criterion_rows.append(f"<tr><td>{variant}</td><td colspan='4'>{esc(result.status)}：{esc(result.error)}</td></tr>")
            for c in result.criteria:
                links = ', '.join(f'<a href="#{reference_id(e.run_id, ref)}">{esc(ref)}</a>' for ref in c.evidence_refs)
                criterion_rows.append(f"<tr><td>{variant}</td><td>{esc(c.criterion_id)}</td><td>{esc(VERDICTS[c.verdict])}</td><td>{esc(c.reason)}</td><td>{links}</td></tr>")
        files = "".join(f'<h4 id="{reference_id(e.run_id, "file:" + path)}">{esc(path)}</h4><pre>{esc(content)}</pre>' for path, content in e.files_after.items()) or "<p>没有文件产物。</p>"
        events = "".join(f'<details id="{reference_id(e.run_id, "event:" + ev.id)}"><summary>{esc(ev.id)} · {esc(ev.kind)} · {esc(ev.session_id or "")}</summary><pre>{esc(json.dumps(ev.data,ensure_ascii=False,indent=2))}</pre></details>' for ev in e.events)
        inventory = "".join(
            f'<li id="{reference_id(e.run_id, "inventory:" + path)}">{esc(path)}：{esc(kind)}</li>'
            for path, kind in (e.file_inventory_after or {}).items())
        completeness = "".join(
            f'<p id="{reference_id(e.run_id, ref)}">{esc(label)}：{esc(value)}</p>'
            for ref, label, value in (
                ("inventory:complete", "目录清单完整", e.file_inventory_complete),
                ("files_after:complete", "文本文件采集完整（旧版证据兼容字段）", e.files_complete),
                ("actions:complete", "动作轨迹完整", e.environment.get("actions_complete", "unknown"))))
        memory = "".join(
            f'<h4 id="{reference_id(e.run_id, "memory:" + path)}">{esc(path)}</h4><pre>{esc(content)}</pre>'
            for path, content in e.memory_snapshot.items())
        cards.append(f"""<article id='{esc(e.run_id)}'><h3>{esc(e.task_id)} <small>{esc(ABILITIES[e.ability])}</small></h3>
<p>{esc(e.agent)} · {esc(e.provenance)} · {esc(e.status)} · {esc(e.split)}</p>
<p class='mono'>证据 SHA-256：{esc(e.evidence_hash)}</p>
<a href='evidence/{esc(e.run_id)}.json'>完整冻结证据 JSON</a>
<div class='scroll'><table><thead><tr><th>版本</th><th>判断项</th><th>结论</th><th>理由</th><th>证据引用</th></tr></thead><tbody>{''.join(criterion_rows)}</tbody></table></div>
<details><summary>查看真实记录的输入、输出与事件</summary>{events}</details>
<details><summary>查看文件产物</summary>{files}</details>
<details><summary>目录清单与采集完整性</summary>{completeness}<ul>{inventory}</ul></details>
<details><summary>可观察记忆</summary><p>内部记忆可见：{esc(e.memory_observable)}</p>{memory}</details>
<details><summary>环境与模型记录</summary><pre>{esc(json.dumps({'environment':e.environment,'model':e.model.model_dump(),'memory_observable':e.memory_observable,'error':e.error},ensure_ascii=False,indent=2))}</pre></details></article>""")
    page = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>openKylin 长期记忆评测 · 实验报告</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f6fa;color:#182334;font:16px/1.65 system-ui,-apple-system,'PingFang SC',sans-serif}}main{{max-width:1160px;margin:auto;padding:42px 24px}}h1{{font-size:34px;line-height:1.3;margin:12px 0}}h2{{margin-top:36px}}h3{{margin:0 0 8px}}small{{font-size:14px;color:#62738b;margin-left:10px}}a{{color:#165dc5}}.eyebrow{{color:#526780;font-weight:600;letter-spacing:2px}}.notice{{border-left:5px solid #d9931d;background:#fff6df;padding:16px 20px;margin:24px 0}}.metrics{{display:flex;gap:16px;flex-wrap:wrap}}.metric{{background:white;flex:1;min-width:180px;padding:18px 24px;border:1px solid #dae1eb;border-radius:10px}}.metric strong{{font-size:30px;display:block}}.panel,article{{background:white;border:1px solid #dae1eb;border-radius:10px;padding:24px;margin:20px 0}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{text-align:left;padding:12px;border-bottom:1px solid #e6eaf1;vertical-align:top}}th{{background:#f2f5fa;white-space:nowrap}}.scroll{{overflow:auto}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6fa;padding:14px;border-radius:6px;font-size:13px}}summary{{cursor:pointer;font-weight:600;padding:10px 0}}.mono{{font:12px/1.6 ui-monospace,monospace;overflow-wrap:anywhere}}.muted{{color:#5c6e86}}nav{{display:flex;gap:22px;flex-wrap:wrap;margin:20px 0}}@media print{{body{{background:white}}main{{padding:0}}details{{display:block}}article{{break-inside:avoid}}}}
</style></head><body><main><div class='eyebrow'>OPENKYLIN MEMORY BENCH / EVIDENCE FIRST</div><h1>长期记忆评测 · 实验报告</h1>
<p class='muted'>同一份运行证据，三种评分方法。先看依据，再看分数。</p>
<nav><a href='#comparison'>评分器比较</a><a href='#abilities'>六项能力</a><a href='#cases'>逐项证据</a><a href='comparison.json'>机器可读比较结果</a></nav>
<div class='notice'><strong>{esc(source_label)}</strong><br>人工校准：{'尚未进行，不能据此选择最佳评分器' if data['human_review']=='not_performed' else '已录入人审，仍需检查覆盖与分歧'}。<br>空缺不等于零分；模拟结果不进入真实成绩。没有达到内部目标时不宣布赢家。{validation_notice}</div>
<div class='metrics'><div class='metric'>证据份数<strong>{data['total_runs']}</strong></div><div class='metric'>有效任务记录<strong>{data['valid_runs']}</strong></div><div class='metric'>环境异常或终态未知<strong>{data['infrastructure_or_unknown_runs']}</strong></div><div class='metric'>预算耗尽<strong>{sum(e.status == "budget_exhausted" for e in evidence)}</strong></div></div>
<section id='comparison'><h2>三版评分器比较</h2><div class='panel scroll'><table><thead><tr><th>版本</th><th>自动判断／有效记录</th><th>覆盖率</th><th>与人工一致率</th><th>关键误放</th><th>Tokens</th><th>评分耗时</th><th>状态</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
{engineering_section}
<section id='abilities'><h2>六项能力：自动判通过比例</h2><p class='muted'>分母为对应能力的有效任务记录，包含未判定；评分器未运行的维度留空。预算耗尽后留下的文件仍可接受产物判定；产物通过不等于在预算内完成。此图展示被测输出，不代表评分器的准确率。</p><div class='panel'>{chart}</div></section>
<section id='stability'><h2>重复运行的稳定性</h2><p class='muted'>同题重复不增加独立题目数。无法判定与错误单列；一致不等于正确。</p><div class='panel scroll'>{stability_html}</div></section>
<section id='cases'><h2>逐项证据与判断</h2>{''.join(cards)}</section>
<footer class='muted'>数据范围与局限：当前为原创短程微型任务。跨会话测试不等同于数月真实使用。运行环境、模型、人工判断和证据哈希共同限定结论。</footer></main><script>
function revealReference(){{const target=document.getElementById(location.hash.slice(1));if(!target)return;let node=target;while(node){{if(node.tagName==='DETAILS')node.open=true;node=node.parentElement;}}target.scrollIntoView();}}
window.addEventListener('hashchange',revealReference);revealReference();
</script></body></html>"""
    path = output / "index.html"
    path.write_text(page, encoding="utf-8")
    return path
