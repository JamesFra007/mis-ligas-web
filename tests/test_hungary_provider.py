from datetime import datetime,timezone
import unittest
from unittest.mock import patch
from app.hungary import parse_round,enrich_match,season_context,sync_games,get_coverage,ProviderError


def page(score='2 - 1',date='2026. 11. 07.',time='13:30',round=13,ident='2186452',extra=''):
 return f'''<div id="match_panel"><div class="schedule"><div class="home_team"><a>PAKSI FC</a></div><div class="result-cont"><a href="https://adatbank.mlsz.hu/match/67/0/33586/{round}/{ident}.html"><span class="schedule-points">{score}</span></a></div><div class="away_team">ZTE FC</div><div class="team_sorsolas_date">{date}<span>{time}</span></div>{extra}</div></div><div id="other"><div class="schedule"><div class="home_team">OTHER</div></div></div>'''


class HungaryProviderTests(unittest.TestCase):
 def test_real_official_scores_identity_timezone_and_round(self):
  result=parse_round(page(),'2026-27',now=datetime(2026,11,8,tzinfo=timezone.utc))
  self.assertEqual(len(result),1);game=result[0]
  self.assertEqual((game['home'],game['away'],game['round']),('Paks','Zalaegerszeg',13))
  self.assertEqual((game['id'],game['provider_id']),('hungary:mlsz:2186452','mlsz:2186452'))
  self.assertEqual((game['date'],game['time'],game['kickoff']),('2026-11-07','07:30','2026-11-07T12:30:00+00:00'))
  self.assertEqual((game['ft_home'],game['ft_away']),(2,1))
  self.assertTrue(all(game[k] is None for k in ['ht_home','ht_away','corners','yellow','red','yellow_home','yellow_away','red_home','red_away']))

 def test_dst_conversion_changes_with_budapest_summer(self):
  game=parse_round(page(date='2026. 08. 07.',time='20:00'),'2026-27',now=datetime(2026,11,8,tzinfo=timezone.utc))[0]
  self.assertEqual(game['time'],'13:00');self.assertEqual(game['kickoff'],'2026-08-07T18:00:00+00:00')

 def test_future_placeholder_score_is_never_zero_zero(self):
  game=parse_round(page(score='0 - 0',date='2027. 02. 01.',time='18:00'),'2026-27',now=datetime(2026,11,8,tzinfo=timezone.utc))[0]
  self.assertEqual(game['status'],'scheduled');self.assertIsNone(game['ft_home']);self.assertIsNone(game['ht_home'])

 def test_future_without_hour_is_unconfirmed(self):
  game=parse_round(page(score='-',date='2027. 02. 01.',time=''),'2026-27',now=datetime(2026,11,8,tzinfo=timezone.utc))[0]
  self.assertIsNone(game['time']);self.assertIsNone(game['kickoff']);self.assertFalse(game['time_confirmed']);self.assertFalse(game['date_confirmed'])

 def test_near_kickoff_unsettled_score_is_not_a_finished_match(self):
  game=parse_round(page(),'2026-27',now=datetime(2026,11,7,13,15,tzinfo=timezone.utc))[0]
  self.assertEqual(game['status'],'live');self.assertIsNone(game['ft_home'])

 def test_half_time_is_taken_from_report_not_total_or_another_widget(self):
  game=parse_round(page(),'2026-27',now=datetime(2026,11,8,tzinfo=timezone.utc))[0]
  actual=enrich_match(game,'<div class="other">8 - 8</div><div class="match-result"><span>2 - 1</span><p>(1 - 0)</p></div>')
  self.assertEqual((actual['ht_home'],actual['ht_away'],actual['ft_home'],actual['ft_away']),(1,0,2,1))
  self.assertIsNone(game['ht_home'])
  self.assertIsNone(enrich_match(game,'<div class="match-result"><span>2 - 1</span><p></p></div>')['ht_home'])
  self.assertIsNone(enrich_match(game,'<div class="match-result"><span>2 - 1</span><p>(3 - 1)</p></div>')['ht_home'])

 def test_abandoned_original_is_preserved_but_not_treated_as_played(self):
  game=parse_round(page(score='FÉLBESZAKADT'),'2026-27',now=datetime(2026,11,8,tzinfo=timezone.utc))[0]
  self.assertTrue(game['administrative']);self.assertEqual(game['status'],'cancelled');self.assertIsNone(game['ft_home'])

 def test_future_fixture_without_report_id_has_a_stable_join_key(self):
  html=page(score='-',date='2027. 02. 01.',time='18:00',round=14)
  html=html.replace('<a>PAKSI FC</a>','<a href="https://adatbank.mlsz.hu/club/67/0/33586/14/328186.html">PAKSI FC</a>')
  html=html.replace('<div class="away_team">ZTE FC</div>','<div class="away_team"><a href="https://adatbank.mlsz.hu/club/67/0/33586/14/328188.html">ZTE FC</a></div>')
  provisional=html.replace('https://adatbank.mlsz.hu/match/67/0/33586/14/2186452.html','#')
  future=parse_round(provisional,'2026-27',now=datetime(2026,11,8,tzinfo=timezone.utc))[0]
  known=parse_round(html,'2026-27',now=datetime(2026,11,8,tzinfo=timezone.utc))[0]
  self.assertEqual(future['id'],'hungary:fixture:67:33586:14:328186:328188')
  self.assertEqual(future['fixture_key'],known['fixture_key'])
  self.assertIsNone(future['mlsz_id']);self.assertEqual(known['mlsz_id'],'2186452')

 def test_real_zeroes_are_preserved(self):
  game=parse_round(page(score='0 - 0'),'2026-27',now=datetime(2026,11,8,tzinfo=timezone.utc))[0]
  actual=enrich_match(game,'<div class="match-result"><span>0 - 0</span><p>(0 - 0)</p></div>')
  self.assertEqual((actual['ft_home'],actual['ft_away'],actual['ht_home'],actual['ht_away']),(0,0,0,0))

 def test_bad_season_or_unrecognized_html_fails(self):
  with self.assertRaises(ProviderError):season_context('2026-99')
  with self.assertRaises(ProviderError):parse_round('<html>Error</html>','2026-27')

 def test_awarded_report_placeholder_half_time_is_never_a_played_zero(self):
  game=parse_round(page(ident='1544830',score='3 - 0'),'2026-27',now=datetime(2026,11,8,tzinfo=timezone.utc))[0]
  self.assertTrue(game['administrative']);self.assertIsNone(game['ht_home'])
  report='<div class="match-result"><span>3 - 0</span><p>(0 - 0)</p></div>'
  updated=enrich_match(game,report)
  self.assertTrue(updated['administrative']);self.assertIsNone(updated['ht_home']);self.assertIsNone(updated['ht_away'])
  self.assertEqual((updated['ft_home'],updated['ft_away']),(3,0))

 def test_actual_midnight_is_not_replaced_when_explicitly_published(self):
  game=parse_round(page(score='-',date='2027. 02. 01.',time='00:00'),'2026-27',now=datetime(2026,11,8,tzinfo=timezone.utc))[0]
  self.assertTrue(game['time_confirmed']);self.assertEqual(game['time'],'18:00');self.assertEqual(game['date'],'2027-01-31')

 def test_full_season_rejects_a_silently_partial_calendar(self):
  with patch('app.hungary.season_context',return_value={'season_id':67,'league_id':33586,'rounds':33}),patch('app.hungary._html',return_value=page()):
   with self.assertRaises(ProviderError):sync_games('2026-27')
  self.assertFalse(get_coverage()['fixtures_available'])

 def test_update_never_deletes_on_partial_round_fetch(self):
  with patch('app.hungary.season_context',return_value={'season_id':67,'league_id':33586,'rounds':2}),patch('app.hungary._html',side_effect=[page(),ProviderError('temporary')]):
   with self.assertRaises(ProviderError):sync_games('2026-27')
  self.assertFalse(get_coverage()['fixtures_available'])

 def test_update_enriches_only_matches_and_keeps_stats_out_of_scope(self):
  report='<div class="match-result"><span>2 - 1</span><p>(1 - 0)</p></div>'
  def load(url):return report if '/match/' in url else page()
  with patch('app.hungary.season_context',return_value={'season_id':67,'league_id':33586,'rounds':1}),patch('app.hungary._html',side_effect=load):
   rows=sync_games('2026-27')
  self.assertEqual(len(rows),1);self.assertEqual(rows[0]['ht_home'],1);self.assertIsNone(rows[0]['corners']);self.assertIsNone(rows[0]['yellow']);self.assertIsNone(rows[0]['red']);self.assertTrue(get_coverage()['fixtures_available'])


if __name__=='__main__':unittest.main()
