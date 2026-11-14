"""Cross-check captured HT/FT against explicit match reports before release.

Only the supplied staging JSON files are changed. No application data is written.
Incomplete reports leave the existing independently observed value in place and
are listed in the QA output. A verified discrepancy uses the explicit periods.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.import_paraguay_history import fetch,parse_report


def verify(directory):
    directory=Path(directory)
    files=[directory/'history_games.json',directory/'current_games.json']
    groups={path:json.loads(path.read_text()) for path in files}
    jobs=[]
    for path,games in groups.items():
        cache=directory/('provider-cache' if path.name.startswith('current') else 'history-cache')/'reports'
        for game in games:
            if game['status']=='finished' and not game.get('administrative'):
                jobs.append((game,cache/(game['archive_id']+'.html.gz')))
    def read(item):
        game,path=item;raw=fetch(game['archive_url'],path);report=parse_report(raw,game)
        return game,report,hashlib.sha256(raw).hexdigest()
    failures=[];differences=[];verified=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending={pool.submit(read,item):item[0] for item in jobs}
        for index,future in enumerate(as_completed(pending),1):
            try:
                game,report,digest=future.result()
                before=[game.get('ht_home'),game.get('ht_away')]
                after=[report['ht_home'],report['ht_away']]
                if before!=after:
                    differences.append({'id':game['id'],'date':game['date'],'home':game['home'],'away':game['away'],
                                        'previous':before,'previous_source':game.get('ht_source_url'),
                                        'verified':after,'source_url':report['ht_source_url']})
                game.update({key:report[key] for key in ('ht_home','ht_away','ht_source','ht_source_url')})
                game['ht_verified']=True
                verified.append({'id':game['id'],'url':game['archive_url'],'sha256':digest})
            except Exception as error:failures.append({'id':pending[future]['id'],'error':str(error)})
            if index%100==0:print(f'Cross-checked HT/FT {index}/{len(jobs)}; differences {len(differences)}; unavailable {len(failures)}',flush=True)
    for path,games in groups.items():path.write_text(json.dumps(games,ensure_ascii=False,indent=2)+'\n')
    report={'captured_at':datetime.now(timezone.utc).isoformat(),'checked':len(verified),'differences':differences,'unavailable':failures}
    (directory/'explicit_reports_qa.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    (directory/'explicit_reports_evidence.json').write_text(json.dumps(verified,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--directory',type=Path,default=Path('/workspace/paraguay-data'))
    args=parser.parse_args();verify(args.directory)
