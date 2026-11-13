"""Exercise Italian scored matches, cards, seasons and actual workbook downloads."""
import argparse
import asyncio
import csv
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app import db
from app.leagues import LEAGUES
from app.stats import btts, pair
from openpyxl import load_workbook
from playwright.async_api import async_playwright
from scripts.test_match_views import expected, all_expected, row_ids, wait_ids, choose_season

ART=ROOT/'test-results'/'italy'
ART.mkdir(parents=True,exist_ok=True)


async def run(url=None):
    db.initialize();errors=[];checks=0
    seasons=[item['season'] for item in db.all_seasons() if item['league']=='italy']
    assert len(seasons)==9 and min(seasons)=='2018-19' and max(seasons)=='2026-27'
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        context=await browser.new_context(viewport={'width':1440,'height':1100},accept_downloads=True,offline=not bool(url))
        page=await context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
        if url:
            await page.goto(url,wait_until='networkidle',timeout=60000)
        else:
            await page.set_content((ROOT/'site/index.html').read_text(),wait_until='load')
        await page.wait_for_function("n=>document.querySelectorAll('#league-nav button').length===n",arg=len(LEAGUES))
        await page.locator('#league-nav button').nth(list(LEAGUES).index('italy')).click()
        await page.wait_for_function("document.querySelector('#page-title').innerText.includes('Serie A')")
        assert await page.locator('#league-nav button[aria-pressed=true]').inner_text()
        assert await page.locator('#season-select option').count()==9
        assert await page.locator('#kpi-grid article').count()==8
        assert await page.locator('#matches-table th:visible').count()==10
        assert await page.locator('[data-drought-metric=red]').is_visible()
        checks+=5
        for season in seasons:
            for order,selected in (('round',''),('chronological',''),('round','auto')):
                params={'league':'italy','season':season,'order':order,'match_round':selected,'page_size':200}
                actual=await page.evaluate("async q=>(await fetch('/api/dashboard?'+q)).json()",urlencode(params))
                target=expected('italy',season,order,selected)
                for key in ('summary','frequency','ht_droughts','droughts','scorelines','team_stats','options','pagination'):
                    assert actual[key]==target[key],(season,order,selected,key)
                    checks+=1
                assert [g['id'] for g in actual['games']]==[g['id'] for g in target['games']]
                assert actual['droughts']['red'] is not None
                checks+=2
            count=target['summary']['total']
            assert count==(381 if season=='2022-23' else 380),(season,count)
            checks+=1
        playoff=expected('italy','2022-23',scope='Desempate por la permanencia')
        assert len(playoff['games'])==1
        assert (playoff['games'][0]['ht_home'],playoff['games'][0]['ht_away'],playoff['games'][0]['ft_home'],playoff['games'][0]['ft_away'])==(1,3,1,3)
        assert playoff['games'][0].get('round') is None and playoff['games'][0].get('round_not_applicable') is True
        assert not any(json.loads(item['id'])[2]==39 for item in expected('italy','2022-23')['options']['rounds'])
        checks+=4

        # The first historical matchday demonstrates real green cells and card
        # icons without changing the row/cell geometry or displaying a red zero.
        await choose_season(page,'2018-19')
        await page.locator('[data-order=round]').click()
        games=all_expected('italy','2018-19',match_round='auto')
        await wait_ids(page,[g['id'] for g in games])
        assert await page.locator('#round-buttons [data-round-id]').count()==38
        for g in games:
            index=[game['id'] for game in games].index(g['id'])
            row=page.locator('#matches-body tr[data-match-id]').nth(index)
            assert await row.locator('.btts-cell').count()==(7 if btts(pair(g,'ht')) and not g.get('administrative') else 0)
            if g.get('red')==0:
                assert (await row.locator('td').nth(8).inner_text()).strip()==''
                assert await row.locator('td').nth(8).locator('.tiny-card').count()==0
            elif g.get('red') is not None:
                assert await row.locator('td').nth(8).locator('.tiny-card.red').count()==1
            checks+=2
        async with page.expect_download() as event:
            await page.locator('#export-link').click()
        download=await event.value;csv_path=ART/download.suggested_filename;await download.save_as(csv_path)
        with csv_path.open(encoding='utf-8-sig',newline='') as source:
            table=list(csv.reader(source,delimiter=';'))
        assert len(table)==len(games)+1
        assert table[0].index('Córners')<table[0].index('Amarillas')<table[0].index('Rojas')
        checks+=2
        async with page.expect_download() as event:
            await page.locator('#history-link').click()
        download=await event.value;book_path=ART/download.suggested_filename;await download.save_as(book_path)
        book=load_workbook(book_path)
        assert len(book.sheetnames)==10 and 'Notas' in book.sheetnames
        assert {f'SERIE A {season[2:]}' for season in seasons}<=set(book.sheetnames)
        assert all(sheet.freeze_panes is None for sheet in book)
        checks+=3
        for season in seasons:
            ws=book[f'SERIE A {season[2:]}']
            assert [c.value for c in ws[30]][:10]==['N.º','Fecha · hora COL','Jornada','Local','Visitante','HT','FT','Córners','Amarillas','Rojas']
            raw=db.games('italy',season)
            indexed={}
            for g in raw:
                indexed.setdefault((g['home'],g['away']),[]).append(g)
            rows=[r for r in ws.iter_rows(min_row=31) if isinstance(r[0].value,int) and (r[3].value,r[4].value) in indexed]
            assert len(rows)==len(raw),(season,len(rows),len(raw))
            anchors={(image.anchor._from.row+1,image.anchor._from.col+1) for image in ws._images}
            for row in rows:
                n=row[0].row;candidates=indexed[(row[3].value,row[4].value)]
                if len(candidates)>1:
                    numbered=[g for g in candidates if g.get('round')==row[2].value]
                    candidates=numbered or [g for g in candidates if g.get('round') is None and g.get('phase') in str(row[10].value)]
                assert len(candidates)==1,(season,row[3].value,row[4].value,row[10].value)
                g=candidates[0]
                if g.get('round_not_applicable'):
                    assert 'pendiente' not in str(row[2].value).lower()
                if g['status']=='finished' and not g.get('administrative') and g.get('red')==0:
                    assert ws.cell(n,10).value is None and ws.cell(n,10).fill.patternType is None
                    assert (n,10) not in anchors
                if g.get('ht_home') is not None and g.get('ht_away') is not None:
                    assert ws.cell(n,6).data_type=='s'
                if g['status']=='finished' and not g.get('administrative') and btts(pair(g,'ht')):
                    green=ws.cell(n,1).fill.fgColor.rgb
                    assert ws.cell(n,1).fill.patternType=='solid'
                    assert all(ws.cell(n,c).fill.fgColor.rgb==green for c in range(1,9))
                    assert ws.cell(n,9).fill.fgColor.rgb!=green
                checks+=1
            checks+=3
        book.close()

        # Accumulation must work for a league with the extra columns as well.
        await page.locator('[data-order=chronological]').click()
        ordered=all_expected('italy','2018-19','chronological')
        ids=[g['id'] for g in ordered]
        await wait_ids(page,ids[:40])
        await page.evaluate("window.__italyFirst=document.querySelector('#matches-body tr[data-match-id]')")
        await page.locator('#load-more').click()
        await wait_ids(page,ids[:80])
        assert await page.evaluate("window.__italyFirst===document.querySelector('#matches-body tr[data-match-id]')")
        assert len(set(await row_ids(page)))==80
        checks+=2
        await page.locator('#drought-title').scroll_into_view_if_needed()
        await page.screenshot(path=str(ART/'desktop.png'),full_page=False)
        for width in (390,320):
            await page.set_viewport_size({'width':width,'height':844})
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
            await page.screenshot(path=str(ART/f'mobile-{width}.png'),full_page=False)
            checks+=1
        assert not errors,errors
        await browser.close()
    report={'passed':True,'checks':checks,'url':url,'javascript_errors':errors,'seasons':seasons}
    (ART/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url')
    asyncio.run(run(parser.parse_args().url))
