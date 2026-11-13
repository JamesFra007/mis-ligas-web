"""Browser verification of real Polish data, coverage and workbook downloads."""
import argparse
import asyncio
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app import db
from app.leagues import LEAGUES
from app.main import dashboard
from openpyxl import load_workbook
from playwright.async_api import async_playwright

ART=ROOT/'test-results'/'poland';ART.mkdir(parents=True,exist_ok=True)

def expected(season,order='round'):
    return dashboard(league='poland',season=season,scope='all',team='all',condition='all',status='all',search='',page=1,page_size=200,order=order)


async def run(url=None):
    errors=[];checks=0
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        context=await browser.new_context(viewport={'width':1440,'height':1100},accept_downloads=True,offline=not bool(url))
        page=await context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
        if url:await page.goto(url,wait_until='networkidle',timeout=60000)
        else:await page.set_content((ROOT/'site/index.html').read_text(),wait_until='load')
        await page.wait_for_function("count=>document.querySelectorAll('#league-nav button').length===count",arg=len(LEAGUES))
        assert await page.locator('#league-nav button').count()==len(LEAGUES);checks+=1
        await page.locator('[data-order=chronological]').click()
        # Existing leagues retain their own columns and statistics.
        await page.wait_for_function("document.querySelectorAll('#matches-body tr').length===40")
        assert await page.locator('#kpi-grid article').count()==8;checks+=1
        assert await page.locator('#matches-table th:visible').count()==10;checks+=1
        await page.locator('#league-nav button').nth(1).click()
        await page.wait_for_function("document.querySelector('#page-title').innerText.includes('BetPlay')")
        assert await page.locator('#matches-table th:visible').count()==8;checks+=1
        await page.locator('#league-nav button').nth(3).click()
        await page.wait_for_function("document.querySelector('#page-title').innerText.includes('Ekstraklasa')")
        assert await page.locator('#kpi-grid article').count()==6;checks+=1
        assert await page.locator('#matches-table th:visible').count()==7;checks+=1
        assert await page.locator('#season-select option').count()==9;checks+=1
        for name in ('Córners','Amarillas','Rojas'):
            assert name not in await page.locator('#matches-table thead').inner_text();checks+=1
        seasons=[s['season'] for s in db.all_seasons() if s['league']=='poland']
        for season in seasons:
            for order in ('round','chronological'):
                query=urlencode({'league':'poland','season':season,'order':order,'page_size':200})
                actual=await page.evaluate("async q=>(await fetch('/api/dashboard?'+q)).json()",query)
                target=expected(season,order)
                for key in ('summary','frequency','scorelines','team_stats','pagination','options'):
                    assert actual[key]==target[key],(season,order,key,actual[key],target[key]);checks+=1
                assert [g['id'] for g in actual['games']]==[g['id'] for g in target['games']];checks+=1
        await page.select_option('#season-select','2018-19');await page.wait_for_function("document.querySelector('#page-title').innerText.includes('2018/19')")
        assert await page.locator('#match-count').inner_text()=='296';checks+=1
        assert await page.locator('#matches-body tr:has(.btts-cell)').count()>0;checks+=1
        assert await page.locator('#matches-body tr:has(.btts-cell)').first.locator('.btts-cell').count()==6;checks+=1
        async with page.expect_download() as event:await page.locator('#export-link').click()
        download=await event.value;path=ART/download.suggested_filename;await download.save_as(path)
        lines=path.read_text(encoding='utf-8-sig').splitlines();assert len(lines)==297;checks+=1
        for word in ('Córners','Amarillas','Rojas'):assert word not in lines[0];checks+=1
        async with page.expect_download() as event:await page.locator('#history-link').click()
        download=await event.value;path=ART/download.suggested_filename;await download.save_as(path)
        book=load_workbook(path);assert len(book.sheetnames)==10;checks+=1
        wanted=ROOT/'data/history/Polonia_Ekstraklasa_2018_2027.xlsx'
        # Hosted CI regenerates timestamps, so semantic checks are portable.
        if not url:assert sha256(path.read_bytes()).hexdigest()==sha256(wanted.read_bytes()).hexdigest();checks+=1
        for ws in book:
            assert ws.freeze_panes is None;checks+=1
            if ws.title=='Notas':continue
            headers=[c.value for c in ws[23]]
            assert headers==['N.º','Fecha · hora COL','Jornada','Local','Visitante','HT','FT','Estado','Observación'];checks+=1
            assert all(c.value not in ('Córners','Amarillas','Rojas') for c in ws[23]);checks+=1
            rows=[r for r in ws.iter_rows(min_row=24) if isinstance(r[0].value,int)]
            assert len(rows)==(296 if ws.title in ('EKSTRA 18-19','EKSTRA 19-20') else 240 if ws.title=='EKSTRA 20-21' else 306);checks+=1
        await page.locator('[data-order=chronological]').click();await page.wait_for_function("document.querySelector('[data-order=chronological]').getAttribute('aria-pressed')==='true'");checks+=1
        await page.select_option('#season-select','2026-27');await page.wait_for_function("document.querySelector('#page-title').innerText.includes('2026/27')")
        model=await page.evaluate("async()=> (await fetch('/api/forecast?league=poland')).json()")
        assert model['status']=='ok';assert len(model['upcoming'])>0;checks+=2
        assert all(g.get('time') and g.get('kickoff') for g in model['upcoming']);checks+=1
        await page.evaluate('scrollTo(0,0)');await page.wait_for_timeout(300)
        await page.screenshot(path=str(ART/'desktop.png'),full_page=False)
        await page.set_viewport_size({'width':390,'height':844});await page.screenshot(path=str(ART/'mobile.png'),full_page=False)
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth+1');checks+=1
        await page.set_viewport_size({'width':320,'height':844});assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth+1');checks+=1
        assert not errors,errors
        await browser.close()
    result={'passed':True,'checks':checks,'javascript_errors':errors,'url':url,'seasons':seasons};(ART/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url');args=parser.parse_args();asyncio.run(run(args.url))
