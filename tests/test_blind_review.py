from html.parser import HTMLParser

from kmb.models import EvidenceBundle, PrivateRubric
from kmb.review import export_review


def test_blind_html_escapes_evidence_and_has_no_labels_or_automatic_verdicts(tmp_path):
    marker = '<script>alert("untrusted")</script>'
    bundle = EvidenceBundle(run_id='fixture', task_id='fixture', ability='update', family='fixture',
                            split='dev', agent='fixture', provenance='simulated',
                            files_after={'report.txt': marker}, memory_observable=True,
                            memory_snapshot={'MEMORY.md':'remembered fact'}).freeze()
    rubric = PrivateRubric(task_id='fixture', criteria=[{'id':'c1', 'kind':'text_equals',
                            'description':'Match text', 'path':'report.txt', 'expected':'ok'}])
    out = tmp_path / 'review'
    export_review([bundle], {'fixture':rubric}, out, ('owner', 'peer'))
    for n in (1,2):
        page = (out / f'reviewer-{n}' / 'index.html').read_text()
        assert marker not in page and '&lt;script&gt;' in page
        assert 'remembered fact' in page and '没有预填判断' in page
        assert not (out / f'reviewer-{n}' / 'labels').exists()
        assert 'automatic_score' not in page
        parser = HTMLParser()
        parser.feed(page)
