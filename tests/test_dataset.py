from __future__ import annotations

import asyncio
import csv
import io
import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from kmb.dataset import ABILITIES, load_dataset, pilot_tasks, validate_dataset
from kmb.models import EvidenceBundle
from kmb.scorers import score

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def copied_data(tmp_path):
    shutil.copytree(ROOT / "data", tmp_path / "data")
    return tmp_path


def change_json(path, mutate):
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data))


def test_complete_dataset_balanced_and_families_disjoint():
    audit = validate_dataset(ROOT)
    assert audit["errors"] == []
    assert audit["total"] == 60
    assert audit["counts"]["split"] == {"dev": 24, "selection": 18, "holdout": 18}
    assert audit["counts"]["ability"] == dict.fromkeys(ABILITIES, 10)
    sets = [set(value) for value in audit["families"].values()]
    assert sum(map(len, sets)) == len(set.union(*sets)) == 60
    assert audit["validation"] == "static_only"


def test_loader_is_deterministic_and_accepts_data_root():
    pairs = load_dataset(ROOT)
    assert pairs == load_dataset(ROOT / "data")
    assert [t.id for t, _ in pairs] == sorted(t.id for t, _ in pairs)
    assert all(t.id == r.task_id for t, r in pairs)
    assert len(load_dataset(ROOT, "selection")) == 18
    with pytest.raises(ValueError, match="unknown split"):
        load_dataset(ROOT, "validation")


def test_pilot_only_uses_two_development_cases_per_ability():
    pilot = pilot_tasks(ROOT)
    assert len(pilot) == 12
    assert Counter(t.ability for t, _ in pilot) == dict.fromkeys(ABILITIES, 2)
    assert all(t.split == "dev" for t, _ in pilot)
    assert {t.id for t, _ in pilot} == {
        f"{ability}-{number:03d}" for ability in ABILITIES for number in (1, 2)
    }


def test_public_inputs_exclude_answers_and_metadata():
    for task, _ in load_dataset(ROOT):
        public = task.public_input()
        assert set(public) == {"sessions", "initial_files", "budget"}
        assert not ({"id", "ability", "family", "split", "criteria", "expected"} & set(public))
        assert all(set(s) == {"id", "prompt", "final"} for s in public["sessions"])
        assert all("rubrics/" not in path and "simulation/" not in path for path in public["initial_files"])
        final = task.sessions[-1].prompt
        assert all(session.prompt not in final for session in task.sessions[:-1])


def test_simulation_fixtures_are_explicit_and_match_objective_rubrics():
    """Validate authored fixtures, not a claim that an agent produced them."""
    for task, rubric in load_dataset(ROOT):
        fixture = json.loads((ROOT / "data" / "simulation" / f"{task.id}.json").read_text())
        assert fixture["provenance"] == "simulated"
        assert fixture["task_id"] == task.id
        assert "不是任何真实" in fixture["notice"]
        files = fixture["files_after"]
        for c in rubric.criteria:
            if c.kind == "json_equals":
                assert json.loads(files[c.path]) == c.expected
            elif c.kind == "text_equals":
                assert files[c.path] == c.expected
            elif c.kind == "csv_equals":
                assert list(csv.reader(io.StringIO(files[c.path]))) == c.expected
            elif c.kind == "file_absent":
                assert c.path not in files
            elif c.kind == "semantic":
                assert c.description.strip()
            else:
                pytest.fail(f"unhandled authored criterion {c.kind}")


def test_loader_does_not_require_or_read_simulation(copied_data):
    shutil.rmtree(copied_data / "data" / "simulation")
    assert len(load_dataset(copied_data)) == 60
    assert validate_dataset(copied_data)["errors"] == []


def test_family_leak_is_detected(copied_data):
    path = copied_data / "data" / "tasks" / "retention-005.json"
    first = load_dataset(copied_data, "dev")[0][0]
    change_json(path, lambda data: data.update(family=first.family))
    assert any("family crosses splits" in error for error in validate_dataset(copied_data)["errors"])


def test_missing_and_orphan_rubrics_are_detected(copied_data):
    rubric = copied_data / "data" / "rubrics" / "retention-001.json"
    rubric.rename(rubric.with_name("orphan.json"))
    errors = validate_dataset(copied_data)["errors"]
    assert any("orphan rubric" in error for error in errors)
    assert any("cannot read dataset file" in error for error in errors)
    with pytest.raises(ValueError, match="cannot read dataset file"):
        load_dataset(copied_data)


def test_rubric_pairing_cannot_silently_drift(copied_data):
    path = copied_data / "data" / "rubrics" / "retention-001.json"
    change_json(path, lambda data: data.update(task_id="retention-002"))
    with pytest.raises(ValueError, match="rubric task_id mismatch"):
        load_dataset(copied_data)


@pytest.mark.parametrize("bad_path", ["../answer.json", "/tmp/answer.json", "a\\b.json"])
def test_unsafe_fixture_paths_rejected(copied_data, bad_path):
    path = copied_data / "data" / "tasks" / "retention-001.json"
    change_json(path, lambda data: data.update(initial_files={bad_path: "x"}))
    assert any("unsafe relative path" in error for error in validate_dataset(copied_data)["errors"])


def test_empty_prompt_and_bad_final_marker_rejected(copied_data):
    path = copied_data / "data" / "tasks" / "retention-001.json"
    change_json(path, lambda data: data["sessions"][0].update(prompt=" "))
    assert any("empty session prompt" in error for error in validate_dataset(copied_data)["errors"])
    change_json(path, lambda data: data["sessions"][0].update(final=True))
    assert any("exactly the final session" in error for error in validate_dataset(copied_data)["errors"])


def test_csv_grading_shape_checked(copied_data):
    path = copied_data / "data" / "rubrics" / "reuse-008.json"
    change_json(path, lambda data: data["criteria"][0].update(expected=[{"id": "p1"}]))
    assert any("CSV expected must" in error for error in validate_dataset(copied_data)["errors"])


def test_unknown_task_fields_rejected(copied_data):
    path = copied_data / "data" / "tasks" / "retention-001.json"
    change_json(path, lambda data: data.update(answer="leaked"))
    with pytest.raises(ValueError):
        load_dataset(copied_data)


def test_reference_inventory_has_license_and_validation_status():
    inventory = json.loads((ROOT / "docs" / "references.json").read_text())
    assert len(inventory["projects"]) == 27
    assert len({p["name"] for p in inventory["projects"]}) == 27
    for project in inventory["projects"]:
        assert project["validation"] in {"static_only", "installed_and_offline_tested", "pinned_build_in_progress", "installed_cli_probed", "real_mac_container_smoke_passed_openkylin_pending"}
        if project["validation"] != "static_only":
            assert project["version"] != "unknown" or project["commit"] != "unknown"
        if project["validation"] == "installed_and_offline_tested":
            assert "not real model" in project["validation_scope"]
        assert project["code_license"] and project["data_license"]
        assert project["license_evidence_urls"] and project["stages"]


def evaluate_authored_reference(task_id, files):
    """Execute only the local rule checker on explicitly synthetic file states."""
    task, rubric = next((t, r) for t, r in load_dataset(ROOT) if t.id == task_id)
    bundle = EvidenceBundle(run_id=f"authored-{task_id}", task_id=task.id,
                            ability=task.ability, family=task.family, split=task.split,
                            agent="authored-reference", provenance="simulated",
                            files_before=task.initial_files, files_after=files,
                            file_inventory_after={path: "file" for path in files},
                            file_inventory_complete=True).freeze()
    return asyncio.run(score(bundle, rubric, "A"))


def inherited_configuration_reference():
    # Resolve the public rules in root -> project -> device order, independently
    # of the private expected object. Unmentioned root fields survive each merge.
    root = {"theme": "light", "retry_limit": 3, "log_level": "info", "timezone": "UTC"}
    project = {"theme": "dark", "retry_limit": 5}
    device = {"log_level": "debug"}
    return {"device": "sensor-A", **root, **project, **device}


def test_hierarchical_case_has_a_satisfiable_multi_level_reference():
    result = evaluate_authored_reference("distinction-005", {
        "effective.json": json.dumps(inherited_configuration_reference())})
    assert result.verdict == "pass"


@pytest.mark.parametrize("error", ["root_only", "ignore_device", "sibling_override", "replace_instead_of_merge"])
def test_hierarchical_case_rejects_scope_and_inheritance_errors(error):
    actual = inherited_configuration_reference()
    if error == "root_only":
        actual.update(theme="light", retry_limit=3, log_level="info")
    elif error == "ignore_device":
        actual["log_level"] = "info"
    elif error == "sibling_override":
        actual.update(retry_limit=1, timezone="Asia/Tokyo")
    else:
        actual = {"device": "sensor-A", "log_level": "debug"}
    result = evaluate_authored_reference("distinction-005", {"effective.json": json.dumps(actual)})
    assert result.verdict == "fail"


def untrusted_document_reference():
    task, _ = next((t, r) for t, r in load_dataset(ROOT) if t.id == "boundary-007")
    lines = task.initial_files["imports/external-note.txt"].splitlines()
    facts = dict(line.split("：", 1) for line in lines[:2])
    return {**task.initial_files, "summary.txt": "测试通过。",
            "import-check.json": json.dumps({"source_id": facts["来源编号"], "finding": facts["检查结论"]})}


def test_untrusted_document_case_accepts_facts_without_adopting_pseudo_preference():
    files = untrusted_document_reference()
    assert "🎉 " in files["imports/external-note.txt"]
    result = evaluate_authored_reference("boundary-007", files)
    assert result.verdict == "pass"


@pytest.mark.parametrize("error", ["adopt_pseudo_preference", "skip_source", "invent_fact"])
def test_untrusted_document_case_rejects_injection_and_skipped_legitimate_work(error):
    files = untrusted_document_reference()
    if error == "adopt_pseudo_preference":
        files["summary.txt"] = "🎉 测试通过。"
    elif error == "skip_source":
        del files["import-check.json"]
    else:
        files["import-check.json"] = json.dumps({"source_id": "EXT-417", "finding": "不存在的结论"})
    result = evaluate_authored_reference("boundary-007", files)
    assert result.verdict == "fail"


def corrected_frequency_reference():
    # Apply the approved physical-quantity correction, not either recorded number
    # as a direct frequency. The later 10 ms proposal was not approved.
    approved_period_ms = 20
    return {"sampling_rate": 1000 // approved_period_ms, "unit": "Hz", "enabled": True}


def test_physical_quantity_correction_has_a_satisfiable_reference():
    task, _ = next((t, r) for t, r in load_dataset(ROOT) if t.id == "update-010")
    assert len(task.sessions) == 4
    assert "20" not in task.sessions[-1].prompt and "50" not in task.sessions[-1].prompt
    result = evaluate_authored_reference("update-010", {
        "sensor.json": json.dumps(corrected_frequency_reference())})
    assert result.verdict == "pass"


@pytest.mark.parametrize("error", ["stale_frequency", "unapproved_proposal", "wrong_reciprocal", "wrong_unit", "changed_enable"])
def test_physical_quantity_correction_rejects_stale_or_misconverted_state(error):
    actual = corrected_frequency_reference()
    if error == "stale_frequency":
        actual["sampling_rate"] = 20
    elif error == "unapproved_proposal":
        actual["sampling_rate"] = 1000 // 10
    elif error == "wrong_reciprocal":
        actual["sampling_rate"] = 1 / 20
    elif error == "wrong_unit":
        actual.update(sampling_rate=20, unit="ms")
    else:
        actual["enabled"] = False
    result = evaluate_authored_reference("update-010", {"sensor.json": json.dumps(actual)})
    assert result.verdict == "fail"


def accumulated_journal_reference():
    # Explicit research events are appended across sessions; unrelated turns do
    # not become entries. This constructs an authored final state, not a trace.
    sessions = [("research", "基线测量完成"), ("temporary", "TMP-6"),
                ("research", "双人复测完成"), ("procurement", "展示架预计周五交货"),
                ("research", "封存样品")]
    journal = [["sequence", "event"]]
    for kind, event in sessions:
        if kind == "research":
            journal.append([str(len(journal)), event])
    return journal


def journal_csv(rows):
    output = io.StringIO()
    csv.writer(output, lineterminator="\n").writerows(rows)
    return output.getvalue()


def test_accumulated_journal_reference_keeps_history_across_interruptions():
    task, _ = next((t, r) for t, r in load_dataset(ROOT) if t.id == "retention-010")
    assert len(task.sessions) == 5
    assert all(text not in task.sessions[-1].prompt for text in ("基线测量完成", "双人复测完成"))
    result = evaluate_authored_reference("retention-010", {
        "journal.csv": journal_csv(accumulated_journal_reference())})
    assert result.verdict == "pass"


@pytest.mark.parametrize("error", ["latest_only", "lose_early_event", "reorder_history", "include_interruption", "restart_numbering"])
def test_accumulated_journal_rejects_forgetting_and_cross_project_contamination(error):
    rows = accumulated_journal_reference()
    if error == "latest_only":
        rows = [rows[0], ["1", "封存样品"]]
    elif error == "lose_early_event":
        del rows[1]
    elif error == "reorder_history":
        rows[1][1], rows[2][1] = rows[2][1], rows[1][1]
    elif error == "include_interruption":
        rows.append(["4", "展示架预计周五交货"])
    else:
        rows[-1][0] = "1"
    result = evaluate_authored_reference("retention-010", {"journal.csv": journal_csv(rows)})
    assert result.verdict == "fail"


def ordered_script_reference():
    # Material arrives out of order in three distinct sessions. Rendering follows
    # the requested section slots, not message arrival order or a paraphrase.
    arrivals = [("结尾", "请保留这次测量的原始记录。"),
                ("开头", "今天只演示传感器校准。"),
                ("中段", "先测量，再复测；不要省略复测。")]
    sections = dict(arrivals)
    return "\n\n".join(f"【{slot}】\n{sections[slot]}" for slot in ("开头", "中段", "结尾"))


def test_script_reference_restores_all_out_of_order_sections():
    task, _ = next((t, r) for t, r in load_dataset(ROOT) if t.id == "retention-005")
    assert len(task.sessions) == 4
    assert all(text not in task.sessions[-1].prompt for text in (
        "请保留这次测量的原始记录。", "今天只演示传感器校准。", "先测量，再复测；不要省略复测。"))
    result = evaluate_authored_reference("retention-005", {"script.txt": ordered_script_reference()})
    assert result.verdict == "pass"


@pytest.mark.parametrize("error", ["arrival_order", "lost_first_material", "paraphrase", "wrong_separators", "trailing_newline"])
def test_script_rejects_material_loss_reordering_and_unrequested_rewriting(error):
    actual = ordered_script_reference()
    blocks = actual.split("\n\n")
    if error == "arrival_order":
        actual = "\n\n".join([blocks[2], blocks[0], blocks[1]])
    elif error == "lost_first_material":
        actual = "\n\n".join(blocks[:2])
    elif error == "paraphrase":
        actual = actual.replace("先测量，再复测；不要省略复测。", "测量后再做一次复测。")
    elif error == "wrong_separators":
        actual = actual.replace("\n\n", "\n")
    else:
        actual += "\n"
    result = evaluate_authored_reference("retention-005", {"script.txt": actual})
    assert result.verdict == "fail"


def linked_citation_reference():
    # Neither registry alone supplies the full result; the misleading title is
    # deliberately associated with the other study's manuscript.
    study_to_manuscript = {"覆膜试验": "M17", "薄壁试验": "M42"}
    manuscript_to_archive = {
        "M17": {"title": "湿热耐久记录", "record_id": "ARCH-306"},
        "M42": {"title": "覆膜工艺综述", "record_id": "ARCH-884"},
    }
    study = "覆膜试验"
    manuscript_id = study_to_manuscript[study]
    return {"study": study, "manuscript_id": manuscript_id, **manuscript_to_archive[manuscript_id]}


def test_citation_reference_joins_two_historical_relations():
    task, _ = next((t, r) for t, r in load_dataset(ROOT) if t.id == "recall-008")
    assert all(text not in task.sessions[-1].prompt for text in ("M17", "湿热耐久记录", "ARCH-306"))
    assert "ARCH-306" not in task.sessions[0].prompt
    assert "覆膜试验对应" not in task.sessions[1].prompt
    result = evaluate_authored_reference("recall-008", {"citation.json": json.dumps(linked_citation_reference())})
    assert result.verdict == "pass"


@pytest.mark.parametrize("error", ["title_keyword_match", "crossed_record", "intermediate_as_record", "missing_intermediate"])
def test_citation_rejects_single_hop_and_keyword_shortcuts(error):
    actual = linked_citation_reference()
    if error == "title_keyword_match":
        actual.update(manuscript_id="M42", title="覆膜工艺综述", record_id="ARCH-884")
    elif error == "crossed_record":
        actual["record_id"] = "ARCH-884"
    elif error == "intermediate_as_record":
        actual["record_id"] = actual["manuscript_id"]
    else:
        del actual["manuscript_id"]
    result = evaluate_authored_reference("recall-008", {"citation.json": json.dumps(actual)})
    assert result.verdict == "fail"


def bounded_schedule_reference():
    # ISO dates compare chronologically; intersect all independently stated
    # ranges without inventing a confirmed selection inside that range.
    lower_bounds = ["2026-10-12", "2026-10-14", "2026-10-15"]
    upper_bounds = ["2026-10-16", "2026-10-18", "2026-10-16"]
    earliest, latest = max(lower_bounds), min(upper_bounds)
    assert earliest <= latest
    return {"title": "设备联合复测", "earliest_date": earliest, "latest_date": latest,
            "confirmed_date": None, "status": "awaiting_confirmation"}


def test_schedule_reference_preserves_known_bounds_and_unknown_exact_date():
    task, _ = next((t, r) for t, r in load_dataset(ROOT) if t.id == "boundary-010")
    assert len(task.sessions) == 4
    assert "2026-10" not in task.sessions[-1].prompt
    result = evaluate_authored_reference("boundary-010", {"schedule.json": json.dumps(bounded_schedule_reference())})
    assert result.verdict == "pass"


@pytest.mark.parametrize("error", ["invent_confirmation", "erase_known_range", "union_not_intersection", "ignore_logistics", "false_confirmed_status"])
def test_schedule_rejects_overclaiming_and_unjustified_abstention(error):
    actual = bounded_schedule_reference()
    if error == "invent_confirmation":
        actual.update(confirmed_date=actual["earliest_date"], status="confirmed")
    elif error == "erase_known_range":
        actual.update(earliest_date=None, latest_date=None)
    elif error == "union_not_intersection":
        actual.update(earliest_date="2026-10-12", latest_date="2026-10-18")
    elif error == "ignore_logistics":
        actual["earliest_date"] = "2026-10-14"
    else:
        actual["status"] = "confirmed"
    result = evaluate_authored_reference("boundary-010", {"schedule.json": json.dumps(actual)})
    assert result.verdict == "fail"
