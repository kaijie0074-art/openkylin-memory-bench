import pytest

from kmb.models import EvidenceBundle, ScoreResult
from kmb.stability import repeated_run_summary


@pytest.mark.parametrize("outcomes,agreement,decided", [
    (["pass", "pass", "fail"], 1 / 3, 3),
    (["pass", "undetermined", "pass"], 1 / 3, 2),
    (["pass", "pass", "pass"], 1, 3),
])
def test_repeats_keep_unknown_and_do_not_inflate_independent_tasks(outcomes, agreement, decided):
    evidence = [EvidenceBundle(run_id=f"r{i}", task_id="t", ability="update", family="f",
               split="dev", agent="fixture", provenance="simulated").freeze() for i in range(3)]
    scores = [ScoreResult(run_id=e.run_id, task_id="t", evidence_hash=e.evidence_hash,
                          scorer="A", verdict=v) for e, v in zip(evidence, outcomes)]
    result = repeated_run_summary(evidence, scores)
    assert len(result) == 1 and result[0]["independent_task_count"] == 1
    assert result[0]["variants"]["A"]["pairwise_verdict_agreement"] == agreement
    assert result[0]["variants"]["A"]["decided_runs"] == decided
    assert result[0]["variants"]["B"]["outcomes"] == {"not_scored": 3}


def test_different_transport_is_not_the_same_execution_configuration():
    runs = [EvidenceBundle(run_id=f'r{i}', task_id='t', ability='update', family='f',
                           split='dev', agent='fixture', provenance='simulated',
                           model={'requested':'m','returned':'m','provider':'same',
                                  'parameters':{'transport':transport}}).freeze()
            for i, transport in enumerate(['chat_completions','responses'])]
    assert repeated_run_summary(runs, []) == []


def test_changed_scorer_or_judge_does_not_claim_consistent_same_configuration():
    runs = [EvidenceBundle(run_id=f'r{i}', task_id='t', ability='update', family='f',
                           split='dev', agent='fixture', provenance='simulated').freeze()
            for i in range(2)]
    scores = [ScoreResult(run_id=e.run_id, task_id='t', evidence_hash=e.evidence_hash,
                           scorer='B', verdict='pass', scoring_fingerprint=f'version{i}',
                           model={'requested':f'judge{i}'}) for i,e in enumerate(runs)]
    result = repeated_run_summary(runs,scores)[0]['variants']['B']
    assert result['configuration_mismatch'] is True
    assert result['pairwise_verdict_agreement'] is None
    assert result['all_decided_and_identical'] is False
