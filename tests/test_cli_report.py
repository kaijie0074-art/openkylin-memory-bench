"""End-to-end offline CLI checks; never invoke native agents or real models."""
import json
import re

from kmb.cli import main


def test_demo_rescore_report_and_review_are_offline_and_non_destructive(tmp_path, monkeypatch):
    def forbid(*args, **kwargs):
        raise AssertionError("demo must never create a real provider")
    monkeypatch.setattr("kmb.provider.JudgeProvider.from_env", forbid)
    output = tmp_path / "demo"
    assert main(["demo", "--output", str(output)]) == 0
    compare = json.loads((output / "comparison.json").read_text())
    assert compare["total_runs"] == 24 and compare["provenance"] == ["simulated"]
    assert compare["human_review"] == "not_performed"
    assert all(not row["meets_internal_target"] for row in compare["variants"])
    page = (output / "index.html").read_text()
    assert "不代表真实智能体表现" in page and "simulation/expected" in page
    targets = set(re.findall(r'id=[\"\']([^\"\']+)[\"\']', page))
    refs = re.findall(r'href=[\"\']#(ref-[^\"\']+)[\"\']', page)
    assert refs and all(ref in targets for ref in refs)
    originals = {p: p.read_bytes() for p in (output / "runs").rglob("evidence.json")}
    assert len(originals) == 24
    assert main(["demo", "--output", str(output)]) == 2
    assert all(p.read_bytes() == content for p, content in originals.items())
    review = tmp_path / "review"
    assert main(["review", "export", "--evidence", str(output / "runs"), "--output", str(review)]) == 0
    merged = tmp_path / "consensus.json"
    assert main(["review", "merge", "--directory", str(review), "--output", str(merged)]) == 0
    result = json.loads(merged.read_text())
    assert len(result["pending"]) == 24 and result["labels"] == {}


def test_probe_failure_is_sanitized_without_traceback(monkeypatch, capsys):
    from kmb.provider import ProviderError
    class Unavailable:
        async def probe(self):
            raise ProviderError("provider_http_error", status_code=503)
    monkeypatch.setattr("kmb.cli.configured_provider", lambda: Unavailable())
    assert main(["probe-model"]) == 2
    assert "HTTP 503" in capsys.readouterr().err


def test_real_holdout_requires_selection_before_any_execution(tmp_path):
    assert main(["run", "--agent", "both", "--split", "holdout", "--repeat", "3",
                 "--output", str(tmp_path / "untouched")]) == 2
    assert not (tmp_path / "untouched").exists()
