import html,json,mimetypes
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote,urlsplit

from .project_service import ReadOnlyProjectService

ROOT=Path(__file__).parent
class WebResponse:
    def __init__(self,status,body,content_type="text/html; charset=utf-8"):
        self.status=status; self.body=body.encode("utf-8") if isinstance(body,str) else body; self.content_type=content_type
class LocalWebApplication:
    def __init__(self,projects_root=None): self.projects=ReadOnlyProjectService(projects_root)
    def dispatch(self,path):
        route=unquote(urlsplit(path).path)
        if route=="/health": return self._json(200,{"status":"ok"})
        if route=="/": return WebResponse(200,self._index())
        if route.startswith("/static/"):
            name=route.removeprefix("/static/")
            if "/" in name or "\\" in name: return WebResponse(404,"Not found","text/plain")
            file=ROOT/"static"/name
            return WebResponse(200,file.read_bytes(),mimetypes.guess_type(file.name)[0] or "application/octet-stream") if file.is_file() else WebResponse(404,"Not found","text/plain")
        prefix="/api/projects/"; suffix="/workflow"
        if route.startswith(prefix) and route.endswith(suffix):
            project_id=route[len(prefix):-len(suffix)].strip("/")
            try: state=self.projects.workflow(project_id)
            except KeyError: return self._json(404,{"error":"project_not_found"})
            return self._json(200,state.model_dump(mode="json"))
        prefix="/projects/"
        if route.startswith(prefix):
            project_id=route[len(prefix):].strip("/")
            try: state=self.projects.workflow(project_id)
            except KeyError: return WebResponse(404,"Project not found")
            return WebResponse(200,self._project(state))
        return WebResponse(404,"Not found","text/plain; charset=utf-8")
    def _index(self):
        projects="".join(f'<li><a href="/projects/{html.escape(x)}">{html.escape(x)}</a></li>' for x in self.projects.list_projects())
        return self._page("Proiecte",f'<main><h1>Academia Video Engine</h1><h2>Proiecte</h2><a class="button" aria-disabled="true">Episod nou</a><ul>{projects}</ul></main>')
    def _project(self,state):
        labels={"lyrics":"Versuri","music":"Muzică","alignment":"Alignment","scene_plan":"Scene Plan","visual_plan":"Visual Plan",
            "prompts":"Prompturi","assets":"Assets","composition":"Compoziție","episode":"Episod"}
        cards="".join(f'<article class="stage-card" data-stage="{x.stage.value}"><h2>{labels[x.stage.value]}</h2>'
            f'<dl><dt>Status</dt><dd>{x.status.value}</dd><dt>Versiune curentă</dt><dd>{x.current_version}</dd>'
            f'<dt>Versiune aprobată</dt><dd>{x.approved_version or "—"}</dd><dt>Motiv blocare</dt><dd>{html.escape(x.blocked_reason or "—")}</dd>'
            f'<dt>Ultima eroare</dt><dd>{html.escape(x.last_error or "—")}</dd></dl></article>' for x in state.stages if x.stage.value!="episode")
        return self._page(state.project_id,f'<main><a href="/">← Proiecte</a><h1>Proiect {html.escape(state.project_id)}</h1><section class="stage-grid">{cards}</section></main>')
    @staticmethod
    def _page(title,content): return f'<!doctype html><html lang="ro"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{html.escape(title)}</title><link rel="stylesheet" href="/static/styles.css"></head><body>{content}<script src="/static/app.js"></script></body></html>'
    @staticmethod
    def _json(status,payload): return WebResponse(status,json.dumps(payload,ensure_ascii=False,sort_keys=True),"application/json; charset=utf-8")

def create_application(projects_root=None): return LocalWebApplication(projects_root)
def serve(application,host="127.0.0.1",port=8080):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            response=application.dispatch(self.path); self.send_response(response.status); self.send_header("Content-Type",response.content_type)
            self.send_header("Content-Length",str(len(response.body))); self.end_headers(); self.wfile.write(response.body)
        def log_message(self,format,*args): pass
    ThreadingHTTPServer((host,port),Handler).serve_forever()
