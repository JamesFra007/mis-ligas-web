from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
import unittest
from unittest.mock import patch
from uuid import UUID
from app import poland as p

IDENT='64038d89-b0b3-4f04-9c18-fd9df7c01d1f'
NOW=datetime(2026,11,9,tzinfo=timezone.utc)

def raw(**updates):
    return {'matchId':IDENT,'status':'played','homeTeam':{'name':'Legia Warszawa','clubSlug':'legia-warszawa'},'awayTeam':{'name':'Lech Poznań','clubSlug':'lech-poznan'},'date':'2026-11-06','localTime':'18:00:00','matchDatetime':'2026-11-06T18:00:00+01:00','homeScore':3,'awayScore':2,'week':14,**updates}


def event(ident,kind='goal',period=1,team='home',subtype='goal',decision=None):
    return {'eventId':ident,'matchId':IDENT,'eventType':kind,'period':period,'teamType':team,'eventSubtype':subtype,'decision':decision}


def complete_events():
    return [event('end1','end',1),event('end2','end',2),event('h1',period=1),event('a1',period=1,team='away'),event('a2',period=1,team='away'),event('h2',period=2),event('h3',period=2)]


def flight(value):
    payload='0:'+json.dumps(value,ensure_ascii=False)+'\n'
    return '<script>self.__next_f.push('+json.dumps([1,payload],ensure_ascii=False)+')</script>'


def report(match=None,events=None):
    return flight({'initialMatch':match or raw(),'initialEvents':complete_events() if events is None else events})


class PolandProviderTests(unittest.TestCase):
    def test_calendar_scores_and_warsaw_bogota_timezone(self):
        g=p.parse_match(raw(),'2026-27',now=NOW)
        self.assertEqual((g['id'],g['provider_id']),('poland:ek:'+IDENT,'ek:'+IDENT))
        self.assertEqual((g['date'],g['time'],g['kickoff']),('2026-11-06','12:00','2026-11-06T17:00:00+00:00'))
        self.assertEqual((g['home'],g['away']),('Legia Varsovia','Lech Poznań'))
        self.assertEqual((g['ft_home'],g['ft_away']),(3,2));self.assertIsNone(g['ht_home'])
        self.assertTrue(all(g[k] is None for k in ('corners','yellow','red','yellow_home','yellow_away','red_home','red_away')))

    def test_future_zero_placeholders_are_not_results(self):
        g=p.parse_match(raw(status='fixture',date='2026-11-20',matchDatetime='2026-11-20T18:00:00+01:00',homeScore=0,awayScore=0),'2026-27',now=NOW)
        self.assertEqual(g['status'],'scheduled');self.assertIsNone(g['ft_home']);self.assertIsNone(g['ft_away'])
        erroneous=p.parse_match(raw(date='2026-11-20',matchDatetime='2026-11-20T18:00:00+01:00'),'2026-27',now=NOW)
        self.assertEqual(erroneous['status'],'scheduled');self.assertIsNone(erroneous['ft_home'])

    def test_unpublished_and_withdrawn_times_remain_unknown(self):
        g=p.parse_match(raw(status='fixture',date='2027-05-22',localTime='None',matchDatetime=None),'2026-27',now=NOW)
        self.assertIsNone(g['time']);self.assertIsNone(g['kickoff']);self.assertFalse(g['time_confirmed']);self.assertFalse(g['date_confirmed'])
        withdrawn=p.parse_match(raw(status='fixture',timeConfirmed=False),'2026-27',now=NOW)
        self.assertIsNone(withdrawn['time']);self.assertIsNone(withdrawn['kickoff'])
        placeholder=p.parse_match(raw(status='fixture',date='2027-05-22',localTime='None',matchDatetime='2027-05-22T00:00:00+02:00'),'2026-27',now=NOW)
        self.assertIsNone(placeholder['time']);self.assertIsNone(placeholder['kickoff']);self.assertFalse(placeholder['date_confirmed'])
        midnight=p.parse_match(raw(status='fixture',date='2027-05-22',localTime='00:00:00',matchDatetime='2027-05-22T00:00:00+02:00'),'2026-27',now=NOW)
        self.assertEqual(midnight['time'],'17:00');self.assertTrue(midnight['time_confirmed'])

    def test_summer_dst_is_converted_from_the_published_offset(self):
        g=p.parse_match(raw(date='2026-08-01',matchDatetime='2026-08-01T20:00:00+02:00',localTime='20:00:00'),'2026-27',now=NOW)
        self.assertEqual(g['time'],'13:00')

    def test_complete_official_period_log_gives_observed_half_time(self):
        self.assertEqual(p.half_time_from_events(raw(),complete_events()),(1,2))
        g=p.parse_match(raw(),'2026-27',now=NOW)
        enriched=p.enrich_match(g,report());self.assertEqual((enriched['ht_home'],enriched['ht_away']),(1,2));self.assertIsNone(g['ht_home'])

    def test_incomplete_or_wrong_goal_tally_is_not_zero(self):
        events=complete_events()
        self.assertIsNone(p.half_time_from_events(raw(),events[:-1]))
        self.assertIsNone(p.half_time_from_events(raw(),[e for e in events if e['eventId']!='end1']))
        self.assertIsNone(p.half_time_from_events(raw(),[e for e in events if e['eventId']!='end2']))
        self.assertIsNone(p.half_time_from_events(raw(),[]))
        self.assertIsNone(p.half_time_from_events(raw(),None))

    def test_genuine_zero_zero_requires_both_completed_period_markers(self):
        match=raw(homeScore=0,awayScore=0)
        self.assertEqual(p.half_time_from_events(match,[event('e1','end',1),event('e2','end',2)]),(0,0))
        self.assertIsNone(p.half_time_from_events(match,[event('e1','end',1)]))

    def test_cancelled_goals_and_duplicate_event_records_do_not_count(self):
        events=complete_events()+[event('var',team='away',decision='disallowed'),event('h1',period=1)]
        self.assertEqual(p.half_time_from_events(raw(),events),(1,2))
        events.append(event('unclear',decision='pending'))
        self.assertIsNone(p.half_time_from_events(raw(),events))

    def test_conflicting_goal_decisions_cannot_depend_on_event_order(self):
        for events in (complete_events()+[event('h1',decision='disallowed')], [event('h1',decision='disallowed')]+complete_events()):
            self.assertIsNone(p.half_time_from_events(raw(),events))
        self.assertIsNone(p.half_time_from_events(raw(),complete_events()+[event('h1',period=2)]))

    def test_own_goal_side_is_only_accepted_when_the_result_disambiguates(self):
        match=raw(homeScore=1,awayScore=0)
        events=[event('e1','end',1),event('e2','end',2),event('og',team='away',subtype='own_goal')]
        self.assertEqual(p.half_time_from_events(match,events),(1,0))
        ambiguous=raw(homeScore=1,awayScore=1)
        events=[event('e1','end',1),event('e2','end',2),event('og1',period=1,team='home',subtype='own_goal'),event('og2',period=2,team='away',subtype='own_goal')]
        self.assertIsNone(p.half_time_from_events(ambiguous,events))

    def test_other_match_widgets_cannot_supply_period_scores(self):
        events=[{**e,'matchId':'different'}for e in complete_events()]
        self.assertIsNone(p.half_time_from_events(raw(),events))
        g=p.parse_match(raw(),'2026-27',now=NOW)
        self.assertIsNone(p.enrich_match(g,report(match=raw(matchId='different')))['ht_home'])

    def test_explicit_half_time_preferred_and_administrative_is_excluded(self):
        g=p.parse_match(raw(),'2026-27',now=NOW)
        direct=p.enrich_match(g,report(match=raw(homeHalfTimeScore=2,awayHalfTimeScore=1),events=[]))
        self.assertEqual((direct['ht_home'],direct['ht_away']),(2,1))
        self.assertIsNone(p.enrich_match({**g,'administrative':True},report())['ht_home'])
        live=p.parse_match(raw(status='live'),'2026-27',now=NOW);self.assertIsNone(live['ft_home']);self.assertIsNone(p.enrich_match(live,report())['ht_home'])

    def test_calendar_structure_is_checked(self):
        props={'seasonSlug':'2026-2027','totalRounds':34,'weekNumber':14,'enrichedMatches':[raw(matchId=str(UUID(int=i+1)))for i in range(9)]}
        rows,_=p.parse_calendar(flight(props),'2026-27');self.assertEqual(len(rows),9)
        with self.assertRaises(p.ProviderError):p.parse_calendar(flight({**props,'enrichedMatches':props['enrichedMatches'][:8]}),'2026-27')
        with self.assertRaises(p.ProviderError):p.parse_calendar(flight({**props,'seasonSlug':'2025-2026'}),'2026-27')
        with self.assertRaises(p.ProviderError):p.parse_calendar('<html>unavailable</html>','2026-27')
        with self.assertRaises(p.ProviderError):p.parse_match(raw(),'2026-99')

    def test_partial_calendar_fetch_never_returns_a_silent_partial_import(self):
        with patch('app.poland._html',side_effect=p.ProviderError('unavailable')):
            with self.assertRaises(p.ProviderError):p.sync_games('2026-27')
        self.assertFalse(p.get_coverage()['fixtures_available'])

    def test_sync_retries_old_pending_reports_without_duplicate_recent_requests(self):
        # Complete fixture metadata is local; no source requests leave this test.
        fixtures=[]
        for home in range(18):
            for away in range(18):
                if home==away:continue
                index=len(fixtures);rnd=index//9+1;ident=str(UUID(int=index+1))
                fixtures.append({'id':'poland:ek:'+ident,'official_id':ident,'home':str(home),'away':str(away),'round':rnd,'date':(datetime(2026,7,1)+timedelta(days=rnd)).date().isoformat(),'time':'12:00','source_url':p.BASE+'/mecz/'+ident+'/','status':'finished','ht_home':None,'ht_away':None})
        fixtures[2].update(status='scheduled',time_confirmed=False)
        fixtures[3]['administrative']=True
        latest=sorted((g for g in fixtures if g['status']=='finished' and not g.get('administrative')),key=lambda g:(g['date'],g['time']),reverse=True)[:40]
        pending=[fixtures[0]['id'],fixtures[1]['id'],latest[0]['id'],fixtures[2]['id'],fixtures[3]['id'],'poland:ek:not-in-calendar']
        def calendar(url,season):
            rnd=int(url.split('kolejka-')[1].split('/')[0])
            return [g.copy() for g in fixtures if g['round']==rnd],[]
        with patch('app.poland._html',side_effect=lambda url:url) as fetch,patch('app.poland.parse_calendar',side_effect=calendar),patch('app.poland.enrich_match',side_effect=lambda game,html:{**game,'ht_home':0,'ht_away':0}) as enrich:
            rows=p.sync_games('2026-27',pending_ids=pending)
        expected={g['id'] for g in latest}|set(pending[:2])
        self.assertEqual({call.args[0]['id'] for call in enrich.call_args_list},expected)
        self.assertEqual(enrich.call_count,42)
        self.assertEqual(fetch.call_count,34+42)
        self.assertEqual({g['id'] for g in rows if g['ht_home'] is not None},expected)

if __name__=='__main__':unittest.main()
