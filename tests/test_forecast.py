from copy import deepcopy
from datetime import datetime, timedelta
import math
import unittest
from zoneinfo import ZoneInfo

from app.forecast import forecast

TZ=ZoneInfo('America/Bogota')
NOW=datetime(2026,11,7,12,tzinfo=TZ)


def history(n=200,hits=40,year=2025,league='spain'):
    start=datetime(year,8,1,12,tzinfo=TZ)
    result=[]
    for i in range(n):
        dt=start+timedelta(hours=i*3)
        result.append({'id':f'{year}:{i}','league':league,'season':f'{year}-{(year+1)%100:02d}' if league=='spain' else str(year),'date':dt.date().isoformat(),'time':dt.strftime('%H:%M'),'home':'Local','away':'Visitante','status':'finished','ht_home':1 if i<hits else 0,'ht_away':1,'ft_home':2,'ft_away':1,'administrative':False})
    return result


def upcoming(n=40):
    rows=[]
    for i in range(n):
        dt=NOW+timedelta(days=1,hours=i*3)
        rows.append({'id':f'future:{i:04d}','league':'spain','season':'2026-27','date':dt.date().isoformat(),'time':dt.strftime('%H:%M'),'home':'Athletic Club','away':'Atlético de Madrid','status':'scheduled','administrative':False})
    return rows


def cdf(alpha,beta,k):
    # Independent mathematical oracle using beta-function log identities.
    return 1-math.exp(math.lgamma(beta+k)-math.lgamma(beta)+math.lgamma(alpha+beta)-math.lgamma(alpha+beta+k))


class ForecastTests(unittest.TestCase):
    def test_posterior_probability_predictive_horizons_and_quantiles(self):
        result=forecast(history(),upcoming(),NOW)
        self.assertEqual(result['status'],'ok')
        base=result['baseline'];a,b=40.5,160.5
        self.assertAlmostEqual(base['probability'],a/(a+b))
        self.assertAlmostEqual(base['observed_rate'],.2)
        self.assertTrue(base['wilson95']['low']<.2<base['wilson95']['high'])
        for row in result['horizons']:
            self.assertAlmostEqual(row['probability_at_least_one'],cdf(a,b,row['matches']),places=11)
        r=result['range']
        for key,q in [('q10',.1),('q50',.5),('q90',.9)]:
            self.assertGreaterEqual(cdf(a,b,r[key])+1e-10,q)
            self.assertLess(cdf(a,b,r[key]-1),q)
        self.assertGreaterEqual(r['central_probability'],.8)
        self.assertEqual(len(result['upcoming']),40)
        self.assertEqual(result['valid_until'],result['upcoming'][0]['kickoff'])
        self.assertEqual(result['order'],'chronological')
        self.assertTrue(r['window']['calendar_complete'])

    def test_unknown_zero_admin_and_future_do_not_pollute_sample(self):
        rows=history(100,20)
        invalid=[dict(rows[0],id='missing',ht_home=None),dict(rows[0],id='admin',administrative=True),dict(rows[0],id='not-finished',status='live'),dict(rows[0],id='bad-score',ht_home=5),dict(rows[0],id='zero-ft',ht_home=0,ht_away=0,ft_home=0,ft_away=0)]
        future=dict(upcoming()[0],id='future-marked-finished',status='finished',ht_home=1,ht_away=1,ft_home=2,ft_away=1)
        rows+=invalid+[future]
        result=forecast(rows,upcoming(),NOW)
        self.assertEqual(result['sample']['n'],101)
        self.assertEqual(result['sample']['hits'],20)
        self.assertEqual(result['sample']['excluded']['not_before_forecast'],1)
        self.assertEqual(result['sample']['excluded']['missing_or_invalid_score'],2)
        self.assertEqual(result['sample']['excluded']['unfinished_or_administrative'],2)

    def test_sample_minimum_and_incomplete_calendar(self):
        small=forecast(history(99,20),upcoming(),NOW)
        self.assertEqual(small['status'],'insufficient_history');self.assertIsNone(small['baseline']);self.assertIsNone(small['range'])
        limited=forecast(history(),upcoming(1),NOW)
        self.assertEqual(limited['status'],'ok');self.assertFalse(limited['range']['window']['calendar_complete']);self.assertIsNone(limited['range']['window']['end'])

    def test_missing_tbd_unconfirmed_and_past_upcoming_are_excluded(self):
        rows=upcoming(1)
        rows += [dict(rows[0],id='missing',time=None),dict(rows[0],id='tbd',time='TBD'),dict(rows[0],id='bad-hour',time='25:00'),dict(rows[0],id='not-confirmed',time_confirmed=False),dict(rows[0],id='date-not-confirmed',date_confirmed=False,time_confirmed=True),dict(rows[0],id='old',date='2020-01-01')]
        result=forecast(history(),rows,NOW)
        self.assertEqual(len(result['upcoming']),1)
        self.assertEqual(result['schedule_excluded']['unconfirmed_datetime'],5)
        self.assertEqual(result['schedule_excluded']['not_future'],1)
        no_schedule=forecast(history(),rows[1:],NOW)
        self.assertEqual(no_schedule['status'],'no_upcoming');self.assertIsNone(no_schedule['range']);self.assertIsNotNone(no_schedule['baseline'])

    def test_streak_does_not_make_event_due_and_names_do_not_change_reference(self):
        rows=history();other=deepcopy(rows)
        labels=[(r['ht_home'],r['ht_away']) for r in other][::-1]
        for game,score in zip(other,labels):
            game['ht_home'],game['ht_away']=score
            game['home']='Ath Madrid';game['away']='Ath Bilbao'
        a,b=forecast(rows,upcoming(),NOW),forecast(other,upcoming(),NOW)
        self.assertEqual(a['baseline'],b['baseline']);self.assertEqual(a['range'],b['range']);self.assertEqual(a['horizons'],b['horizons'])

    def test_latest_three_seasons_and_cap(self):
        rows=sum([history(400,80,year=y) for y in (2022,2023,2024,2025)],[])
        result=forecast(rows,upcoming(),NOW)
        self.assertEqual(result['sample']['n'],1000)
        self.assertEqual(result['sample']['seasons'],['2023-24','2024-25','2025-26'])
        self.assertEqual(result['sample']['excluded']['outside_seasons'],400)
        self.assertEqual(result['sample']['excluded']['sample_limit'],200)

    def test_simultaneous_matches_are_grouped_independent_of_input_order(self):
        rows=upcoming(2);rows[1]['date']=rows[0]['date'];rows[1]['time']=rows[0]['time']
        a=forecast(history(),rows,NOW);b=forecast(history(),rows[::-1],NOW)
        self.assertEqual(a,b)
        self.assertEqual(a['upcoming'][0]['group_start_rank'],1)
        self.assertEqual(a['upcoming'][0]['group_end_rank'],2)
        self.assertTrue(a['upcoming'][1]['simultaneous'])
        self.assertEqual(a['range']['window']['start']['ids'],['future:0000','future:0001'])

    def test_duplicate_events_are_not_double_counted_and_leagues_not_mixed(self):
        rows=history();future=upcoming()
        result=forecast(rows+[rows[0]],future+[future[0]],NOW)
        self.assertEqual(result['sample']['n'],200);self.assertEqual(len(result['upcoming']),40)
        self.assertEqual(result['valid_until'],result['upcoming'][0]['kickoff'])
        self.assertEqual(result['order'],'chronological')
        mixed=history(200,160,league='colombia')
        other=forecast(rows+mixed,future,NOW)
        self.assertEqual(result['baseline'],other['baseline'])
        bad=forecast(rows+mixed,future+[dict(future[0],id='col:future',league='colombia')],NOW)
        self.assertEqual(bad['status'],'mixed_leagues');self.assertIsNone(bad['baseline'])

    def test_all_or_zero_successes_stay_finite_without_false_certainty(self):
        none=forecast(history(1000,0),upcoming(),NOW)
        self.assertGreater(none['baseline']['probability'],0)
        self.assertIsNone(none['range']['q90']);self.assertTrue(none['range']['upper_truncated']);self.assertIsNone(none['range']['central_probability'])
        every=forecast(history(100,100),upcoming(),NOW)
        self.assertLess(every['baseline']['probability'],1)
        self.assertEqual((every['range']['q10'],every['range']['q50'],every['range']['q90']),(1,1,1))
        self.assertLess(every['range']['central_probability'],1)

    def test_unknown_historical_time_is_not_assumed_midnight(self):
        rows=history(100,20);rows[0]['time']=None
        result=forecast(rows,upcoming(),NOW)
        self.assertEqual(result['sample']['n'],99);self.assertEqual(result['status'],'insufficient_history')
        self.assertEqual(result['sample']['excluded']['unconfirmed_datetime'],1)

    def test_utc_now_and_naive_local_now_are_consistent(self):
        aware=forecast(history(),upcoming(),NOW)
        utc=forecast(history(),upcoming(),NOW.astimezone(ZoneInfo('UTC')))
        naive=forecast(history(),upcoming(),NOW.replace(tzinfo=None))
        self.assertEqual(aware,utc);self.assertEqual(aware,naive)


if __name__=='__main__':unittest.main()
