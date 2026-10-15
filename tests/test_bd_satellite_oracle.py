#!/usr/bin/env python3
"""Tests for bd-satellite-prefilter.py and bd-oracle-advisory.py (O1789 token-tools #6).

Ported from token-efficiency/frontier/test_satellite_oracle.py. Every test is hermetic:
the autouse fixture clears the BD_* config env, makes any urlopen/socket connect/subprocess
call raise "HERMETIC: ...", and refuses writes outside tmp_path through the modules' open().
The AGY tests that dialled 192.0.2.1 or the default oracle host now mock the transport.

Regression tests for the defects fixed in the port:
  * no endpoint/host configured -> no network call (AGY dialled hardcoded defaults);
  * no alerts log configured -> no write (AGY appended to a hub-absolute alerts.log);
  * env config is read at call time; no API key or operator PIN literal in source;
  * an oracle that could not look yields COULD_NOT_LOOK, never an invented BOARD/REFUTE
    (fallback advisory, empty consensus, missing verdict, verdict-less reply);
  * an explicit leading `BOARD:` is not flipped to REFUTE by body words;
  * query_all honours the adapter port; the CLI takes no --pin (argv is world-readable).
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

BD_GATE_SCOPE = 'module'  # O906.1 gate marker (precollect guard)
HERE = Path(__file__).resolve().parent
_HARNESS_ROOT = Path('/home/mboyle/bd-persist/harness')
PREFILTER_PATH = HERE.parent / 'bd-satellite-prefilter.py' if (HERE.parent / 'bd-satellite-prefilter.py').exists() else _HARNESS_ROOT / 'bd-satellite-prefilter.py'
ORACLE_PATH = HERE.parent / 'bd-oracle-advisory.py' if (HERE.parent / 'bd-oracle-advisory.py').exists() else _HARNESS_ROOT / 'bd-oracle-advisory.py'
ENV_KEYS = ('BD_LITELLM_ENDPOINT', 'BD_LITELLM_KEY', 'LITELLM_MASTER_KEY', 'BD_SATELLITE_MODEL',
            'BD_ALERTS_LOG', 'BD_ORACLE_HOST', 'BD_ORACLE_PORT', 'BD_ORACLE_PIN', 'BD_ORACLE_BIN',
            'OPERATOR_AUTH_PIN')
_REAL_RUN = subprocess.run


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sp = _load('bd_satellite_prefilter', PREFILTER_PATH)
oa = _load('bd_oracle_advisory', ORACLE_PATH)
NO_LOOK = 'COULD_NOT_LOOK'


@pytest.fixture(autouse=True)
def hermetic(monkeypatch, tmp_path):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    calls = {'net': [], 'proc': [], 'writes': []}

    def no_net(*a, **k):
        calls['net'].append(a)
        raise AssertionError(f'HERMETIC: network call attempted: {a[:1]}')

    def no_connect(self, *a, **k):
        calls['net'].append(a)
        raise AssertionError(f'HERMETIC: socket connect attempted: {a[:1]}')

    def no_proc(*a, **k):
        calls['proc'].append(a)
        raise AssertionError(f'HERMETIC: subprocess attempted: {a[:1]}')

    def guarded_open(file, mode='r', *a, **k):
        if any(c in mode for c in 'wax+') and not str(file).startswith(str(tmp_path)):
            calls['writes'].append(str(file))
            raise PermissionError(f'HERMETIC: write outside tmp_path: {file}')
        return open(file, mode, *a, **k)

    monkeypatch.setattr(urllib.request, 'urlopen', no_net)
    monkeypatch.setattr(socket.socket, 'connect', no_connect)
    monkeypatch.setattr(subprocess, 'run', no_proc)
    for mod in (sp, oa):
        monkeypatch.setattr(mod, 'open', guarded_open, raising=False)
    return calls


def _fake_resp(payload: Any, status: int = 200):
    resp = mock.MagicMock()
    resp.status = status
    resp.read.return_value = json.dumps(payload).encode('utf-8')
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_hermetic_guard_can_say_yes(hermetic, tmp_path) -> None:
    """Positive control (rule 7): the guard really catches network and stray writes."""
    with pytest.raises(AssertionError, match='HERMETIC: network'):
        urllib.request.urlopen('http://127.0.0.1:9/')
    with pytest.raises(PermissionError, match='HERMETIC: write outside'):
        sp.open(str(tmp_path.parent / 'stray.log'), 'a')
    assert hermetic['net'] and hermetic['writes']


class TestSatellitePreFilter:

    def test_prefilter_timeout_enforcement_forced(self) -> None:
        res = sp.prefilter_command_output('sample test output', timeout=0.0)
        assert res['source'] == 'fallback'
        assert res['status'] in ('PASS', 'FAIL', 'DIGEST')
        assert res['tokens'] <= sp.MAX_DIGEST_TOKENS
        assert res['latency_ms'] < 50.0

    def test_prefilter_timeout_negative(self) -> None:
        res = sp.prefilter_command_output('sample test output', timeout=-1.5)
        assert res['source'] == 'fallback'
        assert res['tokens'] <= sp.MAX_DIGEST_TOKENS

    def test_prefilter_force_timeout_flag(self) -> None:
        res = sp.prefilter_command_output('tests passed', timeout=3.0, force_timeout=True)
        assert res['source'] == 'fallback'
        assert res['tokens'] <= sp.MAX_DIGEST_TOKENS

    def test_prefilter_simulated_network_error_triggers_fallback(self, tmp_path) -> None:
        err = urllib.error.URLError(TimeoutError('Connection timed out'))
        with mock.patch('urllib.request.urlopen', side_effect=err) as m:
            res = sp.prefilter_command_output('tests/test_x.py . [100%]\n1 passed', timeout=1.0,
                                              endpoint='http://satellite.invalid:4000',
                                              alerts_log=tmp_path / 'alerts.log')
        assert m.call_count == 1
        assert res['source'] == 'fallback'
        assert res['status'] == 'PASS'
        assert 'URLError' in (tmp_path / 'alerts.log').read_text(encoding='utf-8')

    def test_prefilter_output_token_cap_strict_le_50(self) -> None:
        bulky = 'Traceback (most recent call last):\n' + '\n'.join(
            f"  File 'module_{i}.py', line {i * 5}, in func_{i}" for i in range(120)
        ) + '\nAssertionError: Expected status 200, got 500 Internal Server Error'
        assert len(bulky.split()) > 300
        res = sp.prefilter_command_output(bulky, task_type='test', timeout=0.0)
        assert res['tokens'] <= 50
        assert len(res['digest'].split()) <= 50
        assert res['status'] == 'FAIL'

    def test_prefilter_setup_error_handling(self) -> None:
        setup_log = ('==================== ERRORS ====================\n'
                     'ERROR at setup of test_service_fixture - RuntimeError: service daemon down\n'
                     '1 error in 0.12s')
        res = sp.prefilter_command_output(setup_log, task_type='test', timeout=0.0)
        assert res['status'] == 'FAIL'
        assert 'SyntaxError' not in res['digest']
        assert 'ERROR at setup of' in res['digest']

    def test_prefilter_bounded_envelope_retention(self) -> None:
        envelope = '[OUTPUT BOUNDED: 45 lines, 2100 bytes -> bd-blobs/a1b2c3d4e5.log]\nLine 1\nLine 2\n...\nLine 45'
        res = sp.prefilter_command_output(envelope, task_type='test', timeout=0.0)
        assert 'bd-blobs/a1b2c3d4e5.log' in res['digest']
        assert res['tokens'] <= 50

    def test_prefilter_pass_classification(self) -> None:
        res = sp.prefilter_command_output('tests/test_core.py ........ [100%]\n17 passed in 0.35s',
                                          task_type='test', timeout=0.0)
        assert res['status'] == 'PASS'

    def test_prefilter_git_diff_digest(self) -> None:
        diff = '\n'.join(f'diff --git a/file_{i}.py b/file_{i}.py\n+ new line' for i in range(50))
        res = sp.prefilter_command_output(diff, task_type='diff', timeout=0.0)
        assert res['tokens'] <= 50
        assert 'diff: file_0.py, file_1.py, file_2.py (+47 more)' in res['digest']

    def test_prefilter_alerts_log_file_written(self, tmp_path) -> None:
        log = tmp_path / 'alerts.log'
        res = sp.prefilter_command_output('test payload', timeout=0.0, alerts_log=log)
        assert res['source'] == 'fallback'
        content = log.read_text(encoding='utf-8')
        assert 'SATELLITE_FAILOVER |' in content and 'Latency:' in content

    def test_prefilter_class_wrapper(self) -> None:
        res = sp.SatellitePreFilter(timeout=0.0).filter('test class wrapper')
        assert res['source'] == 'fallback'
        assert res['tokens'] <= 50

    def test_estimate_and_enforce_token_cap(self) -> None:
        assert sp.estimate_tokens('') == 0
        assert sp.estimate_tokens('hello world') == 2
        capped, n = sp.enforce_token_cap('word ' * 100, max_tokens=30)
        assert n <= 30 and capped.endswith('...')

    def test_classify_status_edge_cases(self) -> None:
        assert sp.classify_status('') == 'DIGEST'
        assert sp.classify_status('42 passed, 0 failed in 1.2s') == 'PASS'
        assert sp.classify_status('FAILED tests/test_a.py') == 'FAIL'

    def test_unconfigured_endpoint_makes_no_network_call(self, hermetic) -> None:
        res = sp.prefilter_command_output('17 passed in 0.35s', timeout=1.0)
        assert hermetic['net'] == [], 'prefilter dialled a default endpoint nobody configured'
        assert res['source'] == 'fallback' and res['status'] == 'PASS'

    def test_unconfigured_alerts_log_writes_nothing(self, hermetic) -> None:
        sp.prefilter_command_output('payload', timeout=0.0)
        sp.log_failover_alert('ep', 'reason', 0.1)
        assert hermetic['writes'] == [], f"alerts written to unconfigured path: {hermetic['writes']}"

    def test_alerts_log_env_read_at_call_time(self, monkeypatch, tmp_path) -> None:
        log = tmp_path / 'env-alerts.log'
        monkeypatch.setenv('BD_ALERTS_LOG', str(log))
        sp.prefilter_command_output('payload', timeout=0.0)
        assert 'SATELLITE_FAILOVER' in log.read_text(encoding='utf-8')

    def test_env_endpoint_satellite_path_without_key_literal(self, monkeypatch) -> None:
        monkeypatch.setenv('BD_LITELLM_ENDPOINT', 'http://satellite.invalid:4000')
        reply = _fake_resp({'choices': [{'message': {'content': '<STATUS> PASS | 17 passed'}}]})
        with mock.patch('urllib.request.urlopen', return_value=reply) as m:
            res = sp.prefilter_command_output('x' * 5000 + '\n17 passed', timeout=1.0)
        req = m.call_args[0][0]
        assert req.full_url == 'http://satellite.invalid:4000/v1/chat/completions'
        assert 'Authorization' not in req.headers, f'unconfigured key sent: {req.headers}'
        body = json.loads(req.data)
        assert body['max_tokens'] == 64
        assert len(body['messages'][1]['content']) < 1300  # output[:1200] + instruction
        assert res['source'] == 'satellite' and res['digest'] == '<STATUS> PASS | 17 passed'

    def test_env_key_sent_when_configured(self, monkeypatch) -> None:
        monkeypatch.setenv('BD_LITELLM_KEY', 'k-test')
        reply = _fake_resp({'choices': [{'message': {'content': 'ok'}}]})
        with mock.patch('urllib.request.urlopen', return_value=reply) as m:
            sp.prefilter_command_output('out', timeout=1.0, endpoint='http://satellite.invalid:4000')
        assert m.call_args[0][0].headers['Authorization'] == 'Bearer k-test'

    def test_no_secret_or_hub_path_literals_in_prefilter(self) -> None:
        src = PREFILTER_PATH.read_text(encoding='utf-8')
        assert 'sk-fleet-local' not in src
        assert '/home/' not in src and '127.0.0.1' not in src


class TestOracleAdvisoryAdapter:

    def test_oracle_rule_76_json_schema_conformance(self) -> None:
        res = oa.parse_oracle_verdict('BOARD: All architectural invariants verified cleanly.',
                                      model_name='gemini', round_id='R1', seat='review-oracle')
        assert {'round', 'seat', 'verdict', 'findings'} <= set(res)
        assert res['verdict'] == 'BOARD' and res['findings'] == []
        assert res['marginal_cost'] == 0.0

    def test_oracle_refute_verdict_has_findings(self) -> None:
        text = ('REFUTE: Architectural defect identified.\n'
                '- src/frontier/core.py:45: unhandled NoneType exception\n'
                '- tests/test_core.py:12: missing negative timeout test\n')
        res = oa.parse_oracle_verdict(text, model_name='claude-a')
        assert res['verdict'] == 'REFUTE'
        assert any('core.py:45' in f for f in res['findings'])

    def test_oracle_json_verdict_passthrough(self) -> None:
        res = oa.parse_oracle_verdict('{"round": "R2", "seat": "review-lens", "verdict": "BOARD", "findings": []}')
        assert (res['round'], res['seat'], res['verdict'], res['findings']) == ('R2', 'review-lens', 'BOARD', [])

    def test_leading_board_not_flipped_by_body_words(self) -> None:
        for text in ('BOARD: 0 FAILED, nothing invalid', 'VERDICT: BOARD\nno defect found'):
            assert oa.parse_oracle_verdict(text)['verdict'] == 'BOARD', text

    def test_verdictless_reply_is_could_not_look(self) -> None:
        res = oa.parse_oracle_verdict('I could not open the attachment.')
        assert res['verdict'] == NO_LOOK, f"verdict-less reply became {res['verdict']}"

    @pytest.mark.parametrize('reply', [
        'I inspected the module; no DEFECT was found.',                           # r1 lens F1: body word -> REFUTE
        'The existing test named FAILED is covered; I have no review verdict.',   # r1 lens F1: body word -> REFUTE
        'Please check x.py:3; this is advice without a verdict.',                 # control: passed on r1 too
        'I would not say BOARD or REFUTE here; I have no review verdict.',        # vote words in the body only
    ])
    def test_body_keywords_never_fabricate_a_vote(self, reply) -> None:
        res = oa.parse_oracle_verdict(reply)
        assert res['verdict'] == NO_LOOK, {'reply': reply, 'actual': res['verdict'], 'expected': NO_LOOK}
        assert res['findings'] == [] and 'error' in res

    def test_oracle_consensus_evaluation_unanimous_board(self) -> None:
        verdicts = [{'model': m, 'verdict': 'BOARD', 'findings': [], 'source': 'cdp_oracle'} for m in oa.KNOWN_ORACLES]
        c = oa.evaluate_consensus(verdicts, round_id='round-final', seat='consensus-judge')
        assert c['verdict'] == 'BOARD' and c['is_unanimous'] is True
        assert c['tallies']['BOARD'] == 4 and c['tallies']['REFUTE'] == 0
        assert c['marginal_cost'] == 0.0

    def test_oracle_consensus_split_vote_majority_vs_unanimous(self) -> None:
        verdicts = [{'model': m, 'verdict': 'BOARD', 'findings': [], 'source': 'cdp_oracle'} for m in oa.KNOWN_ORACLES[:3]]
        verdicts.append({'model': 'claude-b', 'verdict': 'REFUTE', 'findings': ['bug in line 10'], 'source': 'cdp_oracle'})
        majority = oa.evaluate_consensus(verdicts, require_unanimous=False)
        assert majority['verdict'] == 'BOARD' and majority['is_unanimous'] is False
        unanimous = oa.evaluate_consensus(verdicts, require_unanimous=True)
        assert unanimous['verdict'] == 'REFUTE' and len(unanimous['findings']) == 1

    def test_empty_consensus_is_could_not_look(self) -> None:
        c = oa.evaluate_consensus([])
        assert c['verdict'] == NO_LOOK, f"empty consensus became {c['verdict']}"
        assert c['is_unanimous'] is False and c['marginal_cost'] == 0.0

    def test_unlooked_votes_are_not_counted_as_board(self) -> None:
        verdicts = [oa.execute_local_fallback_advisory('p', oracle='gemini'),
                    {'model': 'chatgpt', 'findings': [], 'source': 'cdp_oracle'}]
        c = oa.evaluate_consensus(verdicts)
        assert c['verdict'] == NO_LOOK, f"unlooked votes became {c['verdict']}"
        assert c['tallies']['BOARD'] == 0 and c['tallies'][NO_LOOK] == 2
        one = oa.evaluate_consensus(verdicts + [{'model': 'claude-a', 'verdict': 'BOARD', 'source': 'cdp_oracle'}],
                                    require_unanimous=True)
        assert one['verdict'] == NO_LOOK

    def test_fallback_advisory_invents_no_verdict(self) -> None:
        for prompt in ('review this patch', 'Traceback: AssertionError FAIL'):
            res = oa.execute_local_fallback_advisory(prompt)
            assert res['verdict'] == NO_LOOK, f"fallback invented {res['verdict']} for {prompt!r}"
            assert res['source'] == 'fallback' and res['findings'] == [] and res['marginal_cost'] == 0.0

    def test_oracle_cdp_unreachable_graceful_fallback(self) -> None:
        with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('refused')) as m:
            res = oa.OracleAdvisoryAdapter(host='oracle.invalid', timeout=0.5).query('Review', oracle='gemini')
        assert m.call_count == 1 and 'oracle.invalid:9222' in m.call_args[0][0].full_url
        assert res['source'] == 'fallback' and 'error' in res and res['verdict'] == NO_LOOK

    def test_oracle_multi_model_advisory_fallback(self) -> None:
        with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('refused')):
            res = oa.query_multi_model_advisory(prompt='Analyze', host='oracle.invalid', timeout=0.5)
        assert res['source'] == 'fallback' and res['verdict'] == NO_LOOK
        assert len(res['model_verdicts']) == 4 and res['tallies'][NO_LOOK] == 4

    def test_query_all_honours_adapter_port(self) -> None:
        with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('refused')) as m:
            oa.OracleAdvisoryAdapter(host='oracle.invalid', port=9333).query_all('p')
        assert ':9333/' in m.call_args[0][0].full_url, m.call_args[0][0].full_url

    @staticmethod
    def _fake_oracle(tmp_path):
        fake_bin = tmp_path / 'bd-oracle'
        fake_bin.write_text('#!/bin/sh\n', encoding='utf-8')
        fake_bin.chmod(0o755)
        stdout = json.dumps({o: 'BOARD: fine' for o in oa.KNOWN_ORACLES})
        return fake_bin, subprocess.CompletedProcess([], 0, stdout=stdout, stderr='')

    def test_query_all_hands_configured_port_to_every_child(self, tmp_path) -> None:
        fake_bin, done = self._fake_oracle(tmp_path)
        with mock.patch.object(oa, 'is_cdp_reachable', return_value=True) as health, \
                mock.patch('subprocess.run', return_value=done) as run:
            res = oa.OracleAdvisoryAdapter(host='oracle.invalid', port=9333, oracle_bin=fake_bin).query_all('p')
        assert health.call_args.kwargs['port'] == 9333 and res['verdict'] == 'BOARD'
        assert len(run.call_args_list) == len(oa.KNOWN_ORACLES)
        for call in run.call_args_list:
            cmd, env = call.args[0], call.kwargs['env']
            assert env.get('BD_ORACLE_PORT') == '9333', {'configured_port': 9333, 'child_argv': cmd,
                                                         'child_port_env': env.get('BD_ORACLE_PORT')}

    def test_adapter_query_hands_configured_port_to_child(self, tmp_path) -> None:
        fake_bin, done = self._fake_oracle(tmp_path)
        with mock.patch.object(oa, 'is_cdp_reachable', return_value=True), \
                mock.patch('subprocess.run', return_value=done) as run:
            oa.OracleAdvisoryAdapter(host='oracle.invalid', port=9333, oracle_bin=fake_bin).query('p', oracle='gemini')
            run_default = run.call_count
            oa.query_bd_oracle_cli('p', oracle='gemini', host='oracle.invalid', oracle_bin=fake_bin)
        assert run_default == 1 and run.call_count == 2
        configured, default = run.call_args_list[0].kwargs['env'], run.call_args_list[1].kwargs['env']
        assert configured.get('BD_ORACLE_PORT') == '9333', {'configured_port': 9333,
                                                            'child_port_env': configured.get('BD_ORACLE_PORT')}
        assert default.get('BD_ORACLE_PORT') == str(oa.DEFAULT_ORACLE_PORT), default.get('BD_ORACLE_PORT')

    def test_unconfigured_host_makes_no_network_call(self, hermetic) -> None:
        adapter = oa.OracleAdvisoryAdapter()
        health = adapter.check_health()
        res = adapter.query('Review')
        multi = oa.query_multi_model_advisory('Review')
        assert hermetic['net'] == [], 'adapter dialled a default oracle host nobody configured'
        assert health['cdp_reachable'] is False and health['host'] is None
        assert res['verdict'] == NO_LOOK and multi['verdict'] == NO_LOOK

    def test_operator_pin_from_env_only(self, monkeypatch) -> None:
        assert not re.search(r'''['"]\d{4,}['"]''', ORACLE_PATH.read_text(encoding='utf-8')), 'PIN literal in source'
        assert oa.OracleAdvisoryAdapter(host=None).check_health()['pin_configured'] is False
        monkeypatch.setenv('BD_ORACLE_PIN', 'pin-from-env')
        adapter = oa.OracleAdvisoryAdapter(host=None)
        assert adapter.pin == 'pin-from-env' and adapter.check_health()['pin_configured'] is True

    def test_cli_query_passes_pin_by_env_not_argv(self, tmp_path) -> None:
        fake_bin = tmp_path / 'bd-oracle'
        fake_bin.write_text('#!/bin/sh\n', encoding='utf-8')
        fake_bin.chmod(0o755)
        done = subprocess.CompletedProcess([], 0, stdout=json.dumps({'gemini': 'REFUTE: x.py:3 broken'}), stderr='')
        with mock.patch('subprocess.run', return_value=done) as run:
            res = oa.query_bd_oracle_cli('p', oracle='gemini', host='oracle.invalid', pin='pin-x',
                                         oracle_bin=fake_bin)
        cmd, env = run.call_args[0][0], run.call_args[1]['env']
        assert 'pin-x' not in cmd and env['OPERATOR_AUTH_PIN'] == 'pin-x'
        assert res['verdict'] == 'REFUTE' and res['findings'] == ['x.py:3 broken']

    def test_cli_query_unconfigured_bin_could_not_look(self, hermetic) -> None:
        res = oa.query_bd_oracle_cli('p', host='oracle.invalid')
        assert hermetic['proc'] == [] and res['verdict'] == NO_LOOK and 'BD_ORACLE_BIN' in res['error']

    def test_is_cdp_reachable_controls(self) -> None:
        with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('refused')):
            assert oa.is_cdp_reachable(host='oracle.invalid', port=9222, timeout=0.2) is False
        with mock.patch('urllib.request.urlopen', return_value=_fake_resp({}, status=200)):
            assert oa.is_cdp_reachable(host='oracle.invalid', port=9222, timeout=0.2) is True

    def test_cli_rejects_pin_flag(self, capsys) -> None:
        with pytest.raises(SystemExit) as exc:
            oa.main(['--health', '--pin', 'x'])
        assert exc.value.code == 2


@pytest.mark.parametrize('path, args, stdin, expect', [(ORACLE_PATH, ['--health'], '', '"status": "UNCONFIGURED"'), (PREFILTER_PATH, ['--task-type', 'test'], '....\n17 passed in 0.35s\n', '"status": "PASS"')])
def test_executable_entrypoints_unconfigured(path, args, stdin, expect) -> None:
    assert path.read_text(encoding='utf-8').startswith('#!/usr/bin/env python3\n')
    assert os.access(path, os.X_OK), f'{path.name} not executable'
    env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    proc = _REAL_RUN([str(path), *args], input=stdin, capture_output=True, text=True, env=env, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert expect in proc.stdout, proc.stdout
CLI_PATH = Path(os.environ.get('BD_ORACLE_CLI_CANDIDATE') or HERE.parent / 'bd-oracle' if (HERE.parent / 'bd-oracle').exists() else _HARNESS_ROOT / 'bd-oracle' if (_HARNESS_ROOT / 'bd-oracle').exists() else Path('/home/mboyle/bin/bd-oracle'))

def _load_cli():
    if 'bd_oracle_cli' in sys.modules:
        return sys.modules['bd_oracle_cli']
    import importlib.machinery
    fake_ws = mock.MagicMock()
    sys.modules.setdefault('websocket', fake_ws)
    loader = importlib.machinery.SourceFileLoader('bd_oracle_cli', str(CLI_PATH))
    spec = importlib.util.spec_from_loader('bd_oracle_cli', loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['bd_oracle_cli'] = mod
    loader.exec_module(mod)
    return mod

class TestBdOraclePortAndMarkdown:

    def test_get_tabs_default_port_is_9222(self) -> None:
        cli = _load_cli()
        with mock.patch('urllib.request.urlopen', return_value=_fake_resp([{'type': 'page', 'url': 'https://gemini.google.com/app'}])) as m:
            tabs = cli.get_tabs('oracle.invalid', silent=True)
        assert len(tabs) == 1
        assert m.call_args[0][0] == 'http://oracle.invalid:9222/json/list'

    def test_get_tabs_honours_bd_oracle_port_env(self, monkeypatch) -> None:
        cli = _load_cli()
        monkeypatch.setenv('BD_ORACLE_PORT', '9333')
        with mock.patch('urllib.request.urlopen', return_value=_fake_resp([{'type': 'page', 'url': 'https://gemini.google.com/app'}])) as m:
            tabs = cli.get_tabs('oracle.invalid', silent=True)
        assert len(tabs) == 1
        assert m.call_args[0][0] == 'http://oracle.invalid:9333/json/list', {'BD_ORACLE_PORT': '9333', 'requested_url': m.call_args[0][0]}

    def test_get_tabs_explicit_port_overrides_env(self, monkeypatch) -> None:
        cli = _load_cli()
        monkeypatch.setenv('BD_ORACLE_PORT', '9444')
        with mock.patch('urllib.request.urlopen', return_value=_fake_resp([{'type': 'page', 'url': 'https://gemini.google.com/app'}])) as m:
            tabs = cli.get_tabs('oracle.invalid', silent=True, port=9333)
        assert len(tabs) == 1
        assert m.call_args[0][0] == 'http://oracle.invalid:9333/json/list'

    def test_cli_main_accepts_port_flag_and_routes_to_configured_port(self, monkeypatch, capsys) -> None:
        cli = _load_cli()
        urls = []

        def fake_urlopen(url, timeout=2.0):
            urls.append(url)
            return _fake_resp([{'type': 'page', 'url': 'https://gemini.google.com/app', 'webSocketDebuggerUrl': 'ws://oracle.invalid:9333/devtools/page/1'}])
        with mock.patch('urllib.request.urlopen', side_effect=fake_urlopen), mock.patch.object(cli, 'query_gemini', return_value='BOARD: ok'), mock.patch.object(sys, 'argv', ['bd-oracle', 'check patch', '--oracle', 'gemini', '--host', 'oracle.invalid', '--port', '9333', '--json']):
            cli.main()
        out = json.loads(capsys.readouterr().out)
        assert out == {'gemini': 'BOARD: ok'}
        assert urls == ['http://oracle.invalid:9333/json/list']

    @pytest.mark.parametrize('text, expected', [('_BOARD_: clean', 'BOARD'), ('__BOARD__: clean', 'BOARD'), ('_VERDICT: REFUTE_\n- a.py:10 broken', 'REFUTE'), ('__VERDICT: BOARD__', 'BOARD')])
    def test_underscore_markdown_verdict_forms_supported(self, text, expected) -> None:
        res = oa.parse_oracle_verdict(text)
        assert res['verdict'] == expected, {'text': text, 'actual': res['verdict'], 'expected': expected}

    def test_handle_credential_and_pin_prompts_honours_env_and_skips_empty_pin(self, monkeypatch) -> None:
        cli = _load_cli()
        captured: list[str] = []
        def fake_cdp_eval_js(ws, expr, req_id=90):
            captured.append(expr)
            return False
        monkeypatch.setattr(cli, 'cdp_eval_js', fake_cdp_eval_js)
        monkeypatch.delenv('BD_ORACLE_AUTH_PIN', raising=False)
        monkeypatch.delenv('OPERATOR_AUTH_PIN', raising=False)
        monkeypatch.delenv('BD_ORACLE_PIN', raising=False)
        monkeypatch.setattr(cli, 'OPERATOR_AUTH_PIN', '')
        assert cli.handle_credential_and_pin_prompts(object()) is False
        assert 'const pinVal = "";' in captured[-1]
        assert 'if (pinVal)' in captured[-1]
        monkeypatch.setenv('BD_ORACLE_AUTH_PIN', '000000-test')
        assert cli.handle_credential_and_pin_prompts(object()) is False
        assert 'const pinVal = "000000-test";' in captured[-1]

    def test_sentinel_standardization_enforced(self) -> None:
        content = Path(__file__).read_text(encoding='utf-8')
        assert "000000-test" in content, "Synthetic test sentinel '000000-test' must be present"
        forbidden_pin = f"{628}{895}"
        prefix_content = content[:content.rfind("def test_sentinel_standardization_enforced")]
        assert forbidden_pin not in prefix_content, "Real operator PIN literal must never appear in test fixtures"

