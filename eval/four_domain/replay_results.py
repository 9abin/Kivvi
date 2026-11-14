"""Re-score published model outputs offline, without API keys or network."""
import argparse,gzip,shutil
from pathlib import Path
from score_results import score_run
from render_report import write_report
HERE=Path(__file__).resolve().parent
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--variant',choices=['reviewed','state-compare-v2'],default='reviewed')
    ap.add_argument('--out',type=Path)
    args=ap.parse_args()
    source=HERE/'results'/args.variant
    out=(args.out or HERE/'runs'/('replay' if args.variant=='reviewed' else 'replay-'+args.variant)).resolve()
    if out.is_relative_to((HERE/'results').resolve()): raise ValueError('Published results are read-only; use runs/.')
    out.mkdir(parents=True,exist_ok=True)
    # The prompt-only variant uses the identical baseline retrieval snapshot.
    with gzip.open(HERE/'results/reviewed/retrieval.jsonl.gz','rb') as inp, (out/'retrieval.jsonl').open('wb') as target: shutil.copyfileobj(inp,target)
    shutil.copyfile(source/'predictions.jsonl',out/'predictions.jsonl')
    score_run(argparse.Namespace(data=HERE,results=out,cases=None,k=10))
    write_report(out)
if __name__=='__main__': main()
