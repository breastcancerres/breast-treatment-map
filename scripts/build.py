#!/usr/bin/env python3
"""Create one portable HTML document; no dependencies or runtime API calls."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def validate(catalog, state):
    if not state.get('lastRun'):
        raise ValueError('No completed source collection: refusing an empty clinical snapshot')
    drugs = {x['id'] for x in catalog['drugs']}
    trials = {x['id'] for x in catalog['trials']}
    indications = {x['id'] for x in catalog['indications']}
    sources = {x['id']: x for x in catalog['approvalSources']}
    regimens = {x['id']: x for x in catalog.get('regimens', [])}
    if len(regimens) != len(catalog.get('regimens', [])):
        raise ValueError('Duplicate regimen IDs')
    if len(indications) != len(catalog['indications']):
        raise ValueError('Duplicate indication IDs')
    for row in catalog['indications']:
        assert row['drugId'] in drugs and set(row['trialIds']) <= trials
        assert row['setting'] in {'early', 'advanced'}
        regimen = regimens.get(row.get('regimenId'))
        if not regimen or regimen['drugId'] != row['drugId']:
            raise ValueError('Missing or mismatched regimen for '+row['id'])
        if regimen['kind'] == 'investigational' and not set(regimen['trialIds']) & set(row['trialIds']):
            raise ValueError('Research regimen does not match the indication trial')
    for regimen in regimens.values():
        assert regimen['kind'] in {'approved', 'investigational'}
        assert regimen['reviewedOn'] <= catalog['reviewedOn']
        assert regimen['stages'] and regimen['duration'] and regimen['sources']
        for stage in regimen['stages']:
            assert stage['title'] and stage['agents']
            for agent in stage['agents']:
                assert all(agent.get(k) for k in ['name', 'dose', 'route', 'schedule'])
        for source in regimen['sources']:
            assert source['label'] and source['url'].startswith('https://')
    for key, approval in state['approvals'].items():
        assert key in indications
        assert key in sources[approval['sourceId']]['indicationIds']
        assert approval['jurisdiction'] == 'US'
    for key, trial in state['trials'].items():
        assert key in trials and trial['id'] == key
    for key, event in state['events'].items():
        assert key == event['id'] and set(event['drugIds']) <= drugs

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--automation-configured', action='store_true')
    args = parser.parse_args()
    catalog = json.loads((ROOT/'data/catalog.json').read_text())
    state = json.loads((ROOT/'data/state.json').read_text())
    validate(catalog, state)
    data = json.dumps({'catalog':catalog,'state':state,'automationConfigured':args.automation_configured},
                      ensure_ascii=False, separators=(',',':')).replace('<',r'\u003c').replace('\u2028',r'\u2028').replace('\u2029',r'\u2029')
    output = (ROOT/'web/index.template.html').read_text()
    output = output.replace('/*__STYLES__*/',(ROOT/'web/style.css').read_text())
    output = output.replace('/*__APP__*/',(ROOT/'web/app.js').read_text())
    output = output.replace('/*__DATA__*/',data)
    assert '/*__' not in output
    dist=ROOT/'dist';dist.mkdir(exist_ok=True)
    (dist/'index.html').write_text(output,encoding='utf-8')
    (dist/'.nojekyll').write_text('')
    print('Built dist/index.html:',len(output.encode()),'bytes')

if __name__ == '__main__':
    main()
