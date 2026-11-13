"""The chart series: sampled, bounded, honest about gaps."""

import json

import pytest

from mt5trader.history import SpreadHistory, with_zscore


def md(spread):
    return {'spread': spread, 'long_spread': spread + 1,
            'short_spread': spread - 1, 'leg_a_mid': 100.0,
            'leg_b_mid': 100.0 + spread, 'spread_sigma': None}


def test_samples_at_most_once_a_second():
    h = SpreadHistory(sample_sec=1.0)
    assert h.observe('p', md(1.0), now=10.0)
    assert not h.observe('p', md(2.0), now=10.5)
    assert h.observe('p', md(3.0), now=11.0)
    assert [r['spread'] for r in h.series('p')] == [1.0, 3.0]


def test_a_dark_second_is_a_gap_not_a_zero():
    h = SpreadHistory()
    h.observe('p', None, now=1.0)
    assert h.series('p')[0]['spread'] is None


def test_it_keeps_only_the_last_hour():
    h = SpreadHistory(keep=5)
    for t in range(10):
        h.observe('p', md(float(t)), now=float(t))
    assert [r['spread'] for r in h.series('p')] == [5.0, 6.0, 7.0, 8.0, 9.0]


def test_no_zscore_until_there_are_thirty_prices():
    rows = [{'t': i, 'spread': float(i % 3)} for i in range(29)]
    assert all(r['z'] is None for r in with_zscore(rows))
    rows = [{'t': i, 'spread': float(i % 3)} for i in range(40)]
    assert with_zscore(rows)[-1]['z'] is not None


def test_a_gap_keeps_its_gap_through_the_zscore():
    rows = [{'t': i, 'spread': float(i % 5)} for i in range(40)]
    rows.append({'t': 40, 'spread': None})
    assert with_zscore(rows)[-1]['z'] is None


def test_the_api_serves_one_pairs_series_with_its_zscore(tmp_path):
    from mt5trader.webapp import create_app
    status = tmp_path / 'status.json'
    status.write_text('{}')
    h = SpreadHistory()
    for t in range(40):
        h.observe('A|B', md(float(t % 7)), now=float(t))
    (tmp_path / 'history.json').write_text(json.dumps(h.payload()))
    client = create_app(str(status), str(tmp_path / 'c.jsonl'),
                        str(tmp_path / 'r.json'),
                        str(tmp_path / 'config.json')).test_client()
    body = client.get('/api/history/A|B').get_json()
    assert len(body['points']) == 40 and body['points'][-1]['z'] is not None
    newer = client.get('/api/history/A|B?since=35').get_json()['points']
    assert [p['t'] for p in newer] == [36.0, 37.0, 38.0, 39.0]
    assert client.get('/api/history/nope').get_json()['points'] == []
