"""Conservative state transitions. Clinical interpretation is never inferred."""
import copy
import hashlib
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlparse, urljoin


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def new_state(as_of):
    return {'schemaVersion': 1, 'asOf': as_of, 'approvals': {}, 'trials': {},
            'events': {}, 'publications': {}, 'sources': {}, 'observations': {},
            'lastRun': None}


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'head', 'nav', 'footer'}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'head', 'nav', 'footer'} and self.skip:
            self.skip -= 1

    def handle_data(self, value):
        if not self.skip:
            self.parts.append(value)


def plain_html(html):
    parser = TextParser()
    parser.feed(html)
    return ' '.join(' '.join(parser.parts).split())


def article_text(html):
    match = re.search(r'<h1\b[^>]*>(.*?)</h1>', html, re.S | re.I)
    if not match:
        raise ValueError('Official document has no h1')
    heading = plain_html(match.group(1))
    tail = plain_html(html[match.start():])
    # Exclude navigation/footer and page metadata, keep the clinical document.
    for stop in ['Healthcare professionals should report', 'For assistance with single-patient',
                 'Content current as of:', 'Follow the Oncology Center']:
        tail = tail.split(stop, 1)[0]
    return heading, tail.strip()


def source_ok(state, key, url, now):
    state['sources'][key] = {'url': url, 'status': 'ok',
                             'lastAttempt': now, 'lastSuccess': now}


def source_error(state, key, url, now, error):
    previous = state['sources'].get(key, {})
    state['sources'][key] = {'url': url, 'status': 'error', 'lastAttempt': now,
                             'lastSuccess': previous.get('lastSuccess'),
                             'error': str(error)[:200]}


def event(state, identity, *, kind, drug_ids, title, url, now,
          effective_on=None, review=False, indication_ids=None):
    key = digest(identity)[:24]
    if key in state['events']:
        return False
    state['events'][key] = {'id': key, 'kind': kind, 'drugIds': sorted(drug_ids),
                            'indicationIds': indication_ids or [], 'title': title,
                            'url': url, 'observedOn': now, 'effectiveOn': effective_on,
                            'reviewRequired': review}
    return True


def proof_for(source, raw):
    if source['kind'] == 'label':
        obj = json.loads(raw) if isinstance(raw, str) else raw
        candidates = obj.get('results', [])
        candidates = [r for r in candidates if source['brand'].upper() in
                      [v.upper() for v in r.get('openfda', {}).get('brand_name', [])]]
        if not candidates:
            raise ValueError('Wrong or missing brand in label response')
        latest = max(candidates, key=lambda r:r.get('effective_time', ''))
        text = ' '.join(latest.get('indications_and_usage', []))
        if not text:
            raise ValueError('Label has no indications section')
        return {'heading': source['expectedTitle'], 'text': text,
                'sha256': digest(text), 'labelEffectiveTime': latest.get('effective_time')}
    heading, text = article_text(raw)
    return {'heading': heading, 'text': text, 'sha256': digest(text)}


def apply_approval(state, source, proof, now):
    """Only reviewed source-to-indication mappings can grant a status.

    A changed or unrecognised document creates a review event. An inaccessible
    source or an older label cannot undo a previously documented approval.
    """
    host = urlparse(source['url']).hostname
    if host not in {'www.fda.gov', 'api.fda.gov'}:
        raise ValueError('Untrusted approval source')
    text = proof['text'].lower()
    valid = (proof['heading'] == source['expectedTitle']
             and proof['sha256'] == source['reviewedSha256']
             and all(s.lower() in text for s in source['requiredPhrases']))
    if source.get('effectiveOn') and source['effectiveOn'] > state['asOf']:
        valid = False
    if not valid:
        for indication_id in source['indicationIds']:
            if indication_id in state['approvals']:
                state['approvals'][indication_id]['needsReview'] = True
        event(state, ['fda-review', source['id'], proof['sha256']],
              kind='document_changed', drug_ids=[source['drugId']],
              indication_ids=source['indicationIds'],
              title='Изменился документ FDA: требуется сверка показания',
              url=source['url'], now=now, review=True)
        return False
    for indication_id in source['indicationIds']:
        existing = state['approvals'].get(indication_id)
        # Do not silently supersede a more recent or separately reviewed decision.
        if existing and existing['sourceId'] != source['id']:
            event(state, ['approval-conflict', indication_id, source['id']],
                  kind='mapping_conflict', drug_ids=[source['drugId']],
                  title='Несколько документов для показания: требуется сверка',
                  url=source['url'], now=now, review=True)
            continue
        first_observed = existing['confirmedOn'] if existing else now
        state['approvals'][indication_id] = {
            'status': 'approved', 'jurisdiction': 'US', 'sourceId': source['id'],
            'effectiveOn': source.get('effectiveOn'), 'confirmedOn': first_observed,
            'proofSha256': proof['sha256'],
            'needsReview': bool(existing and existing.get('needsReview'))}
        if source.get('effectiveOn'):
            event(state, ['fda-approval', source['id'], indication_id],
                  kind='fda_approval', drug_ids=[source['drugId']],
                  indication_ids=[indication_id], title=source['eventTitles'][indication_id],
                  url=source['url'], now=now, effective_on=source['effectiveOn'])
    return True


def normalize_trial(raw, expected_id):
    p = raw.get('protocolSection', {})
    ident, status = p.get('identificationModule', {}), p.get('statusModule', {})
    if ident.get('nctId') != expected_id:
        raise ValueError('NCT mismatch')
    phases = p.get('designModule', {}).get('phases', [])
    if 'PHASE3' not in phases:
        raise ValueError('Record is no longer phase III; manual scope review required')
    if not status.get('overallStatus'):
        raise ValueError('Record has no status')
    references = p.get('referencesModule', {}).get('references', [])
    return {
        'id': expected_id, 'title': ident.get('briefTitle'), 'phase': phases,
        'status': status['overallStatus'],
        'updatedOn': status.get('lastUpdatePostDateStruct', {}).get('date'),
        'primaryCompletion': status.get('primaryCompletionDateStruct'),
        'completion': status.get('completionDateStruct'),
        'hasResults': bool(raw.get('hasResults')),
        'enrollment': p.get('designModule', {}).get('enrollmentInfo', {}),
        'referencePmids': sorted({r['pmid'] for r in references if r.get('pmid')
                                 and r.get('type') == 'RESULT'}),
        'whyStopped': status.get('whyStopped')
    }


def apply_trial(state, trial, definition, now):
    previous = state['trials'].get(trial['id'])
    state['trials'][trial['id']] = copy.deepcopy(trial)
    if previous and digest(previous) != digest(trial):
        event(state, ['ctg-change', trial['id'], digest(trial)],
              kind='trial_changed', drug_ids=[definition['drugId']],
              title='Обновлена запись '+definition['name'],
              url='https://clinicaltrials.gov/study/'+trial['id'], now=now)
    # Deliberately no approval/guideline changes from a registry record.


def add_publication(state, publication, now, emit_event=True):
    pmid = str(publication['pmid'])
    previous = state['publications'].get(pmid)
    if previous:
        publication['trialIds'] = sorted(set(previous['trialIds']) | set(publication['trialIds']))
        publication['drugIds'] = sorted(set(previous['drugIds']) | set(publication['drugIds']))
    state['publications'][pmid] = publication
    if emit_event and not previous:
        event(state, ['publication', pmid], kind='publication',
              drug_ids=publication['drugIds'], title='Новая связанная запись PubMed: '+publication['title'],
              url='https://pubmed.ncbi.nlm.nih.gov/'+pmid+'/', now=now)


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current, self.links = None, []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.current = [dict(attrs).get('href', ''), '']

    def handle_data(self, value):
        if self.current is not None:
            self.current[1] += value

    def handle_endtag(self, tag):
        if tag == 'a' and self.current is not None:
            self.links.append(self.current)
            self.current = None


def discover_fda(state, catalog, html, now):
    parser = LinkParser()
    parser.feed(html)
    known = {s['url'] for s in catalog['approvalSources']}
    for href, title in parser.links:
        title = ' '.join(title.split())
        target = urljoin('https://www.fda.gov', href)
        if urlparse(target).hostname != 'www.fda.gov' or target in known:
            continue
        if 'breast' not in title.lower():
            continue
        drug_ids = [d['id'] for d in catalog['drugs']
                    if any(s.lower() in title.lower() for s in d['aliases'])]
        if not drug_ids:
            continue
        event(state, ['fda-discovery', target], kind='fda_document',
              drug_ids=drug_ids, title=title, url=target, now=now, review=True)
        # Safety/regulatory restrictions flag existing uses; do not infer their scope.
        if re.search(r'withdraw|restrict|suspend|safety', title, re.I):
            indication_ids = {i['id'] for i in catalog['indications'] if i['drugId'] in drug_ids}
            for key in indication_ids & state['approvals'].keys():
                state['approvals'][key]['needsReview'] = True
