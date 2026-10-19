from __future__ import annotations
import argparse,json,os,sys
from pathlib import Path
from .db import DEFAULT_DB,PROJECT,connect,stats,dumps

def main():
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT/'.env',override=False)
    except ImportError:pass
    parser=argparse.ArgumentParser(description='全量新污染物目录数据库与CrewAI工作流')
    parser.add_argument('--db',default=str(DEFAULT_DB))
    sub=parser.add_subparsers(dest='command',required=True)
    run=sub.add_parser('run');run.add_argument('--snapshot',default=str(PROJECT/'inputs/catalog_snapshot.json'));run.add_argument('--benchmarks',default=str(PROJECT/'inputs/epa_benchmarks.json'));run.add_argument('--output',default=str(PROJECT/'outputs'))
    view=sub.add_parser('serve');view.add_argument('--port',type=int,default=int(os.getenv('EC_AGENT_PORT','8765')))
    sub.add_parser('stats')
    ask=sub.add_parser('ask');ask.add_argument('question')
    analyze=sub.add_parser('analyze');analyze.add_argument('query');analyze.add_argument('--llm-review',action='store_true');analyze.add_argument('--output')
    analyze.add_argument('--workflow',action='append',choices=('structure','neighbors','ecology'),help='可重复选择；省略时执行三条流程')
    exposure=sub.add_parser('import-exposures');exposure.add_argument('csv')
    evidence=sub.add_parser('import-evidence');evidence.add_argument('csv')
    literature=sub.add_parser('import-literature');literature.add_argument('pack')
    args=parser.parse_args()
    if args.command=='run':
        from .workflow import CatalogFlow
        flow=CatalogFlow();flow.state.db_path=args.db;flow.state.snapshot_path=args.snapshot;flow.state.benchmarks_path=args.benchmarks;flow.state.output_dir=args.output
        result=flow.kickoff();print(dumps(result))
    elif args.command=='serve':
        from .server import serve
        serve(args.db,args.port)
    elif args.command=='stats':
        with connect(args.db) as con:print(dumps(stats(con)))
    elif args.command=='ask':
        from .crew import answer_question
        print(dumps(answer_question(args.question,args.db)))
    elif args.command=='analyze':
        from .analysis_flow import run_entity_analysis
        print(dumps(run_entity_analysis(args.query,args.db,args.llm_review,args.output,workflows=args.workflow)))
    elif args.command=='import-exposures':
        from .evidence import import_exposures
        print(dumps(import_exposures(args.csv,args.db)))
    elif args.command=='import-evidence':
        from .evidence import import_evidence
        print(dumps(import_evidence(args.csv,args.db)))
    elif args.command=='import-literature':
        from .literature import import_pack
        print(dumps(import_pack(args.pack,args.db)))

if __name__=='__main__':main()
