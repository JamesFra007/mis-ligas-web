from copy import deepcopy
import unittest

from app.streaks import ht_droughts, ft_droughts, red_droughts


def match(n, *, case=False, **overrides):
    return {
        'id': str(n), 'date': f'2025-08-{n:02d}', 'time': '14:00',
        'round': n, 'scope': 'Apertura', 'phase': 'Fase regular',
        'home': 'Local', 'away': 'Visitante', 'status': 'finished',
        'ht_home': 1 if case else 0, 'ht_away': 1, 'administrative': False,
        **overrides,
    }


class HTDroughtTests(unittest.TestCase):
    def test_ranked_dates_rounds_and_current_are_chronological(self):
        games = [match(n, case=n in (4, 6)) for n in range(1, 9)]
        original = deepcopy(games)
        result = ht_droughts(list(reversed(games)))
        self.assertEqual([s['count'] for s in result['top']], [3, 2, 1])
        longest = result['top'][0]
        self.assertEqual((longest['start']['date'], longest['end']['date']), ('2025-08-01', '2025-08-03'))
        self.assertEqual(longest['ended_by']['id'], '4')
        self.assertEqual(longest['state'], 'closed')
        self.assertEqual([r['round'] for r in longest['rounds']], [1, 2, 3])
        self.assertEqual(result['current']['count'], 2)
        self.assertFalse(longest['lower_bound'])
        self.assertEqual(games, original)

    def test_missing_ht_breaks_verification_instead_of_joining_segments(self):
        result = ht_droughts([match(1), match(2, ht_home=None, ht_away=None), match(3), match(4)])
        self.assertEqual(result['missing_ht'], 1)
        self.assertEqual(result['eligible'], 3)
        self.assertEqual([s['count'] for s in result['top']], [2, 1])
        self.assertEqual(result['top'][1]['state'], 'interrupted')
        self.assertEqual(result['top'][1]['interrupted_by']['id'], '2')
        self.assertTrue(result['current']['lower_bound'])
        self.assertTrue(result['top'][1]['lower_bound'])
        result = ht_droughts([match(1), match(2, ht_home=None)])
        self.assertIsNone(result['current'])

    def test_simultaneous_mixed_block_never_invents_an_event_order(self):
        games = [match(1), match(2, case=True), match(3, date='2025-08-02'), match(4)]
        result = ht_droughts(games)
        self.assertEqual([s['count'] for s in result['top']], [1, 1])
        self.assertEqual(result['mixed_blocks'], 1)
        self.assertTrue(all(s['lower_bound'] for s in result['top']))
        self.assertEqual(result, ht_droughts(list(reversed(games))))
        self.assertEqual(result['top'][0]['ended_by']['id'], '2')

    def test_simultaneous_all_non_cases_count_together(self):
        result = ht_droughts([match(1), match(2, date='2025-08-01'), match(3, case=True)])
        self.assertEqual(result['top'][0]['count'], 2)
        self.assertFalse(result['top'][0]['lower_bound'])

    def test_missing_hour_groups_entire_day_conservatively(self):
        result = ht_droughts([
            match(1), match(2, date='2025-08-02', time='08:00'),
            match(3, date='2025-08-02', time=None),
            match(4, date='2025-08-02', time='17:00', case=True), match(5),
        ])
        self.assertEqual(result['uncertain_order'], 1)
        self.assertEqual([s['count'] for s in result['top']], [1, 1])
        self.assertEqual(result['mixed_blocks'], 1)

    def test_undated_finished_match_prevents_certifying_continuity(self):
        result = ht_droughts([match(1), match(2, date_confirmed=False), match(3)])
        self.assertEqual(result['uncertain_order'], 1)
        self.assertEqual(result['top'], [])
        self.assertIsNone(result['current'])
        self.assertIn('sin fecha confirmada', result['note'])

    def test_future_and_administrative_matches_never_count_or_break(self):
        result = ht_droughts([
            match(1), match(2, status='scheduled', ht_home=None, time=None),
            match(3, administrative=True, date_confirmed=False, ht_home=None), match(4),
        ])
        self.assertEqual(result['eligible'], 2)
        self.assertEqual(result['missing_ht'], 0)
        self.assertEqual(result['uncertain_order'], 0)
        self.assertEqual(result['current']['count'], 2)

    def test_empty_all_hits_and_no_hits(self):
        self.assertEqual(ht_droughts([])['top'], [])
        self.assertIsNone(ht_droughts([])['current'])
        self.assertEqual(ht_droughts([match(1, case=True)])['top'], [])
        self.assertIsNone(ht_droughts([match(1, case=True)])['current'])
        result = ht_droughts([match(1), match(2)])
        self.assertEqual(result['current']['count'], 2)
        self.assertEqual(result['top'], [result['current']])


class OtherDroughtTests(unittest.TestCase):
    def test_full_time_is_independent_and_missing_scores_break_continuity(self):
        games = [
            match(1, case=True, ft_home=0, ft_away=0),
            match(2, case=True, ft_home=1, ft_away=0),
            match(3, case=True, ft_home=1, ft_away=1),
            match(4, case=True, ft_home=0, ft_away=2),
            match(5, case=True, ft_home=None, ft_away=None),
            match(6, case=True, ft_home=0, ft_away=0),
        ]
        result = ft_droughts(games)
        self.assertEqual([s['count'] for s in result['top']], [2,1,1])
        self.assertEqual(result['top'][0]['ended_by']['id'], '3')
        self.assertEqual(result['top'][1]['state'], 'interrupted')
        self.assertEqual(result['missing_data'], 1)
        self.assertEqual(result['eligible'], 5)
        self.assertEqual(result['current']['count'], 1)
        self.assertTrue(result['current']['lower_bound'])
        self.assertEqual(ht_droughts(games)['top'], [])
        self.assertNotIn('missing_ht', result)
        self.assertIn('Los FT pendientes', result['note'])

    def test_red_zero_is_observed_none_is_unknown_and_future_admin_are_excluded(self):
        games = [
            match(1, red=0), match(2, red=None), match(3, red=0),
            match(4, red=2), match(5, red=0),
            match(6, red=None, administrative=True, date_confirmed=False),
            match(7, red=None, status='scheduled', date_confirmed=False), match(8, red=0),
        ]
        original = deepcopy(games)
        result = red_droughts(list(reversed(games)))
        self.assertEqual([s['count'] for s in result['top']], [2,1,1])
        self.assertEqual(result['missing_data'], 1)
        self.assertEqual(result['eligible'], 5)
        self.assertEqual(result['uncertain_order'], 0)
        self.assertEqual(result['current']['count'], 2)
        self.assertFalse(result['current']['lower_bound'])
        closed = next(s for s in result['top'] if s['state']=='closed')
        self.assertEqual(closed['ended_by']['id'], '4')
        self.assertTrue(closed['lower_bound'])
        self.assertIn('alguna tarjeta roja', result['note'])
        self.assertNotIn('HT pendientes', result['note'])
        self.assertEqual(games, original)

    def test_simultaneous_red_and_non_red_do_not_get_an_invented_order(self):
        games = [match(1, red=0), match(2, red=1), match(3, date='2025-08-02', red=0), match(4, red=0)]
        result = red_droughts(games)
        self.assertEqual([s['count'] for s in result['top']], [1,1])
        self.assertEqual(result['mixed_blocks'], 1)
        self.assertTrue(all(s['lower_bound'] for s in result['top']))
        self.assertEqual(result, red_droughts(list(reversed(games))))

    def test_non_integer_red_values_are_not_treated_as_known_zeroes(self):
        for value in (None, True, False, -1, 0.0, '0'):
            with self.subTest(value=value):
                result = red_droughts([match(1, red=value)])
                self.assertEqual(result['missing_data'], 1)
                self.assertEqual(result['top'], [])


if __name__ == '__main__':
    unittest.main()
