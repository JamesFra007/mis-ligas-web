from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from app import paraguay as p
from scripts.import_paraguay_history import parse_report
from scripts.paraguay_archive import parse_results


def event(ident='1001',home='Olimpia Asuncion',away='Cerro Porteno',status='finished',year=2026,scope='apertura'):
    state={'finished':{'name':'STATUS_FULL_TIME','state':'post','completed':True},
           'scheduled':{'name':'STATUS_SCHEDULED','state':'pre','completed':False}}[status]
    return {'id':ident,'date':f'{year}-02-01T22:00Z','season':{'year':year,'slug':f'torneo-{scope}-{year}'},
            'status':{'type':state},'competitions':[{'timeValid':True,'competitors':[
                {'id':'1','homeAway':'home','score':'2','team':{'displayName':home},'linescores':[{'value':0},{'value':2}]},
                {'id':'2','homeAway':'away','score':'1','team':{'displayName':away},'linescores':[{'value':0},{'value':1}]},
            ]}]}


def goal(ident,team,period,*,own=False,shootout=False):
    return {'id':str(ident),'team':{'id':str(team)},'period':{'number':period},
            'scoringPlay':True,'shootout':shootout,'type':{'type':'own-goal' if own else 'goal'}}


def summary(e,goals):
    return {'header':{'id':e['id'],'competitions':deepcopy(e['competitions'])},'keyEvents':goals}


def metrics(**values):
    return {'statistics':[{'name':name,'displayValue':str(value)} for name,value in values.items()]}


class ParaguayProviderTests(unittest.TestCase):
    def test_scoreboard_placeholder_halves_and_future_results_are_not_observations(self):
        row=p.parse_espn_event(event(),'2026')
        self.assertEqual((row['home'],row['away']),('Olimpia','Cerro Porteño'))
        self.assertEqual((row['ft_home'],row['ft_away']),(2,1))
        self.assertIsNone(row['ht_home']);self.assertIsNone(row['ht_away'])
        for e in [event(status='scheduled'),event(year=2050)]:
            row=p.parse_espn_event(e,str(e['season']['year']))
            for field in ('ft_home','ft_away','ht_home','ht_away','corners','yellow','red'):self.assertIsNone(row[field])

    def test_complete_goal_log_counts_stoppage_and_ignores_penalty_shootout(self):
        e=event();game=p.parse_espn_event(e,'2026')
        log=[goal('a',1,1),goal('b',2,1),goal('c',1,2),goal('d',2,5,shootout=True)]
        log[1]['clock']={'displayValue':'45+5'}
        self.assertEqual(p.half_time_from_complete_events(summary(e,log),game),(1,1))
        result=p.enrich_match(game,summary(e,log))
        self.assertEqual((result['ht_home'],result['ht_away']),(1,1))
        self.assertIn('summary?event=1001',result['ht_source_url'])

    def test_incomplete_empty_misidentified_and_contradictory_logs_are_rejected(self):
        e=event();game=p.parse_espn_event(e,'2026')
        complete=[goal('a',1,1),goal('b',2,1),goal('c',1,2)]
        invalid=[[],complete[:-1],[*complete,goal('a',2,1)],
                 [goal('a',1,None),*complete[1:]],[goal('a',99,1),*complete[1:]]]
        for log in invalid:
            with self.subTest(log=log):self.assertIsNone(p.half_time_from_complete_events(summary(e,log),game))
        other=summary(e,complete);other['header']['id']='another'
        self.assertIsNone(p.half_time_from_complete_events(other,game))
        self.assertIsNone(p.half_time_from_complete_events(summary(e,complete),{**game,'administrative':True}))

    def test_own_goal_convention_is_accepted_only_when_final_tally_matches(self):
        e=event();game=p.parse_espn_event(e,'2026')
        # Source identifies the scorer's side; the goal belongs to its opponent.
        log=[goal('a',1,1),goal('b',2,1),goal('c',2,2,own=True)]
        self.assertEqual(p.half_time_from_complete_events(summary(e,log),game),(1,1))

    def test_goal_only_corner_zero_placeholders_do_not_become_observations(self):
        fake={'home':metrics(wonCorners=0,totalShots=2,shotsOnTarget=2,totalGoals=2),
              'away':metrics(wonCorners=0,totalShots=1,shotsOnTarget=1,totalGoals=0)}
        self.assertEqual(p.corner_pair(fake),[None,None])
        true_zero={side:metrics(wonCorners=0,totalShots=8,foulsCommitted=10) for side in ('home','away')}
        self.assertEqual(p.corner_pair(true_zero),[0,0])
        observed={'home':metrics(wonCorners=0,totalShots=3,foulsCommitted=5),
                  'away':metrics(wonCorners=6,totalShots=8,foulsCommitted=10)}
        self.assertEqual(p.corner_pair(observed),[0,6])
        self.assertEqual(p.corner_pair({'home':observed['home']}),[0,None])

    def test_verified_corrections_are_exact_and_administrative_scores_excluded(self):
        for ident,(year,home,away,fh,fa,admin,url) in p.VERIFIED_RESULTS.items():
            e=event(ident,home,away,year=year)
            row=p.parse_espn_event(e,str(year))
            self.assertEqual((row['ft_home'],row['ft_away']),(fh,fa))
            self.assertEqual(row['administrative'],admin)
            self.assertEqual(row['result_source_url'],url)
            if admin:self.assertIsNone(row['ht_home']);self.assertIsNone(row['corners'])
            wrong=p.parse_espn_event(event(ident,'Unrelated team',away,year=year),str(year))
            self.assertEqual((wrong['ft_home'],wrong['ft_away']),(2,1))
        normal=p.parse_espn_event(event('728904','2 de Mayo','General Caballero',year=2025),'2025')
        self.assertFalse(normal['administrative']);self.assertNotIn('played_ft_home',normal)

    def test_verified_half_survives_refresh_only_for_the_same_game_and_ft(self):
        game=p.parse_espn_event(event(),'2026')
        old={**game,'ht_home':1,'ht_away':1,'ht_verified':True,'ht_source':'BetExplorer',
             'ht_source_url':'https://www.betexplorer.com/football/paraguay/example/event/'}
        p._retain_verified_half(game,old)
        self.assertEqual((game['ht_home'],game['ht_away']),(1,1))
        for edit in ({'ft_home':3},{'scope':'Clausura'},{'home':'Other club'},{'ht_home':4},
                     {'ht_source_url':'https://unrelated.invalid/event'},{'ht_verified':False}):
            new=p.parse_espn_event(event(),'2026');p._retain_verified_half(new,{**old,**edit})
            self.assertIsNone(new['ht_home'])

    def test_report_requires_matching_teams_id_dates_and_period_sum(self):
        game=p.parse_espn_event(event(),'2026');game['archive_id']='abc'
        source={'@type':'SportsEvent','startDate':'2026-02-01T23:00:00+01:00',
                'homeTeam':{'name':'Olimpia Asuncion'},'awayTeam':{'name':'Cerro Porteno'},
                'url':'https://www.betexplorer.com/football/paraguay/example/abc/'}
        def report(src,halves='(1:1, 1:0)'):
            return '<script type="application/ld+json">'+json.dumps(src)+'</script><div id="js-partial">'+halves+'</div>'
        parsed=parse_report(report(source),game)
        self.assertEqual((parsed['ht_home'],parsed['ht_away'],parsed['time']),(1,1,'17:00'))
        for bad in [report(source,'(0:0, 1:0)'),report({**source,'startDate':'2025-02-01T23:00:00+01:00'}),
                    report({**source,'awayTeam':{'name':'Libertad'}}),report({**source,'url':'https://www.betexplorer.com/wrong/'})]:
            with self.assertRaises(ValueError):parse_report(bad,game)

    def test_rsssf_goal_logs_never_guess_a_boundary_minute(self):
        html='Torneo Apertura 2025\nRound 1\nOlimpia 2-1 Libertad\n  [Player 45+3, Player 65; Other 10]\n'
        row=parse_results(html,2025)[0]
        self.assertEqual(row['ht_from_goal_minutes'],[1,1])
        self.assertIsNone(parse_results(html.replace('45+3','47'),2025)[0]['ht_from_goal_minutes'])

    def test_partial_or_duplicate_calendar_fails_before_publishing(self):
        for events in ([event()], [event(),event()]):
            with patch.object(p,'_json',return_value={'events':events}):
                with self.assertRaises(p.ProviderError):p.sync_games('2026',enrich_limit=0)

    def test_saved_round_identity_does_not_conflate_apertura_and_clausura(self):
        game=p.parse_espn_event(event(),'2026')
        old={**game,'round':5,'round_source':'BetExplorer','round_source_url':'https://www.betexplorer.com/football/paraguay/results/'}
        self.assertEqual(p._saved_metadata(game,old)['round'],5)
        self.assertIsNone(p._saved_metadata(game,{**old,'scope':'Clausura'}))
        self.assertIsNone(p._saved_metadata(game,{**old,'round':True}))


if __name__=='__main__':unittest.main()
