#!/usr/bin/env python3
"""Free HTTP collector. Requires Python 3.10+; no packages or paid APIs."""
import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

from core import (new_state, source_ok, source_error, proof_for, apply_approval,
                  normalize_trial, apply_trial, discover_fda, add_publication)

ROOT = Path(__file__).resolve().parents[1]
FDA_INDEX = 'https://www.fda.gov/drugs/resources-information-approved-drugs/oncology-cancerhematologic-malignancies-approval-notifications'
EUTILS = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'


def get_text(url, attempts=2):
    for attempt in range(attempts):
        try:
            request=urllib.request.Request(url,headers={'User-Agent':'BreastTreatmentMap/0.1 (public evidence monitor)',
                                                        'Accept':'application/json,text/html,*/*'})
            with urllib.request.urlopen(request, timeout=15) as response:
                # Bounded responses; no remote commands or linked script execution.
                raw=response.read(5_000_001)
                if len(raw)>5_000_000:
                    raise ValueError('Source response exceeds 5 MB')
                return raw.decode('utf-8')
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
            if attempt+1 == attempts:
                raise
            time.sleep(1.0)


def pubmed(state, catalog, now):
    initial = not bool(state['publications'])
    pmid_trials = {}
    # Sequential requests respect the unauthenticated NCBI rate limit.
    for trial in catalog['trials']:
        key='pubmed:'+trial['id']
        # The trial must be a PubMed Secondary Source ID, not merely mentioned
        # in a reference list or in a general review.
        term=trial['id']+'[si]'
        query=EUTILS+'esearch.fcgi?'+urllib.parse.urlencode({
            'db':'pubmed','term':term,'retmode':'json','retmax':20,'sort':'pub_date',
            'tool':'breast-treatment-map'})
        try:
            result=json.loads(get_text(query))
            if 'esearchresult' not in result:
                raise ValueError('Unexpected PubMed response')
            ids=result['esearchresult']['idlist']
            # Registry links supplement query results; absence never removes records.
            ids=list(set(ids)|set(state['trials'].get(trial['id'],{}).get('referencePmids',[])))
            for pmid in ids:
                pmid_trials.setdefault(pmid,[]).append(trial['id'])
            source_ok(state,key,'https://pubmed.ncbi.nlm.nih.gov/?term='+trial['id'],now)
        except Exception as error:
            source_error(state,key,query,now,error)
        time.sleep(.4)
    trial_drug={t['id']:t['drugId'] for t in catalog['trials']}
    ids=sorted(pmid_trials)
    for start in range(0,len(ids),80):
        batch=ids[start:start+80]
        query=EUTILS+'esummary.fcgi?'+urllib.parse.urlencode({'db':'pubmed','id':','.join(batch),'retmode':'json'})
        try:
            result=json.loads(get_text(query))['result']
            for pmid in batch:
                row=result.get(pmid)
                if not row or row.get('error'):
                    continue
                add_publication(state,{'pmid':pmid,'title':row['title'],
                                      'publicationDate':row.get('pubdate'),
                                      'trialIds':sorted(pmid_trials[pmid]),
                                      'drugIds':sorted({trial_drug[nct] for nct in pmid_trials[pmid]})},
                                now,emit_event=not initial)
            source_ok(state,'pubmed:metadata',EUTILS+'esummary.fcgi',now)
        except Exception as error:
            source_error(state,'pubmed:metadata',EUTILS+'esummary.fcgi',now,error)
        time.sleep(.4)


def run(catalog,state,now,fetch=get_text,include_pubmed=True):
    state=copy.deepcopy(state)
    state['asOf']=now[:10]
    # Independent official-source reads are bounded and fetched once per URL.
    # Mutations remain sequential; PubMed requests below remain rate-limited.
    urls={FDA_INDEX}
    urls.update('https://clinicaltrials.gov/api/v2/studies/'+t['id'] for t in catalog['trials'])
    urls.update(s['url'] for s in catalog['approvalSources'])
    urls.update(d['labelUrl'] for d in catalog['drugs'])
    original_fetch=fetch
    def retrieve(url):
        try:
            return url,original_fetch(url)
        except Exception as error:
            return url,error
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses=dict(pool.map(retrieve,sorted(urls)))
    def cached_fetch(url):
        value=responses[url]
        if isinstance(value,Exception):
            raise value
        return value
    fetch=cached_fetch
    for definition in catalog['trials']:
        nct=definition['id'];key='ctg:'+nct
        url='https://clinicaltrials.gov/api/v2/studies/'+nct
        try:
            raw=json.loads(fetch(url))
            apply_trial(state,normalize_trial(raw,nct),definition,now)
            source_ok(state,key,url,now)
        except Exception as error:
            source_error(state,key,url,now,error)
    for source in catalog['approvalSources']:
        key='fda:'+source['id']
        try:
            raw=fetch(source['url'])
            proof=proof_for(source,raw)
            apply_approval(state,source,proof,now)
            state['observations'][source['id']]={'sha256':proof['sha256'],'checkedAt':now}
            source_ok(state,key,source['url'],now)
        except Exception as error:
            source_error(state,key,source['url'],now,error)
    try:
        discover_fda(state,catalog,fetch(FDA_INDEX),now)
        source_ok(state,'fda:index',FDA_INDEX,now)
    except Exception as error:
        source_error(state,'fda:index',FDA_INDEX,now,error)
    # Observe labels separately. A missing indication in an older label never
    # overrides a later FDA announcement.
    for drug in catalog['drugs']:
        key='fda:label-'+drug['id'];url=drug['labelUrl']
        try:
            obj=json.loads(fetch(url))
            rows=obj.get('results',[])
            if not rows:
                raise ValueError('No label records')
            row=max(rows,key=lambda x:x.get('effective_time',''))
            from core import digest,event
            text=' '.join(row.get('indications_and_usage',[]))
            if not text:
                raise ValueError('No indications section')
            observation={'sha256':digest(text),'effectiveTime':row.get('effective_time'),
                         'datasetUpdatedOn':obj.get('meta',{}).get('last_updated')}
            old=state['observations'].get(key)
            if old and old['sha256']!=observation['sha256']:
                event(state,['label-change',drug['id'],observation['sha256']],
                      kind='label_changed',drug_ids=[drug['id']],
                      title='Обновлён раздел показаний в инструкции '+drug['brand'],
                      url=url,now=now,review=True)
                for use in catalog['indications']:
                    if use['drugId']==drug['id'] and use['id'] in state['approvals']:
                        state['approvals'][use['id']]['needsReview']=True
            state['observations'][key]=observation
            source_ok(state,key,url,now)
        except Exception as error:
            source_error(state,key,url,now,error)
    if include_pubmed:
        pubmed(state,catalog,now)
    attempted=[s for s in state['sources'].values() if s['lastAttempt']==now]
    successes=sum(s['status']=='ok' for s in attempted)
    state['lastRun']={'finishedAt':now,'successes':successes,'attempts':len(attempted),
                      'status':'ok' if successes==len(attempted) and successes else ('partial' if successes else 'failed')}
    return state


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--skip-pubmed',action='store_true')
    args=parser.parse_args()
    catalog=json.loads((ROOT/'data/catalog.json').read_text())
    path=ROOT/'data/state.json'
    now=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
    state=json.loads(path.read_text()) if path.exists() else new_state(now[:10])
    updated=run(catalog,state,now,include_pubmed=not args.skip_pubmed)
    updated['lastRun']['startedAt']=now
    updated['lastRun']['finishedAt']=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(updated,ensure_ascii=False,indent=2)+'\n')
    tmp.replace(path)
    print(json.dumps(updated['lastRun'],ensure_ascii=False))
    print('Approvals:',len(updated['approvals']),'Trials:',len(updated['trials']),
          'Publications:',len(updated['publications']),'Events:',len(updated['events']))
    if updated['lastRun']['status']=='failed':
        raise SystemExit(1)


if __name__=='__main__':
    main()
