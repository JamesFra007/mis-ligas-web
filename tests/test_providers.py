"""Tests for provider boundaries where missing values previously caused errors."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / 'app' / 'providers.py'
spec = importlib.util.spec_from_file_location('providers_under_test', path)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def event(status='scheduled'):
    state = {'name': 'STATUS_SCHEDULED', 'state': 'pre', 'completed': False}
    if status == 'finished':
        state = {'name': 'STATUS_FULL_TIME', 'state': 'post', 'completed': True}
    if status == 'live':
        state = {'name': 'STATUS_IN_PROGRESS', 'state': 'in', 'completed': False}
    return {'id': '401999001', 'date': '2026-11-07T01:00Z', 'season': {'slug': 'clausura'}, 'status': {'type': state}, 'competitions': [{'timeValid': True, 'competitors': [{'id':'4815','homeAway':'home','score':'0','team':{'displayName':'Atlético Junior'},'statistics':[]}, {'id':'5480','homeAway':'away','score':'0','team':{'displayName':'Boyaca Chico'},'statistics':[]}]}]}


class ProviderTests(unittest.TestCase):
    def test_scheduled_placeholders_are_not_zero_scores(self):
        row = p._parse_espn_event(event(), 2026)
        self.assertEqual(row['status'], 'scheduled')
        for k in ['ft_home', 'ft_away', 'ht_home', 'ht_away', 'corners', 'yellow', 'red']:
            self.assertIsNone(row[k])
        self.assertEqual(row['date'], '2026-11-06')
        self.assertEqual(row['time'], '20:00')
        self.assertEqual(row['home'], 'Junior')
        self.assertEqual(row['scope'], 'Finalización')
        self.assertIsNone(row['round'])

    def test_live_is_never_counted_as_full_time(self):
        row = p._parse_espn_event(event('live'), 2026)
        self.assertEqual(row['status'], 'live')
        self.assertIsNone(row['ft_home'])

    def test_real_completed_zero_is_a_number(self):
        row = p._parse_espn_event(event('finished'), 2026)
        self.assertEqual((row['ft_home'], row['ft_away']), (0, 0))
        self.assertIsNone(row['ht_home'])

    def test_placeholder_stat_blocks_remain_missing(self):
        c = {'statistics': [{'name': 'wonCorners', 'displayValue': '0'}, {'name':'totalShots','displayValue':'0'}]}
        self.assertIsNone(p._stats(c)['corners'])
        c['statistics'].append({'name':'possessionPct','displayValue':'53.2'})
        self.assertEqual(p._stats(c)['corners'], 0)

    def test_unknown_sides_do_not_fabricate_match(self):
        e = event()
        e['competitions'][0]['competitors'].pop()
        self.assertIsNone(p._parse_espn_event(e, 2026))

    def test_untrusted_or_invalid_season_rejected(self):
        for league, season in [('spain','2026-99'),('spain','../2627'),('colombia','2026&foo=x'),('other','2026')]:
            with self.subTest(league=league, season=season), self.assertRaises(p.ProviderError):
                p.sync_games(league, season)

    def test_spain_missing_rows_keep_null_and_no_invented_round(self):
        data = b'Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,HTHG,HTAG,HC,AC,HY,AY,HR,AR\n15/08/2026,20:00,Ath Bilbao,Betis,1,0,0,0,0,2,1,2,0,0\n16/08/2026,20:00,Celta,Espanol,,,,,,,,,,\n'
        with patch.object(p, '_fetch', return_value=data):
            rows = p.sync_games('spain', '2026-27')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['home'], 'Athletic Club')
        self.assertEqual(rows[0]['away'], 'Real Betis')
        self.assertEqual(rows[0]['corners'], 2)
        self.assertEqual(rows[0]['red'], 0)
        self.assertIsNone(rows[0]['round'])
        self.assertEqual(rows[0]['scope'], 'all')
        self.assertEqual(rows[1]['status'], 'scheduled')
        self.assertIsNone(rows[1]['corners'])

    def test_spain_invalid_ht_does_not_pollute_statistics(self):
        data = b'Date,HomeTeam,AwayTeam,FTHG,FTAG,HTHG,HTAG\n15/08/2026,Celta,Betis,1,0,2,0\n'
        with patch.object(p, '_fetch', return_value=data):
            row = p.sync_games('spain', '2026-27')[0]
        self.assertEqual(row['ft_home'], 1)
        self.assertIsNone(row['ht_home'])
        self.assertIsNone(row['ht_away'])

    def test_summary_ht_is_period_one_and_invalid_is_not_accepted(self):
        row = p._parse_espn_event(event('finished'), 2026)
        row['ft_home'], row['ft_away'] = 0, 3
        summary = {'header': {'competitions': [{'competitors': [{'id':'4815','homeAway':'home','linescores':[{'displayValue':'0'},{'displayValue':'0'}]}, {'id':'5480','homeAway':'away','linescores':[{'displayValue':'1'},{'displayValue':'2'}]}]}]}}
        with patch.object(p, '_json', return_value=summary):
            got = p._enrich_espn(row)
        self.assertEqual((got['ht_home'], got['ht_away']), (0, 1))
        bad = copy.deepcopy(summary)
        bad['header']['competitions'][0]['competitors'][0]['linescores'][0]['displayValue'] = '8'
        row['ht_home'] = row['ht_away'] = None
        with patch.object(p, '_json', return_value=bad):
            got = p._enrich_espn(row)
        self.assertIsNone(got['ht_home'])

    def test_colombia_repeated_pairings_keep_distinct_event_ids(self):
        a, b = event(), event()
        b['id'] = '401999002'
        self.assertNotEqual(p._parse_espn_event(a, 2026)['id'], p._parse_espn_event(b, 2026)['id'])

    def test_bad_csv_cannot_erase_history(self):
        with patch.object(p, '_fetch', return_value=b'<html>Not found</html>'), self.assertRaises(p.ProviderError):
            p.sync_games('spain','2026-27')

    def test_known_administrative_game_excluded(self):
        e = event('finished')
        e['id'] = '730479'
        row = p._parse_espn_event(e, 2025)
        self.assertTrue(row['administrative'])
        self.assertIsNone(row['ht_home'])
        self.assertIsNone(row['corners'])


if __name__ == '__main__':
    unittest.main()
