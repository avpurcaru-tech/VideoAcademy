import argparse,threading,webbrowser
from .server import create_app,serve
from .bootstrap import ApplicationSettings,RuntimeMode,build_application_services

def main(argv=None,*,server_runner=serve,browser_open=webbrowser.open):
    parser=argparse.ArgumentParser(description="Academia Video Engine local UI")
    parser.add_argument("--config"); parser.add_argument("--no-browser",action="store_true"); parser.add_argument("--projects-root")
    parser.add_argument("--runtime-mode",choices=tuple(x.value for x in RuntimeMode)); parser.add_argument("--host"); parser.add_argument("--port",type=int)
    parser.add_argument("--allow-non-loopback",action="store_true")
    args=parser.parse_args(argv); cli={"runtime_mode":args.runtime_mode,"projects_root":args.projects_root,"host":args.host,"port":args.port,"allow_non_loopback":args.allow_non_loopback}
    settings=ApplicationSettings.load(args.config,cli=cli); services=build_application_services(settings=settings,runtime_mode=settings.runtime_mode)
    url=f"http://{settings.server.host}:{settings.server.port}"
    if settings.server.open_browser and not args.no_browser: threading.Timer(.5,lambda:browser_open(url)).start()
    server_runner(create_app(settings=settings,services=services),settings.server.host,settings.server.port); return 0
if __name__=="__main__": raise SystemExit(main())
