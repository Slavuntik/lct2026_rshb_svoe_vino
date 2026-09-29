"""Post-process this audit; no model calls, credentials or photos are published."""
import argparse, json, math, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from qa.label_audit import summarize

parser = argparse.ArgumentParser(description='Analyze the completed 119-photo label audit and sequential API log')
parser.add_argument('--root', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
root = args.root
rows=[json.loads(line) for line in (root/'archive-isolated-vlm.jsonl').read_text().splitlines()]
assert len(rows)==119, 'Wait for full run'
metrics=summarize(rows)
known=[r for r in rows if r['truth_kind']=='known']
scored=[r for r in rows if r['truth_kind']!='unknown']
accepted=[r for r in scored if r.get('http_status')==200 and r['response'].get('slug') and not r['response']['not_in_catalog']]
def correct(r): return r['truth_kind']=='known' and r['response'].get('slug')==r['true_slug']
def wilson(k,n):
 z=1.959963984540054;p=k/n;den=1+z*z/n;mid=(p+z*z/(2*n))/den;half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
 return [mid-half,mid+half]
metrics['candidate_top1_accuracy']=metrics['candidate_top1_correct']/len(known)
metrics['candidate_top5_recall']=metrics['candidate_top5_correct']/len(known)
metrics['false_accept_rate_absent']=metrics['false_accepts_absent']/metrics['absent']
metrics['accepted_coverage']=len(accepted)/len(scored)
metrics['wilson95_descriptive_only']={
 'candidate_top1':wilson(metrics['candidate_top1_correct'],len(known)),
 'precision_accepted':wilson(metrics['correct_accepted'],len(accepted)),
 'recall_accepted':wilson(metrics['correct_accepted'],len(known)),
 'false_accept_rate':wilson(metrics['false_accepts_absent'],metrics['absent'])}
thresholds=[]
for floor,gap in [(.77,.03),(.8,.03),(.85,.03),(.9,.03),(.77,.05),(.77,.08)]:
 keep=[r for r in accepted if r['response']['confidence']['top1_score']>=floor and (r['response']['confidence']['gap'] is None or r['response']['confidence']['gap']>=gap)]
 good=sum(correct(r) for r in keep)
 thresholds.append({'cv_floor':floor,'gap_floor':gap,'accepted':len(keep),'correct':good,'false_accepts_absent':sum(r['truth_kind']=='absent' for r in keep),'precision':good/len(keep) if keep else None,'recall':good/len(known)})
errors=[]
for r in scored:
 d=r.get('response',{});slug=d.get('slug'); matches=[m['slug'] for m in d.get('matches',[])]
 if correct(r) or r['truth_kind']=='absent' and d.get('not_in_catalog') is True: continue
 errors.append({'qid':r['qid'],'id':r['id'],'truth_kind':r['truth_kind'],'true_slug':r['true_slug'],'shown_slug':slug,'candidate_top1':matches[0] if matches else None,'truth_rank':matches.index(r['true_slug'])+1 if r['true_slug'] in matches else None,'confidence':d.get('confidence'),'elapsed_ms':r['elapsed_ms']})
log=(root/'isolated-api.log').read_text()
branches=re.findall(r'cv_fusion: local=(\S+) model=(\S+) agree=(\S+) choose=(\S+) chosen=(\S+)',log)
assert len(branches)==len(rows),(len(branches),len(rows))
paired={'caveat':'Same CV vectors and union of both text candidate sets; not independent OCR-only run','known':len(known),'local_top1_correct':0,'model_top1_correct':0,'disagreements':[]}
for r,b in zip(rows,branches):
 if r['truth_kind']=='known':
  paired['local_top1_correct']+=b[0]==r['true_slug'];paired['model_top1_correct']+=b[1]==r['true_slug']
 if b[0]!=b[1]:paired['disagreements'].append({'qid':r['qid'],'truth':r['true_slug'],'local':b[0],'model':b[1],'chosen':b[4]})
counts={}
for source in re.findall(r'scan_photo: текст этикетки прочитан source=(\S+)',log):counts[source]=counts.get(source,0)+1
metrics['text_source_counts']=counts
metrics['chosen_side_source_counts']={}
for b,source in zip(branches,re.findall(r'scan_photo: текст этикетки прочитан source=(\S+)',log)):
 key=b[4]+'/'+source
 metrics['chosen_side_source_counts'][key]=metrics['chosen_side_source_counts'].get(key,0)+1
out={'metrics':metrics,'sensitivity_excluding_disputed_C56_C58':summarize([r for r in rows if r['qid'] not in {'C56','C58'}]),'postfilter_replay_not_new_inference':thresholds,'paired_branches':paired,'errors':errors}
(root/'analysis.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
# Share outcomes and provenance, without photos, card descriptions or local paths.
clean=[]
for r in rows:
 d=r.get('response',{});clean.append({**{k:r[k] for k in ['qid','id','sha256','truth_kind','true_slug','http_status','elapsed_ms']},'slug':d.get('slug'),'not_in_catalog':d.get('not_in_catalog'),'confidence':d.get('confidence'),'matches':[{'slug':m['slug'],'score':m['score']} for m in d.get('matches',[])]})
outdir=args.out;outdir.mkdir(parents=True,exist_ok=True)
(outdir/'analysis.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
(outdir/'predictions.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in clean))
(outdir/'catalog-audit.json').write_text((root/'catalog-audit.json').read_text())
(outdir/'live-probes.json').write_text((root/'live-probes.json').read_text())
(outdir/'code-fingerprints.json').write_text((root/'code-fingerprints.json').read_text())
print(json.dumps(out,ensure_ascii=False,indent=2))
