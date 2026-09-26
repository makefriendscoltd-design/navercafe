import json
import sys

import pytest

from aside_mcp import AsideMCP, AsideMCPError, AsideMCPTimeout


@pytest.fixture
def fake_aside(tmp_path):
    executable = tmp_path / "aside"
    executable.write_text(f"#!{sys.executable}\n" + r'''
import json, sys, time
assert sys.argv[1:] == ['mcp', '--account', 'u0']
count = 0
def send(message):
    print(json.dumps(dict(jsonrpc='2.0', **message)), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    with open('requests.jsonl', 'a') as log:
        log.write(json.dumps(request) + '\n')
    method = request['method']
    if method == 'initialize':
        assert request['params']['protocolVersion'] == '2024-11-05'
        send({'id': request['id'], 'result': {'protocolVersion': '2024-11-05'}})
    elif method == 'notifications/initialized':
        continue
    else:
        assert method == 'tools/call'
        assert request['params']['name'] == 'repl'
        count += 1
        code = request['params']['arguments']['code']
        if code == 'timeout':
            time.sleep(30)
        elif code == 'eof':
            break
        elif code == 'wrong-id':
            send({'id': request['id'] + 1, 'result': {}})
        elif code == 'invalid-json':
            print('not json', flush=True)
        elif code == 'protocol-error':
            send({'id': request['id'], 'error': {'code': -32603, 'message': 'fixture error'}})
        elif code == 'tool-error':
            send({'id': request['id'], 'result': {'isError': True,
                 'content': [{'type': 'text', 'text': 'fixture tool error'}]}})
        else:
            send({'method': 'notifications/message', 'params': {'level': 'info'}})
            send({'id': request['id'], 'result': {'content': [
                {'type': 'text', 'text': str(count)},
                {'type': 'image', 'data': 'ignored'},
                {'type': 'text', 'text': code}]}})
''')
    executable.chmod(0o700)
    return executable


def test_persistent_repl_calls_and_owned_cleanup(fake_aside, tmp_path):
    with AsideMCP(str(fake_aside), cwd=tmp_path) as client:
        process = client._process
        assert client.repl('first', title='test') == '1\nfirst'
        assert client.repl('second') == '2\nsecond'
        assert process.poll() is None
    assert process.poll() is not None
    client.close()  # Idempotent, including after context exit.
    requests = [json.loads(line) for line in (tmp_path / 'requests.jsonl').read_text().splitlines()]
    assert [r['method'] for r in requests] == [
        'initialize', 'notifications/initialized', 'tools/call', 'tools/call']
    assert [r['id'] for r in requests if 'id' in r] == [1, 2, 3]


@pytest.mark.parametrize(('code', 'error', 'match'), [
    ('timeout', AsideMCPTimeout, 'timed out'),
    ('eof', AsideMCPError, 'EOF'),
    ('wrong-id', AsideMCPError, 'ID does not match'),
    ('invalid-json', AsideMCPError, 'invalid JSON'),
    ('protocol-error', AsideMCPError, 'protocol error'),
    ('tool-error', AsideMCPError, 'tool failed'),
])
def test_errors_never_retry(fake_aside, tmp_path, code, error, match):
    with AsideMCP(str(fake_aside), cwd=tmp_path) as client:
        with pytest.raises(error, match=match):
            client.repl(code, timeout=0.15 if code == 'timeout' else 3)
    requests = [json.loads(line) for line in (tmp_path / 'requests.jsonl').read_text().splitlines()]
    assert len([r for r in requests if r['method'] == 'tools/call']) == 1
    assert client._process.poll() is not None


def test_rejects_other_accounts_without_starting_a_process(tmp_path):
    with pytest.raises(ValueError, match='u0'):
        AsideMCP(str(tmp_path / 'does-not-exist'), account='u1')
