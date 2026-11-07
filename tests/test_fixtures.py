"""Fixture clocks, season boundaries and source reconciliation."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('fixture_providers',Path(__file__).resolve().parents[1]/'app/providers.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)


def fixture(ident='401900001',date='2026-11-08T20:00Z',time_valid=True,status='scheduled',home='86',away='83'):
    names={'86':'Real Madrid','83':'Barcelona','85':'Celta Vigo','88':'Espanyol','87':'Racing Santander','90':'Deportivo','99':'Málaga'}
    state={'name':'STATUS_SCHEDULED','state':'pre','completed':False,'detail':'Scheduled'}
    if status=='finished':state={'name':'STATUS_FULL_TIME','state':'post','completed':True,'detail':'FT'}
    if status=='live':state={'name':'STATUS_IN_PROGRESS','state':'in','completed':False,'detail':"30'"}
    return {'id':ident,'date':date,'season':{'year':2026,'slug':'2026-27-laliga'},'status':{'type':state},'competitions':[{'timeValid':time_valid,'competitors':[{'id':home,'homeAway':'home','team':{'displayName':names[home]},'score':'0','statistics':[]},{'id':away,'homeAway':'away','team':{'displayName':names[away]},'score':'0','statistics':[]}]}]}


class FixturesTests(unittest.TestCase):
    def test_confirmed_kickoff_is_utc_and_local_colombia(self):
        row=p._parse_espn_event(fixture(),2026,'spain')
        self.assertEqual((row['date'],row['time'],row['kickoff']),('2026-11-08','15:00','2026-11-08T20:00:00+00:00'))
        self.assertTrue(row['time_confirmed']);self.assertTrue(row['date_confirmed'])
        self.assertEqual(row['provider_id'],'espn:401900001')
        for field in ('ft_home','ft_away','ht_home','ht_away','corners','yellow','red'):self.assertIsNone(row[field])

    def test_tbd_is_not_a_real_kickoff_or_midnight(self):
        e=fixture(time_valid=False)
        row=p._parse_espn_event(e,2026,'spain')
        self.assertFalse(row['time_confirmed']);self.assertFalse(row['date_confirmed'])
        self.assertIsNone(row['time']);self.assertIsNone(row['kickoff'])
        e['competitions'][0]['timeValid']=True;e['status']['type']['detail']='TBD'
        self.assertIsNone(p._parse_espn_event(e,2026,'spain')['kickoff'])
        del e['competitions'][0]['timeValid'];e['status']['type']['detail']='Scheduled'
        self.assertFalse(p._parse_espn_event(e,2026,'spain')['time_confirmed'])

    def test_genuine_source_midnight_can_be_confirmed(self):
        row=p._parse_espn_event(fixture(date='2026-11-08T05:00Z'),2026,'spain')
        self.assertEqual(row['time'],'00:00');self.assertTrue(row['time_confirmed'])

    def test_season_boundaries_do_not_mix_calendar_years(self):
        self.assertIsNone(p._parse_espn_event(fixture(date='2026-06-20T20:00Z'),2026,'spain'))
        self.assertIsNotNone(p._parse_espn_event(fixture(date='2027-05-30T18:00Z'),2026,'spain'))
        self.assertIsNone(p._parse_espn_event(fixture(date='2027-07-03T18:00Z'),2026,'spain'))
        wrong=fixture();wrong['season']['slug']='2025-26-laliga'
        self.assertIsNone(p._parse_espn_event(wrong,2026,'spain'))

    def test_naive_or_invalid_clock_is_not_assumed_utc(self):
        self.assertIsNone(p._parse_espn_event(fixture(date='2026-11-08T20:00'),2026,'spain'))
        self.assertIsNone(p._parse_espn_event(fixture(date='not-a-date'),2026,'spain'))

    def test_aliases_match_existing_football_data_ids(self):
        for home,name in [('85','Celta'),('87','Santander'),('90','La Coruna'),('99','Malaga')]:
            with self.subTest(home=home):self.assertEqual(p._parse_espn_event(fixture(home=home),2026,'spain')['home'],name)

    def test_published_week_only_never_row_position(self):
        e=fixture();row=p._parse_espn_event(e,2026,'spain')
        self.assertIsNone(row['round']);self.assertEqual(row['scope'],'all')
        e['week']={'number':12};row=p._parse_espn_event(e,2026,'spain')
        self.assertEqual(row['round'],12);self.assertEqual(row['scope'],'first')
        e['week']['number']=26;self.assertEqual(p._parse_espn_event(e,2026,'spain')['scope'],'second')
        e['week']['number']=2005;self.assertIsNone(p._parse_espn_event(e,2026,'spain')['round'])

    def test_calendar_combines_two_years_and_deduplicates_ids(self):
        a=fixture(date='2026-11-08T20:00Z');b=fixture(ident='2',date='2027-05-30T18:00Z')
        old=fixture(ident='3',date='2026-05-30T18:00Z')
        def response(url):return {'events':[a,old]} if 'dates=2026' in url else {'events':[a,b]}
        with patch.object(p,'_json',side_effect=response):rows=p._spain_calendar(2026)
        self.assertEqual([r['provider_id'] for r in rows],['espn:401900001','espn:2'])

    def test_football_data_scores_and_card_totals_remain_authoritative(self):
        csv=b'Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,HTHG,HTAG,HC,AC,HY,AY,HR,AR\n08/11/2026,20:00,Celta,Espanol,2,1,1,0,4,3,2,1,0,0\n'
        complete=fixture(home='85',away='88',status='finished');complete['week']={'number':12}
        next_game=fixture(ident='next',date='2026-11-09T20:00Z')
        with patch.object(p,'_fetch',return_value=csv),patch.object(p,'_spain_calendar',return_value=[p._parse_espn_event(complete,2026,'spain'),p._parse_espn_event(next_game,2026,'spain')]):rows=p.sync_games('spain','2026-27')
        self.assertEqual(len(rows),2);first=rows[0]
        self.assertTrue(first['provider_id'].startswith('fd:'));self.assertEqual(first['espn_id'],'401900001')
        self.assertEqual((first['ft_home'],first['ft_away'],first['ht_home'],first['corners'],first['yellow'],first['red']),(2,1,1,7,3,0))
        self.assertEqual((first['round'],first['scope']),(12,'first'))
        self.assertEqual(rows[1]['status'],'scheduled');self.assertIsNone(rows[1]['ft_home'])

    def test_calendar_error_keeps_csv_and_reports_coverage(self):
        csv=b'Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,HTHG,HTAG\n08/11/2026,20:00,Celta,Espanol,2,1,1,0\n'
        with patch.object(p,'_fetch',return_value=csv),patch.object(p,'_spain_calendar',side_effect=p.ProviderError('offline')):rows=p.sync_games('spain','2026-27')
        self.assertEqual(rows[0]['ft_home'],2);self.assertFalse(p.get_coverage('spain')['fixtures_available']);self.assertTrue(p.get_coverage('spain')['warnings'])

    def test_new_season_fixtures_work_before_csv_results_exist(self):
        calendar=[p._parse_espn_event(fixture(),2026,'spain')]
        with patch.object(p,'_football_data',side_effect=p.ProviderError('not yet published')),patch.object(p,'_spain_calendar',return_value=calendar):rows=p.sync_games('spain','2026-27')
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'scheduled')
        self.assertTrue(rows[0]['time_confirmed']);self.assertTrue(p.get_coverage('spain')['warnings'])

    def test_placeholder_csv_fixture_can_become_finished_from_espn(self):
        csv=b'Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,HTHG,HTAG\n08/11/2026,20:00,Celta,Espanol,,,,\n'
        complete=p._parse_espn_event(fixture(home='85',away='88',status='finished'),2026,'spain')
        with patch.object(p,'_fetch',return_value=csv),patch.object(p,'_spain_calendar',return_value=[complete]),patch.object(p,'_enrich_espn',side_effect=lambda game,*args:game):rows=p.sync_games('spain','2026-27')
        self.assertEqual(len(rows),1);self.assertTrue(rows[0]['id'].startswith('spain:2627:'))
        self.assertEqual((rows[0]['status'],rows[0]['ft_home'],rows[0]['ft_away']),('finished',0,0))

    def test_spain_summary_adds_real_ht_and_zero_red_cards(self):
        row=p._parse_espn_event(fixture(status='finished'),2026,'spain');row['ft_home']=2;row['ft_away']=1
        summary={'header':{'competitions':[{'competitors':[{'id':'86','homeAway':'home','linescores':[{'period':1,'displayValue':'1'}]},{'id':'83','homeAway':'away','linescores':[{'period':1,'displayValue':'0'}]}]}]},'boxscore':{'teams':[{'team':{'id':ident},'statistics':[{'name':'possessionPct','displayValue':'50'},{'name':'wonCorners','displayValue':'3'},{'name':'yellowCards','displayValue':'2'},{'name':'redCards','displayValue':'0'}]} for ident in ['86','83']]}}
        with patch.object(p,'_json',return_value=summary):p._enrich_espn(row,p.SPAIN_ESPN_BASE)
        self.assertEqual((row['ht_home'],row['ht_away'],row['corners'],row['yellow'],row['red']),(1,0,6,4,0))

    def test_cancelled_fixture_not_reported_as_postponed(self):
        self.assertEqual(p._espn_status({'name':'STATUS_CANCELED','state':'pre','completed':False}),'cancelled')


if __name__=='__main__':unittest.main()
