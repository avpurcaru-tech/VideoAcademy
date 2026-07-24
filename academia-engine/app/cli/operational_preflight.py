import argparse
from app.web_ui.bootstrap import ApplicationSettings
from app.web_ui.operational_preflight import OperationalPreflightService

def main(argv=None):
    parser=argparse.ArgumentParser(description="Read-only operational preflight")
    parser.add_argument("--config"); parser.add_argument("--project-id"); parser.add_argument("--format",choices=("text","json"),default="text"); parser.add_argument("--check-provider-connectivity",action="store_true")
    args=parser.parse_args(argv); settings=ApplicationSettings.load(args.config); report=OperationalPreflightService(settings,config_path=args.config).run(args.project_id,check_provider_connectivity=args.check_provider_connectivity,confirm_connectivity=args.check_provider_connectivity)
    print(report.to_json() if args.format=="json" else report.to_text(),end=""); return 0 if report.ready else 2
if __name__=="__main__": raise SystemExit(main())
