from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from app import italy as p
from app.italy_names import normalize_team


HEADER='Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,HTHG,HTAG,HC,AC,HY,AY,HR,AR\n'


def event(ident='1001',home='Internazionale',away='AC Milan',status='scheduled'):
    states={'scheduled':{'name':'STATUS_SCHEDULED','state':'pre','completed':False},
            'finished':{'name':'STATUS_FULL_TIME','state':'post','completed':True},
            'live':{'name':'STATUS_IN_PROGRESS','state':'in','completed':False}}
    return {'id':ident,'date':'2026-08-15T18:00Z','season':{'slug':'2026-27-italian-serie-a'},
            'status':{'type':states[status]},'competitions':[{'timeValid':True,'competitors':[
                {'id':'1','homeAway':'home','score':'0','team':{'displayName':home}},
                {'id':'2','homeAway':'away','score':'0','team':{'displayName':away}},
            ]}]}


def schedule():
    teams=[f'Team{i:02d}' for i in range(20)];first=[]
    for rnd in range(1,20):
        for i in range(10):
            home,away=teams[i],teams[19-i]
            if rnd%2==0:home,away=away,home
            first.append((home,away,rnd))
        teams=[teams[0],teams[-1],*teams[1:-1]]
    return first+[(away,home,rnd+19) for home,away,rnd in first]


def round_html(records,season='2026/2027'):
    rows=[];previous=None
    for home,away,rnd in records:
        if rnd!=previous:rows.append(f'<tr><th>{rnd}. Round</th></tr>');previous=rnd
        rows.append(f'<tr><td>15.08.</td><td><a href="/match/{home}-{away}/" class="in-match"><span>{home}</span> - <span>{away}</span></a></td></tr>')
    return f'<meta name="keywords" content="Serie A {season}, results"><table class="table-main">'+''.join(rows)+'</table>'


def saved_calendar():
    rows=[]
    for i,(home,away,rnd) in enumerate(schedule()):
        row=p.parse_espn_event(event(str(i+1),home,away),'2026-27')
        row.update(round=rnd,round_source='BetExplorer · tabla verificada',round_source_url='https://www.betexplorer.com/football/italy/serie-a/results/')
        rows.append(row)
    return rows


class ItalyProviderTests(unittest.TestCase):
    def test_coordinator_forwards_only_same_season_regular_snapshot(self):
        from app import db, providers
        saved=saved_calendar()
        playoff={**saved[0],'phase':'Desempate por la permanencia','round':None}
        with patch.object(db,'DB_PATH') as path,patch.object(db,'games',return_value=saved+[playoff]) as load,patch.object(p,'sync_games',return_value=[]) as fetch:
            path.exists.return_value=True
            self.assertEqual(providers.sync_games('italy','2026-27'),[])
        load.assert_called_once_with('italy','2026-27')
        fetch.assert_called_once_with('2026-27',saved_rounds=saved)

    def test_verified_saved_season_avoids_archive_but_refreshes_scores_and_kickoffs(self):
        saved=saved_calendar()
        saved[0].update(ft_home=9,ft_away=9,time='00:00',kickoff='2026-08-15T05:00:00+00:00')
        fixtures=[p.parse_espn_event(event(str(i+1),home,away),'2026-27') for i,(home,away,_) in enumerate(schedule())]
        observed=p.parse_football_data(HEADER+'15/08/2026,20:00,Team00,Team19,1,0,0,0,0,2,1,2,0,0\n','2026-27')
        with patch.object(p,'football_data',return_value=observed),patch.object(p,'calendar',return_value=fixtures),patch.object(p,'round_calendar',side_effect=p.ProviderError('archive unavailable')) as archive:
            rows=p.sync_games('2026-27',saved_rounds=saved)
        archive.assert_not_called()
        first=next(g for g in rows if (g['home'],g['away'])==('Team00','Team19'))
        self.assertEqual((first['ft_home'],first['ft_away'],first['round'],first['time']),(1,0,1,'13:00'))
        self.assertEqual(p.get_coverage()['rounds_source'],'verified-season-snapshot')
        self.assertTrue(p.get_coverage()['fixtures_available'])

    def test_incomplete_or_other_season_snapshot_requires_a_live_archive(self):
        complete=saved_calendar()
        other=deepcopy(complete)
        for row in other:row['season']='2025-26'
        mapping=p.parse_round_tables([(round_html(schedule()),'https://www.betexplorer.com/results/')],'2026-27')
        for bad in (complete[:-1],other):
            with self.subTest(size=len(bad),season=bad[0]['season']):
                fixtures=[p.parse_espn_event(event(str(i+1),home,away),'2026-27') for i,(home,away,_) in enumerate(schedule())]
                with patch.object(p,'football_data',return_value=[]),patch.object(p,'calendar',return_value=fixtures),patch.object(p,'round_calendar',return_value=mapping) as archive:
                    rows=p.sync_games('2026-27',saved_rounds=bad)
                archive.assert_called_once_with('2026-27')
                self.assertEqual(len(rows),380)
                self.assertEqual(p.get_coverage()['rounds_source'],'BetExplorer')

    def test_saved_calendar_rejects_duplicates_fixture_mismatch_and_unverified_rounds(self):
        original=saved_calendar();pairs={(g['home'],g['away']) for g in original}
        invalid=[]
        duplicate=deepcopy(original);duplicate[-1]=deepcopy(duplicate[0]);invalid.append(duplicate)
        different=deepcopy(original);different[0]['home']='Other team';invalid.append(different)
        boolean=deepcopy(original);boolean[0]['round']=True;invalid.append(boolean)
        no_source=deepcopy(original);no_source[0].pop('round_source_url');invalid.append(no_source)
        private_source=deepcopy(original);private_source[0]['round_source_url']='http://localhost/calendar';invalid.append(private_source)
        conflicting=deepcopy(original);conflicting[0]['round']=2;invalid.append(conflicting)
        for rows in invalid:
            with self.subTest(first=rows[0],last=rows[-1]):
                self.assertIsNone(p.verified_saved_rounds(rows,'2026-27',pairs))
        self.assertEqual(len(p.verified_saved_rounds(original,'2026-27',pairs)),380)

    def test_saved_calendar_does_not_hide_live_calendar_failure(self):
        with patch.object(p,'football_data',return_value=[]),patch.object(p,'calendar',side_effect=p.ProviderError('calendar unavailable')),patch.object(p,'round_calendar') as archive:
            with self.assertRaises(p.ProviderError):p.sync_games('2026-27',saved_rounds=saved_calendar())
        archive.assert_not_called()
        self.assertFalse(p.get_coverage()['fixtures_available'])

    def test_saved_rounds_cannot_override_a_contradictory_live_week(self):
        saved=saved_calendar();fixtures=deepcopy(saved);fixtures[0]['round']=2
        with patch.object(p,'football_data',return_value=[]),patch.object(p,'calendar',return_value=fixtures),patch.object(p,'round_calendar') as archive:
            with self.assertRaises(p.ProviderError):p.sync_games('2026-27',saved_rounds=saved)
        archive.assert_not_called()

    def test_shared_team_aliases_keep_fixture_identity(self):
        for alias,wanted in [('Internazionale','Inter'),('AC Milan','Milan'),('AS Roma','Roma'),('Hellas Verona','Verona'),('SPAL','Spal'),('Unknown Club','Unknown Club')]:
            self.assertEqual(normalize_team(alias),wanted)

    def test_csv_observed_zeroes_and_missing_sides_are_distinct(self):
        text=HEADER+'15/08/2026,20:00,Inter,Milan,1,0,0,0,0,2,1,2,0,0\n16/08/2026,20:00,Roma,Lazio,0,0,0,0,1,,0,,0,\n'
        rows=p.parse_football_data(text,'2026-27')
        self.assertEqual(rows[0]['id'],'italy:fd:2627:Inter:Milan')
        self.assertEqual((rows[0]['date'],rows[0]['time']),('2026-08-15','14:00'))
        self.assertEqual((rows[0]['corners'],rows[0]['yellow'],rows[0]['red']),(2,3,0))
        self.assertEqual((rows[0]['corners_home'],rows[0]['corners_away']),(0,2))
        self.assertIsNone(rows[1]['corners']);self.assertIsNone(rows[1]['yellow']);self.assertIsNone(rows[1]['red'])

    def test_csv_future_scores_and_statistics_are_not_observations(self):
        row=p.parse_football_data(HEADER+'15/08/2050,20:00,Inter,Milan,0,0,0,0,0,0,0,0,0,0\n','2050-51')[0]
        self.assertEqual(row['status'],'scheduled')
        for key in ('ht_home','ht_away','ft_home','ft_away','corners','yellow','red'):self.assertIsNone(row[key])

    def test_csv_covids_real_august_finish_and_missing_clock(self):
        row=p.parse_football_data(HEADER+'01/08/2020,,Juventus,Roma,1,3,1,2,5,2,1,2,0,0\n','2019-20')[0]
        self.assertEqual(row['date'],'2020-08-01');self.assertIsNone(row['time']);self.assertFalse(row['time_confirmed'])

    def test_bad_csv_season_duplicates_and_invalid_ht_are_guarded(self):
        for text,season in [('<html>Not found</html>','2026-27'),(HEADER+'15/08/2027,,Inter,Milan,0,0,0,0,,,,,,\n','2026-27'),(HEADER+'15/08/2026,,Inter,Milan,0,0,0,0,,,,,,\n15/08/2026,,Inter,Milan,0,0,0,0,,,,,,\n','2026-27'),(HEADER,'2026-99')]:
            with self.subTest(season=season,text=text[:30]),self.assertRaises(p.ProviderError):p.parse_football_data(text,season)
        row=p.parse_football_data(HEADER+'15/08/2026,,Inter,Milan,1,0,2,0,,,,,,\n','2026-27')[0]
        self.assertEqual(row['ft_home'],1);self.assertIsNone(row['ht_home']);self.assertIsNone(row['ht_away'])

    def test_calendar_future_and_live_placeholders_stay_unknown(self):
        for status in ('scheduled','live'):
            row=p.parse_espn_event(event(status=status),'2026-27')
            self.assertEqual((row['home'],row['away']),('Inter','Milan'))
            for key in ('ft_home','ht_home','corners','yellow','red'):self.assertIsNone(row[key])
        e=event();e['competitions'][0]['timeValid']=False
        row=p.parse_espn_event(e,'2026-27')
        self.assertIsNone(row['time']);self.assertIsNone(row['kickoff']);self.assertFalse(row['date_confirmed'])
        e=event(status='finished');e['date']='2050-08-15T18:00Z';e['season']['slug']='2050-51-italian-serie-a'
        self.assertEqual(p.parse_espn_event(e,'2050-51')['status'],'scheduled')

    def test_real_full_time_zero_and_explicit_round_are_kept(self):
        e=event(status='finished');e['week']={'number':4}
        row=p.parse_espn_event(e,'2026-27')
        self.assertEqual((row['ft_home'],row['ft_away']),(0,0));self.assertIsNone(row['ht_home'])
        self.assertEqual((row['round'],row['scope']),(4,'first'))
        e['date']='2026-05-10T18:00Z';self.assertIsNone(p.parse_espn_event(e,'2026-27'))
        e=event();e['status']['type']['state']='mystery'
        with self.assertRaises(p.ProviderError):p.parse_espn_event(e,'2026-27')

    def test_explicit_round_tables_require_season_and_complete_calendar(self):
        fixtures=schedule();html=round_html(fixtures)
        parsed=p.parse_round_tables([(html,'https://www.betexplorer.com/fixtures/')],'2026-27')
        self.assertEqual(len(parsed),380)
        home,away,rnd=fixtures[57];self.assertEqual(parsed[(home,away)]['round'],rnd)
        for bad in (round_html(fixtures,'2025/2026'),round_html(fixtures[:-1]),'<html>Unavailable</html>'):
            with self.assertRaises(p.ProviderError):p.parse_round_tables([(bad,'https://example.invalid')],'2026-27')
        duplicate=html.replace('</table>','<tr><th>38. Round</th></tr><tr><td><a href="/x" class="in-match"><span>Team00</span>-<span>Team19</span></a></td></tr></table>')
        with self.assertRaises(p.ProviderError):p.parse_round_tables([(duplicate,'https://example.invalid')],'2026-27')

    def test_source_published_award_is_not_a_played_half_time(self):
        html=round_html(schedule())
        html=html.replace('<span>Team00</span> - <span>Team19</span></a>','<span>Team00</span> - <span>Team19</span></a><span title="Awarded">3:0</span> AWA.',1)
        parsed=p.parse_round_tables([(html,'https://example.invalid/results/')],'2026-27')
        info=parsed[('Team00','Team19')]
        self.assertTrue(info['administrative']);self.assertEqual((info['awarded_ft_home'],info['awarded_ft_away']),(3,0))

    def test_incomplete_calendar_fails_closed(self):
        with patch.object(p,'football_data',return_value=[]),patch.object(p,'calendar',return_value=[p.parse_espn_event(event(),'2026-27')]):
            with self.assertRaises(p.ProviderError):p.sync_games('2026-27')
        self.assertFalse(p.get_coverage()['fixtures_available'])

    def test_merge_keeps_csv_authority_and_adds_source_rounds(self):
        fixtures=schedule();games=[]
        for i,(home,away,rnd) in enumerate(fixtures):
            e=event(str(i+1),home,away)
            e['competitions'][0]['timeValid']=False
            games.append(p.parse_espn_event(e,'2026-27'))
        rows=p.parse_football_data(HEADER+'15/08/2026,20:00,Team00,Team19,1,0,0,0,0,2,1,2,0,0\n','2026-27')
        mapping=p.parse_round_tables([(round_html(fixtures),'https://example.invalid')],'2026-27')
        with patch.object(p,'football_data',return_value=rows),patch.object(p,'calendar',return_value=games),patch.object(p,'round_calendar',return_value=mapping):
            merged=p.sync_games('2026-27')
        self.assertEqual(len(merged),380)
        target=next(g for g in merged if g['home']=='Team00' and g['away']=='Team19')
        self.assertEqual(target['id'],'italy:fd:2627:Team00:Team19')
        self.assertEqual((target['ft_home'],target['ft_away'],target['red'],target['round'],target['time']),(1,0,0,1,'14:00'))
        self.assertTrue(p.get_coverage()['fixtures_available'])

    def test_summary_statistics_zero_is_accepted_only_with_population_signals(self):
        row=p.parse_espn_event(event(status='finished'),'2026-27');row['ft_home']=1
        summary={'header':{'competitions':[{'competitors':[
            {'id':'1','homeAway':'home','score':'1','linescores':[{'period':1,'displayValue':'1'}]},
            {'id':'2','homeAway':'away','score':'0','linescores':[{'period':1,'displayValue':'0'}]},
        ]}]},'boxscore':{'teams':[
            {'team':{'id':side},'statistics':[{'name':'possessionPct','displayValue':'50'},{'name':'wonCorners','displayValue':'0'},{'name':'yellowCards','displayValue':'0'},{'name':'redCards','displayValue':'0'}]} for side in ('1','2')
        ]}}
        with patch.object(p,'_json',return_value=summary):p.enrich_match(row)
        self.assertEqual((row['ht_home'],row['ht_away'],row['corners'],row['yellow'],row['red']),(1,0,0,0,0))
        empty=deepcopy(summary)
        for box in empty['boxscore']['teams']:box['statistics']=[{'name':'wonCorners','displayValue':'0'},{'name':'redCards','displayValue':'0'}]
        row=p.parse_espn_event(event(status='finished'),'2026-27');row['ft_home']=1
        with patch.object(p,'_json',return_value=empty):p.enrich_match(row)
        self.assertIsNone(row['corners']);self.assertIsNone(row['red'])


if __name__=='__main__':unittest.main()
