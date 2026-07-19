from pathlib import Path
import importlib.util


def test_runner_merges_template_before_trigger_scan():
    spec = importlib.util.find_spec("bulk_downloader.runner")
    assert spec is not None
    assert spec.origin is not None

    s = Path(spec.origin).read_text()

    merge_pos = s.find("merge_template_download_hints(page, learned_dl)")
    trigger_pos = s.find(
        'triggers_to_try=([trigger] if trigger else []) + '
        '(learned_dl.get("trigger_selectors") or [])'
    )
    scrape_pos = s.find("best=find_best_download(page,self.config.get")

    assert merge_pos != -1
    assert trigger_pos != -1
    assert scrape_pos != -1
    assert merge_pos < trigger_pos < scrape_pos
