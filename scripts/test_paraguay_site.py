"""Verify Paraguayan HT/FT, observed corners and downloadable annual workbooks."""
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
from app.stats import btts, pair, known
from openpyxl import load_workbook
from playwright.async_api import async_playwright
from scripts.test_match_views import expected, all_expected, row_ids, wait_ids, choose_season, numbered_order_is_valid

ART=ROOT/'test-results'/'paraguay'
ART.mkdir(parents=True,exist_ok=True)
COUNTS={'2018':264,'2019':264,'2020':205,'2021':182,'2022':264,'2023':264,'2024':264,'2025':264,'2026':264}
CARD_FIELDS=('yellow','red','yellow_home','yellow_away','red_home','red_away')


async def run(url=None):
    db.initialize();errors=[];checks=0
    seasons=[row['season'] for row in db.all_seasons() if row['league']=='paraguay']
    assert set(seasons)==set(COUNTS)
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        context=await browser.new_context(viewport={'width':1440,'height':1100},accept_downloads=True,offline=not bool(url))
        page=await context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
        if url:await page.goto(url,wait_until='networkidle',timeout=60000)
        else:await page.set_content((ROOT/'site/index.html').read_text(),wait_until='load')
        await page.wait_for_function("n=>document.querySelectorAll('#league-nav button').length===n",arg=len(LEAGUES))
        await page.locator('#league-nav button').nth(list(LEAGUES).index('paraguay')).click()
        await page.wait_for_function("document.querySelector('#page-description').innerText.includes('Paraguay')")
        await page.wait_for_function("!document.querySelector('#matches-table').classList.contains('loading-table')")
        assert await page.locator('#season-select option').count()==9
        assert await page.locator('#matches-table th:visible').count()==8
        assert await page.locator('[data-drought-metric=red]').count()==0
        assert await page.locator('.tiny-card').count()==0
        assert 'Tarjetas' not in await page.locator('#kpi-grid').inner_text()
        header_text=await page.locator('#matches-table thead').inner_text()
        assert not any(word in header_text for word in ('Amarillas','Rojas'))
        checks+=6

        for season in seasons:
            raw=db.games('paraguay',season)
            assert len(raw)==COUNTS[season]
            assert all(g.get(field) is None for g in raw for field in CARD_FIELDS)
            for order,selected in (('round',''),('chronological',''),('round','auto')):
                params={'league':'paraguay','season':season,'order':order,'match_round':selected,'page_size':200}
                actual=await page.evaluate("async q=>(await fetch('/api/dashboard?'+q)).json()",urlencode(params))
                target=expected('paraguay',season,order,selected)
                for key in ('summary','frequency','ht_droughts','droughts','scorelines','team_stats','options','pagination'):
                    assert actual[key]==target[key],(season,order,selected,key)
                    checks+=1
                assert [g['id'] for g in actual['games']]==[g['id'] for g in target['games']]
                assert actual['droughts']['red'] is None
                checks+=2
            played=[g for g in raw if g['status']=='finished' and not g.get('administrative') and known(pair(g,'ft'))]
            observed=[g['corners'] for g in played if g.get('corners') is not None]
            assert target['summary']['corners_base']==len(observed)
            assert target['summary']['corners_missing']==len(played)-len(observed)
            assert {'Apertura','Clausura'}<={item['id'] for item in target['options']['scopes']}
            checks+=5

        year2020=db.games('paraguay','2020')
        finals=[g for g in year2020 if g.get('scope')=='Clausura' and 'final' in (g.get('phase') or '').lower()]
        assert len(finals)==7
        assert not any(g.get('round')==39 for g in finals)
        await choose_season(page,'2020')
        await page.select_option('#scope-select','Clausura')
        await page.wait_for_function("!document.querySelector('#matches-table').classList.contains('loading-table')")
        labels=await page.locator('#round-buttons button').evaluate_all("nodes=>nodes.map(node=>node.title)")
        assert any('final' in label.lower() for label in labels)
        for g in finals:
            wanted=next(item['id'] for item in expected('paraguay','2020',scope='Clausura')['options']['rounds'] if json.loads(item['id'])[1]==g['phase'] and json.loads(item['id'])[2]==g.get('round'))
            buttons=page.locator('#round-buttons [data-round-id]')
            index=await buttons.evaluate_all("(nodes,id)=>nodes.findIndex(node=>node.dataset.roundId===id)",wanted)
            await buttons.nth(index).click()
            selected=all_expected('paraguay','2020',match_round=wanted,scope='Clausura')
            await wait_ids(page,[row['id'] for row in selected])
            assert all(row['phase']==g['phase'] for row in selected)
            checks+=1
        checks+=3

        # The 2021 promotion belongs to the year's first-division history. Its
        # two legs must not be lost or numbered as ordinary league matchdays.
        promotion=[g for g in db.games('paraguay','2021') if g.get('scope')=='Promoción']
        assert len(promotion)==2
        assert {g.get('phase') for g in promotion}=={'Promoción'}
        assert all(g.get('round') is None and g.get('round_not_applicable') is True for g in promotion)
        assert all({g['home'],g['away']}=={'Sportivo Luqueño','Sportivo Ameliano'} for g in promotion)
        assert len({g['id'] for g in promotion})==2
        assert len({g['date'] for g in promotion})==2
        await choose_season(page,'2021')
        await page.select_option('#scope-select','Promoción')
        promotion_data=all_expected('paraguay','2021',match_round='auto',scope='Promoción')
        await wait_ids(page,[g['id'] for g in promotion_data])
        assert len(promotion_data)==2
        assert all(g.get('scope')=='Promoción' for g in promotion_data)
        promotion_label=await page.locator('#round-buttons [aria-pressed=true]').get_attribute('title')
        assert 'Promoción' in promotion_label and 'pendiente' not in promotion_label.lower()
        checks+=9

        await choose_season(page,'2018')
        await page.locator('[data-order=round]').click()
        games=all_expected('paraguay','2018',match_round='auto')
        await wait_ids(page,[g['id'] for g in games])
        assert await numbered_order_is_valid(page)
        assert await page.locator('[data-drought-metric=red]').count()==0
        for index,g in enumerate(games):
            row=page.locator('#matches-body tr[data-match-id]').nth(index)
            assert await row.locator('td').count()==8
            assert await row.locator('.btts-cell').count()==(7 if g['status']=='finished' and not g.get('administrative') and btts(pair(g,'ht')) else 0)
            corner_text=(await row.locator('td').nth(6).inner_text()).strip()
            if g.get('corners') is None:assert corner_text=='N/D'
            else:assert corner_text==str(g['corners'])
            checks+=3
        async with page.expect_download() as event:await page.locator('#export-link').click()
        download=await event.value;csv_path=ART/download.suggested_filename;await download.save_as(csv_path)
        with csv_path.open(encoding='utf-8-sig',newline='') as source:table=list(csv.reader(source,delimiter=';'))
        assert len(table)==len(games)+1
        assert table[0].index('FT')+1==table[0].index('Córners')
        assert not {'Amarillas','Rojas'}&set(table[0])
        checks+=3

        async with page.expect_download() as event:await page.locator('#history-link').click()
        download=await event.value;book_path=ART/download.suggested_filename;await download.save_as(book_path)
        book=load_workbook(book_path)
        assert len(book.sheetnames)==10 and 'Notas' in book.sheetnames
        assert {f'PARAGUAY {season}' for season in seasons}<=set(book.sheetnames)
        assert all(sheet.freeze_panes is None for sheet in book)
        assert all(not sheet._images for sheet in book)
        checks+=4
        for season in seasons:
            ws=book[f'PARAGUAY {season}']
            assert [c.value for c in ws[40]][:12]==['N.º','Fecha · hora COL','Jornada','Local','Visitante','HT','FT','Córners','Torneo','Fase','Estado','Observación']
            raw=db.games('paraguay',season)
            pairs={(g['home'],g['away']) for g in raw}
            rows=[row for row in ws.iter_rows(min_row=41) if type(row[0].value) is int and (row[3].value,row[4].value) in pairs]
            assert len(rows)==len(raw)
            for cells in rows:
                number=cells[0].value;g=raw[number-1];n=cells[0].row
                assert (cells[3].value,cells[4].value,cells[8].value,cells[9].value)==(g['home'],g['away'],g['scope'],g['phase'])
                assert not any(isinstance(cell.value,str) and 'https://' in cell.value for cell in cells[:12])
                if g['status']=='finished' and not g.get('administrative'):
                    assert ws.cell(n,8).value==(g['corners'] if g.get('corners') is not None else 'N/D')
                elif g['status']!='finished':
                    assert ws.cell(n,6).value is None and ws.cell(n,7).value is None and ws.cell(n,8).value is None
                for col,period in ((6,'ht'),(7,'ft')):
                    if known(pair(g,period)) and not g.get('administrative'):assert ws.cell(n,col).data_type=='s'
                if g.get('round_not_applicable'):assert 'pendiente' not in str(cells[2].value).lower()
                if g['status']=='finished' and not g.get('administrative') and btts(pair(g,'ht')):
                    green=ws.cell(n,1).fill.fgColor.rgb
                    assert ws.cell(n,1).fill.patternType=='solid'
                    assert all(ws.cell(n,col).fill.fgColor.rgb==green for col in range(1,9))
                    assert ws.cell(n,9).fill.fgColor.rgb!=green
                checks+=1
            checks+=2
        book.close()

        await page.locator('[data-order=chronological]').click()
        ids=[g['id'] for g in all_expected('paraguay','2018','chronological')]
        await wait_ids(page,ids[:40]);await page.evaluate("window.__paraguayFirst=document.querySelector('#matches-body tr[data-match-id]')")
        while len(await row_ids(page))<len(ids):
            before=len(await row_ids(page));await page.locator('#load-more').click()
            await wait_ids(page,ids[:min(before+40,len(ids))])
            assert await page.evaluate("window.__paraguayFirst===document.querySelector('#matches-body tr[data-match-id]')")
            checks+=1
        assert not await page.locator('#load-more').is_visible()
        assert len(set(await row_ids(page)))==len(ids)
        checks+=2
        model=await page.evaluate("async()=>(await fetch('/api/forecast?league=paraguay')).json()")
        assert isinstance(model.get('upcoming'),list)
        assert all(g.get('time') and g.get('kickoff') for g in model['upcoming'])
        checks+=2
        await page.locator('#drought-title').scroll_into_view_if_needed()
        await page.screenshot(path=str(ART/'desktop.png'),full_page=False)
        for width in (390,320):
            await page.set_viewport_size({'width':width,'height':844})
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
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
