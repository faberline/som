"""Join derived response mappings to the authenticated later recorder, not acceptance."""
import argparse
from pathlib import Path
import re
import reconstruct_historical_responses as r

REPORT_SHA = '0678867a693a8707cd03aa4a5442556aaf42cbc1fb90e47cf849bf701422e70a'
RECORDING_SHA = 'd8489abe98f1325d2f91cb8e31871abd1040c2a4c9580dc77b535ca7d30ab5e0'
BASE = 'run/python-baseline-raw/'

def join(mapping, recording, report, load, expected_count=668):
    r.need(recording.get('version') == 'som.m6-http-recording.v1' and recording.get('exit_code') == 0
           and recording.get('recording_errors') == [])
    calls = recording.get('calls'); rows = report.get('rows')
    r.need(type(calls) is list and type(rows) is list)
    by_id = {}
    for call in calls:
        ident = call.get('id'); r.need(type(ident) is int and ident not in by_id); by_id[ident] = call
    reports = {}
    for row in rows:
        key = (row.get('source'), row.get('id'), row.get('variant'))
        r.need(key not in reports); reports[key] = row
    observed = set(); count = 0
    for item in mapping['reconstructed_cases']:
        r.need(item['source_request_equal'] is False and item['historical_request_sha256'] != item['later_baseline_request_sha256'])
        for variant in ('none', 'python'):
            row = reports.get((item['source'], item['id'], variant)); r.need(type(row) is dict)
            r.need(row.get('baseline_assemble_status') == 200 and
                   row.get('baseline_assemble_request_sha256') == item['later_baseline_request_sha256'] and
                   row.get('baseline_assemble_response_sha256') == item['decoded_response_sha256'])
        baseline = False; local_ids = set()
        for candidate in item['source_candidates']:
            name = candidate['path']; r.need(r.valid_response_name(name))
            ident = int(name.removeprefix(BASE+'calls/').split('.')[0]); call = by_id.get(ident)
            r.need(type(call) is dict and ident not in local_ids); local_ids.add(ident); observed.add(ident)
            rel=f'calls/{ident:06d}'
            r.need(call.get('method') == 'POST' and call.get('url') in ('http://127.0.0.1:18917/v1/assemble','http://127.0.0.1:18919/v1/assemble')
                   and call.get('status') == 200 and call.get('complete') is True and call.get('error') is None
                   and call.get('request_file') == rel+'.request.raw' and call.get('response_file') == rel+'.response.raw')
            request=load(BASE+rel+'.request.raw'); response=load(BASE+rel+'.response.raw')
            r.need(r.parse(load(BASE+rel+'.json')) == call and r.sha(request) == call.get('request_sha256') == item['later_baseline_request_sha256']
                   and r.sha(response) == call.get('response_sha256') == item['decoded_response_sha256'])
            if call['url'] == 'http://127.0.0.1:18917/v1/assemble': baseline=True
        r.need(baseline); count+=1
    r.need(count == expected_count)
    return {'derived_pairs':count, 'later_capture_calls':len(observed)}

def main():
    p=argparse.ArgumentParser()
    for flag in ('map','evidence-packages','evidence-root'): p.add_argument('--'+flag,required=True,type=Path)
    a=p.parse_args(); raw=r.read(a.map); r.need(r.sha(raw)==r.MAP_SHA); mapping=r.parse(raw)
    files=r.indexed_files(a.evidence_packages,r.EVIDENCE_SHA,True)
    def load(name): return r.checked(a.evidence_root,name,files)
    record=load(BASE+'manifest.json'); report=load('run/python-baseline/report.json')
    r.need(r.sha(record)==RECORDING_SHA and r.sha(report)==REPORT_SHA)
    result=join(mapping,r.parse(record),r.parse(report),load)
    print('Verified later captured requests, status and responses for %d derived pairs; not original wire capture or product acceptance.' % result['derived_pairs'])

if __name__ == '__main__': main()
