"""Re-score the one-edit 60-case responses without API keys or network."""
import argparse,gzip,hashlib,json,shutil,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.append(str(HERE.parent))
from score_results import score_run
from render_report import write_report

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,default=HERE/'runs/replay');args=ap.parse_args()
    out=args.out.resolve();source=HERE/'results/luna'
    if not out.is_relative_to((HERE/'runs').resolve()):raise ValueError('Use single_change/runs/.')
    manifest=json.loads((source/'snapshot-hashes.json').read_text(encoding='utf-8'))
    for name,expected in manifest.items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==expected,name
    out.mkdir(parents=True,exist_ok=True)
    with gzip.open(source/'retrieval.jsonl.gz','rb') as src,(out/'retrieval.jsonl').open('wb') as dst:shutil.copyfileobj(src,dst)
    shutil.copyfile(source/'predictions.jsonl',out/'predictions.jsonl')
    score_run(argparse.Namespace(data=HERE,results=out,cases=None,k=10))
    write_report(out)
    assert json.loads((out/'summary.json').read_text())==json.loads((source/'summary.json').read_text())
    print('Offline replay exactly matches the published summary.')

if __name__=='__main__':main()
