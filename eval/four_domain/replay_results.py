"""Re-score published model outputs offline, without API keys or network."""
import argparse,gzip,shutil
from pathlib import Path
from score_results import score_run
from render_report import write_report
HERE=Path(__file__).resolve().parent
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--out',type=Path,default=HERE/'runs/replay'); args=ap.parse_args()
    source=HERE/'results/reviewed'; out=args.out.resolve()
    if out.is_relative_to((HERE/'results').resolve()): raise ValueError('Published results are read-only; use runs/.')
    out.mkdir(parents=True,exist_ok=True)
    with gzip.open(source/'retrieval.jsonl.gz','rb') as inp, (out/'retrieval.jsonl').open('wb') as target: shutil.copyfileobj(inp,target)
    shutil.copyfile(source/'predictions.jsonl',out/'predictions.jsonl')
    score_run(argparse.Namespace(data=HERE,results=out,cases=None,k=10))
    write_report(out)
if __name__=='__main__': main()
