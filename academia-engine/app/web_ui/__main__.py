import argparse,threading,webbrowser
from .server import create_application,serve

def main(argv=None,*,server_runner=serve,browser_open=webbrowser.open):
    parser=argparse.ArgumentParser(description="Academia Video Engine local UI")
    parser.add_argument("--no-browser",action="store_true"); parser.add_argument("--projects-root")
    args=parser.parse_args(argv); url="http://127.0.0.1:8080"
    if not args.no_browser: threading.Timer(.5,lambda:browser_open(url)).start()
    server_runner(create_application(args.projects_root),"127.0.0.1",8080); return 0
if __name__=="__main__": raise SystemExit(main())
