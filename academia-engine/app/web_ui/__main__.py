import argparse,threading,webbrowser
from .server import create_application,serve
from .bootstrap import ApplicationSettings,RuntimeMode,build_application_services

def main(argv=None,*,server_runner=serve,browser_open=webbrowser.open):
    parser=argparse.ArgumentParser(description="Academia Video Engine local UI")
    parser.add_argument("--no-browser",action="store_true"); parser.add_argument("--projects-root"); parser.add_argument("--runtime-mode",choices=tuple(x.value for x in RuntimeMode),default=RuntimeMode.DRY_RUN.value)
    args=parser.parse_args(argv); url="http://127.0.0.1:8080"
    settings=ApplicationSettings.from_environment()
    if args.projects_root: settings=ApplicationSettings(**{**settings.__dict__,"projects_root":__import__("pathlib").Path(args.projects_root)})
    services=build_application_services(settings=settings,runtime_mode=RuntimeMode(args.runtime_mode))
    if not args.no_browser: threading.Timer(.5,lambda:browser_open(url)).start()
    server_runner(create_application(settings.projects_root,services=services),"127.0.0.1",8080); return 0
if __name__=="__main__": raise SystemExit(main())
