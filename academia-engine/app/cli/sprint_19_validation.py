import argparse
from app.web_ui.bootstrap import ApplicationSettings
from app.web_ui.sprint19_validation import RealProjectSmokeTest

def main(argv=None):
    parser=argparse.ArgumentParser(description="Safe Sprint 19 / first real project validation")
    parser.add_argument("--project-id"); parser.add_argument("--dry-run",action="store_true"); parser.add_argument("--include-provider-connectivity",action="store_true"); parser.add_argument("--format",choices=("text","json"),default="text"); parser.add_argument("--config")
    args=parser.parse_args(argv); settings=ApplicationSettings.load(args.config); report=RealProjectSmokeTest(settings,config_path=args.config).run_dry(args.project_id,args.include_provider_connectivity)
    print(report.to_json() if args.format=="json" else report.to_text(),end=""); return 0
if __name__=="__main__": raise SystemExit(main())
