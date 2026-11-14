"""Verify numbered matchdays, cumulative lists and droughts in the real site.

Run after publish_snapshot.py. --url verifies the deployed standalone artifact;
without it the same artifact is tested fully offline in Chromium.
"""
import argparse
import asyncio
import csv
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import db
from app.leagues import LEAGUES
from app.main import dashboard
from openpyxl import load_workbook
from playwright.async_api import async_playwright

ART = ROOT / 'test-results' / 'match-views'
ART.mkdir(parents=True, exist_ok=True)


def expected(league, season, order='round', match_round='', page=1, page_size=200, **filters):
    return dashboard(league=league, season=season, scope=filters.get('scope', 'all'),
                     team=filters.get('team', 'all'), condition=filters.get('condition', 'all'),
                     status=filters.get('status', 'all'), search=filters.get('search', ''),
                     page=page, page_size=page_size, order=order, match_round=match_round)


async def row_ids(page):
    return await page.locator('#matches-body tr[data-match-id]').evaluate_all(
        "rows => rows.map(row => row.dataset.matchId)")


async def wait_ids(page, wanted):
    await page.wait_for_function("""wanted =>
        !document.querySelector('#matches-table').classList.contains('loading-table') &&
        JSON.stringify([...document.querySelectorAll('#matches-body tr[data-match-id]')]
        .map(row => row.dataset.matchId)) === JSON.stringify(wanted)""", arg=wanted)


async def choose_season(page, season):
    if await page.locator('#season-select').input_value() == season:
        await page.locator('#season-select').dispatch_event('change')
    else:
        await page.select_option('#season-select', season)


async def numbered_order_is_valid(page):
    return await page.locator('#round-buttons .round-number-list').evaluate_all("""groups => groups.every(group => {
        const values = [...group.querySelectorAll('[data-round-id]')].map(node => {
            const value = JSON.parse(node.dataset.roundId)[2];
            return value === null || value === '' ? Infinity : Number(value);
        });
        return values.every((value, index) => !Number.isNaN(value) && (!index || values[index-1] <= value));
    })""")


def all_expected(league, season, order='round', match_round='', **filters):
    first = expected(league, season, order, match_round, **filters)
    games = list(first['games'])
    for page in range(2, first['pagination']['pages'] + 1):
        games.extend(expected(league, season, order, match_round, page=page, **filters)['games'])
    return games


async def run(url=None):
    db.initialize()
    errors, checks, seasons_checked = [], 0, []
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
                                          args=['--no-sandbox'])
        context = await browser.new_context(viewport={'width':1440, 'height':1100},
                                            accept_downloads=True, offline=not bool(url))
        page = await context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        if url:
            await page.goto(url, wait_until='networkidle', timeout=60000)
        else:
            await page.set_content((ROOT/'site/index.html').read_text(), wait_until='load')
        await page.wait_for_function("count=>document.querySelectorAll('#league-nav button').length===count",arg=len(LEAGUES))
        await page.wait_for_function("document.querySelectorAll('#round-buttons [data-round-id]').length >= 1")
        await page.wait_for_function("!document.querySelector('#matches-table').classList.contains('loading-table')")
        assert await page.locator('#previous-page, #next-page, #page-label').count() == 0
        assert await page.locator('[data-order]').count() == 2
        assert await page.locator('[data-order=chronological]').inner_text() == 'Lista completa'
        initial_season = await page.locator('#season-select').input_value()
        initial = expected('spain', initial_season, match_round='auto')
        await wait_ids(page, [g['id'] for g in all_expected('spain', initial_season, match_round='auto')])
        assert len(await row_ids(page)) == initial['pagination']['total']
        assert not await page.locator('#load-more').is_visible()
        assert await page.locator('#round-buttons [data-round-id]').count() == 38
        assert len(await row_ids(page)) == 10
        assert await numbered_order_is_valid(page)
        checks += 8

        # The hosted JS and Python API must agree over every league and season,
        # including the full, independently ordered HT drought calculation.
        for item in db.all_seasons():
            league, season = item['league'], item['season']
            seasons_checked.append(f'{league}:{season}')
            for order, selected in [('round', ''), ('chronological', ''), ('round', 'auto')]:
                params = {'league':league, 'season':season, 'order':order,
                          'match_round':selected, 'page_size':200}
                actual = await page.evaluate("async q => (await fetch('/api/dashboard?' + q)).json()",
                                             urlencode(params))
                target = expected(league, season, order, selected)
                for key in ('summary','frequency','ht_droughts','droughts','scorelines','team_stats','pagination','options'):
                    assert actual[key] == target[key], (league, season, order, selected, key)
                    checks += 1
                assert [g['id'] for g in actual['games']] == [g['id'] for g in target['games']]
                checks += 1
            groups = target['options']['rounds']
            if len(groups) > 1:
                chosen = groups[-1]['id']
                params['match_round'] = chosen
                actual = await page.evaluate("async q => (await fetch('/api/dashboard?' + q)).json()",
                                             urlencode(params))
                target = expected(league, season, 'round', chosen)
                assert actual['options'] == target['options']
                assert actual['ht_droughts'] == target['ht_droughts']
                assert actual['droughts'] == target['droughts']
                assert [g['id'] for g in actual['games']] == [g['id'] for g in target['games']]
                checks += 4

        # Exercise both modes in each competition using completed historical
        # seasons: every load-more must preserve the first DOM node and prefix.
        for index, league in enumerate(LEAGUES):
            season = min(item['season'] for item in db.all_seasons() if item['league']==league)
            await page.locator('#league-nav button').nth(index).click()
            await choose_season(page, season)
            await page.locator('[data-order=round]').click()
            target = expected(league, season, match_round='auto')
            await wait_ids(page, [g['id'] for g in target['games']])
            buttons = page.locator('#round-buttons [data-round-id]')
            assert await page.locator('#round-buttons').is_visible()
            assert not await page.locator('#load-more').is_visible()
            assert await buttons.count() == len(target['options']['rounds'])
            assert await page.locator('#round-buttons [aria-pressed=true]').get_attribute('data-round-id') == target['options']['selected_round']
            assert await buttons.evaluate_all("nodes => nodes.every(node => node.getBoundingClientRect().width > 0 && node.getBoundingClientRect().height > 0)")
            assert await buttons.evaluate_all("nodes => nodes.every(node => { const n=JSON.parse(node.dataset.roundId)[2],text=node.innerText.trim(); return n === null ? Boolean(text) && !/^\\d+$/.test(text) : text === String(n); })")
            assert await numbered_order_is_valid(page)
            assert not await page.locator('#round-select').count()
            for metric in ('ht', 'ft'):
                card = page.locator(f'#drought-content article[data-drought-metric={metric}]')
                assert await card.is_visible()
                metric_data = target['droughts'][metric]
                if metric_data['top']:
                    assert await card.locator('.drought-value strong').inner_text() == str(metric_data['top'][0]['count'])
                    assert await card.locator('.drought-dates').inner_text()
                    assert await card.locator('.drought-rounds').inner_text()
                checks += 2
            red = page.locator('#drought-content article[data-drought-metric=red]')
            assert await red.count() == (1 if LEAGUES[league]['cards'] else 0)
            if LEAGUES[league]['cards']:
                assert await red.is_visible()
                assert target['droughts']['red'] is not None
                assert await red.locator('.drought-value strong').inner_text() == str(target['droughts']['red']['top'][0]['count'])
            else:
                assert target['droughts']['red'] is None
            checks += 9
            chosen = target['options']['rounds'][-1]['id']
            chosen_index = await buttons.evaluate_all("(nodes, id) => nodes.findIndex(node => node.dataset.roundId === id)", chosen)
            assert chosen_index >= 0
            await buttons.nth(chosen_index).click()
            chosen_data = expected(league, season, match_round=chosen)
            await wait_ids(page, [g['id'] for g in chosen_data['games']])
            assert await page.locator('#round-buttons [aria-pressed=true]').get_attribute('data-round-id') == chosen
            assert chosen_data['summary'] == target['summary']
            assert chosen_data['ht_droughts'] == target['ht_droughts']
            assert chosen_data['droughts'] == target['droughts']
            checks += 4
            if league == 'colombia':
                labels = await buttons.evaluate_all("nodes => nodes.map(node => node.getAttribute('aria-label') || node.title)")
                assert any('Apertura' in label for label in labels)
                assert any('Finalización' in label for label in labels)
                checks += 2
            if league == 'paraguay':
                labels = await buttons.evaluate_all("nodes => nodes.map(node => node.getAttribute('aria-label') || node.title)")
                assert any('Apertura' in label for label in labels)
                assert any('Clausura' in label for label in labels)
                assert await page.locator('#matches-table th:visible').count() == 8
                assert not await page.locator('.tiny-card').count()
                checks += 4
            if league == 'poland':
                async with page.expect_download() as event:
                    await page.locator('#export-link').click()
                download = await event.value
                path = ART / download.suggested_filename
                await download.save_as(path)
                with path.open(encoding='utf-8-sig', newline='') as source:
                    export_rows = list(csv.reader(source, delimiter=';'))
                assert len(export_rows) == len(chosen_data['games']) + 1
                assert 'Córners' not in export_rows[0] and 'Amarillas' not in export_rows[0]
                async with page.expect_download() as event:
                    await page.locator('#history-link').click()
                download = await event.value
                path = ART / download.suggested_filename
                await download.save_as(path)
                workbook = load_workbook(path, read_only=True)
                assert len(workbook.sheetnames) == 10
                assert 'EKSTRA 18-19' in workbook.sheetnames
                workbook.close()
                checks += 4

            await page.locator('[data-order=chronological]').click()
            ordered = expected(league, season, 'chronological', page_size=200)
            all_ids = [g['id'] for g in ordered['games']]
            for n in range(2, ordered['pagination']['pages'] + 1):
                all_ids.extend(g['id'] for g in expected(league, season, 'chronological',
                                                        page=n, page_size=200)['games'])
            await wait_ids(page, all_ids[:40])
            assert not await page.locator('#round-filter').is_visible()
            await page.evaluate("window.__testFirstRow = document.querySelector('#matches-body tr[data-match-id]')")
            while len(await row_ids(page)) < len(all_ids):
                before = len(await row_ids(page))
                await page.locator('#load-more').click()
                after = min(before + 40, len(all_ids))
                await wait_ids(page, all_ids[:after])
                assert await page.evaluate("window.__testFirstRow === document.querySelector('#matches-body tr[data-match-id]')")
                assert len(set(await row_ids(page))) == after
                checks += 2
            assert not await page.locator('#load-more').is_visible()
            assert (await page.locator('#pagination-label').inner_text()).endswith(f'{len(all_ids)} partidos')
            checks += 3

        # A failed append must retain rows and the original node. Retry asks for
        # the same second page; an out-of-date append cannot cross a filter reset.
        league = 'poland'
        season = '2018-19'
        await page.locator('#league-nav button').nth(list(LEAGUES).index(league)).click()
        await choose_season(page, season)
        ids = [g['id'] for g in expected(league, season, 'chronological', page_size=200)['games']]
        await wait_ids(page, ids[:40])
        await page.evaluate("""() => {
            window.__testFirstRow = document.querySelector('#matches-body tr[data-match-id]');
            window.__testFetch = window.fetch;
            window.__testFailOnce = true;
            window.fetch = async (input, options) => {
                const url = new URL(String(input), /^https?:/.test(location.href) ? location.href : 'https://offline.invalid');
                if (url.pathname === '/api/dashboard' && url.searchParams.get('page') === '2' && window.__testFailOnce) {
                    window.__testFailOnce = false;
                    throw new Error('Fallo de carga simulado por la prueba');
                }
                return window.__testFetch(input, options);
            };
        }""")
        await page.locator('#load-more').click()
        await page.wait_for_function("document.querySelector('#notice').innerText.includes('No se pudieron cargar más partidos') && window.__testFailOnce === false")
        await wait_ids(page, ids[:40])
        assert await page.evaluate("window.__testFirstRow === document.querySelector('#matches-body tr[data-match-id]')")
        await page.locator('#load-more').click()
        await wait_ids(page, ids[:80])
        checks += 3
        await page.evaluate("window.fetch = window.__testFetch")
        await choose_season(page, season)
        await wait_ids(page, ids[:40])
        await page.evaluate("""() => {
            window.__testFetch = window.fetch;
            window.__testDelayed = false;
            window.fetch = async (input, options) => {
                const url = new URL(String(input), /^https?:/.test(location.href) ? location.href : 'https://offline.invalid');
                const response = await window.__testFetch(input, options);
                if (url.pathname === '/api/dashboard' && url.searchParams.get('page') === '2') {
                    window.__testDelayed = true;
                    await new Promise(resolve => setTimeout(resolve, 350));
                }
                return response;
            };
        }""")
        await page.locator('#load-more').click()
        await page.wait_for_function('window.__testDelayed')
        new_season = '2019-20'
        await choose_season(page, new_season)
        new_ids = [g['id'] for g in expected(league, new_season, 'chronological', page_size=40)['games']]
        await wait_ids(page, new_ids)
        await page.wait_for_timeout(500)
        assert await row_ids(page) == new_ids
        await page.evaluate('window.fetch = window.__testFetch')
        checks += 1

        # Team/status/search changes must reset accumulation rather than leave
        # stale matches from a previous season or view.
        team = expected(league, new_season)['options']['teams'][0]
        await page.select_option('#team-select', team)
        filtered = expected(league, new_season, 'chronological', page_size=40, team=team)
        await wait_ids(page, [g['id'] for g in filtered['games']])
        assert all(g['home']==team or g['away']==team for g in filtered['games'])
        assert await page.locator('#drought-title').is_visible()
        checks += 2
        await page.locator('#reset-button').click()
        await wait_ids(page, new_ids)
        await page.locator('#drought-title').scroll_into_view_if_needed()
        await page.screenshot(path=str(ART/'desktop.png'), full_page=False)
        for width in (390, 320):
            await page.set_viewport_size({'width':width, 'height':844})
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            await page.screenshot(path=str(ART/f'mobile-{width}.png'), full_page=False)
            await page.locator('[data-order=round]').click()
            await wait_ids(page, [g['id'] for g in all_expected(league, new_season, match_round='auto')])
            assert await page.locator('#round-buttons').is_visible()
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            assert await page.locator('#round-buttons [data-round-id]').evaluate_all("nodes => nodes.every(node => node.getBoundingClientRect().width > 0)")
            await page.locator('[data-order=chronological]').click()
            await wait_ids(page, new_ids)
            checks += 4
        assert not errors, errors
        await browser.close()
    report = {'passed':True, 'checks':checks, 'url':url, 'javascript_errors':errors,
              'seasons':seasons_checked}
    (ART/'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url')
    asyncio.run(run(parser.parse_args().url))
