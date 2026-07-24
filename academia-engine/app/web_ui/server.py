import html,json,mimetypes
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,unquote,urlsplit

from .project_service import ReadOnlyProjectService
from .project_creation import AtomicProjectCreationService,EpisodeCreationInput
from pydantic import ValidationError

ROOT=Path(__file__).parent
class WebResponse:
    def __init__(self,status,body,content_type="text/html; charset=utf-8",headers=None):
        self.status=status; self.body=body.encode("utf-8") if isinstance(body,str) else body; self.content_type=content_type; self.headers=headers or {}
class LocalWebApplication:
    def __init__(self,projects_root=None): self.projects=ReadOnlyProjectService(projects_root)
    def dispatch(self,path,method="GET",body=b"",headers=None):
        route=unquote(urlsplit(path).path)
        if method=="GET" and route=="/projects/new": return WebResponse(200,self._new_project_form())
        if method=="POST" and route=="/projects":
            values={key:items[-1] for key,items in parse_qs(body.decode("utf-8"),keep_blank_values=True).items()}
            for optional in ("episode_theme","educational_goal","notes"):
                if not values.get(optional): values[optional]=None
            try: manifest=AtomicProjectCreationService(self.projects.root).create(values)
            except ValidationError as error: return WebResponse(422,self._new_project_form(values,error.errors()))
            except Exception as error: return WebResponse(409,self._new_project_form(values,({"msg":str(error)},)))
            return WebResponse(303,"",headers={"Location":f"/projects/{manifest.project_id}?created=1"})
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
            created=parse_qs(urlsplit(path).query).get("created")==["1"]
            return WebResponse(200,self._project(state,created=created))
        return WebResponse(404,"Not found","text/plain; charset=utf-8")
    def _index(self):
        projects="".join(f'<li><a href="/projects/{html.escape(x)}">{html.escape(x)}</a></li>' for x in self.projects.list_projects())
        return self._page("Proiecte",f'<main><h1>Academia Video Engine</h1><h2>Proiecte</h2><a class="button" href="/projects/new">Episod nou</a><ul>{projects}</ul></main>')
    def _new_project_form(self,values=None,errors=()):
        values=values or {}; error_html="" if not errors else '<div class="errors" role="alert">Datele formularului nu sunt valide.</div>'
        def field(name,label,kind="text",required=True):
            required_html=" required" if required else ""; value=html.escape(str(values.get(name) or ""),quote=True)
            if kind=="textarea": return f'<label>{label}<textarea name="{name}"{required_html}>{value}</textarea></label>'
            return f'<label>{label}<input type="{kind}" name="{name}" value="{value}"{required_html}></label>'
        selects=(f'<label>Limba<select name="language" required>{self._options(("ro","en","fr","de","es"),values.get("language","ro"))}</select></label>'
            f'<label>Vârsta<select name="target_age" required>{self._options(("2-5","6-8","9-12"),values.get("target_age","2-5"))}</select></label>'
            f'<label>Raport video<select name="aspect_ratio" required>{self._options(("16:9","9:16","1:1"),values.get("aspect_ratio","16:9"))}</select></label>')
        content=(f'<main><a href="/">← Proiecte</a><h1>Episod nou</h1>{error_html}<form method="post" action="/projects">'
            +field("title","Titlu episod")+field("description","Descriere","textarea")+field("episode_theme","Tema",required=False)
            +field("educational_goal","Obiectiv educațional","textarea",False)+selects+field("main_character_name","Personaj principal")
            +field("main_character_description","Descriere personaj","textarea")+field("notes","Note","textarea",False)
            +'<button type="submit">Creează proiectul</button></form></main>')
        return self._page("Episod nou",content)
    @staticmethod
    def _options(options,selected): return "".join(f'<option value="{html.escape(x)}"{" selected" if x==selected else ""}>{html.escape(x)}</option>' for x in options)
    def _project(self,state,created=False):
        labels={"lyrics":"Versuri","music":"Muzică","alignment":"Alignment","scene_plan":"Scene Plan","visual_plan":"Visual Plan",
            "prompts":"Prompturi","assets":"Assets","composition":"Compoziție","episode":"Episod"}
        cards="".join(f'<article class="stage-card" data-stage="{x.stage.value}"><h2>{labels[x.stage.value]}</h2>'
            f'<dl><dt>Status</dt><dd>{x.status.value}</dd><dt>Versiune curentă</dt><dd>{x.current_version}</dd>'
            f'<dt>Versiune aprobată</dt><dd>{x.approved_version or "—"}</dd><dt>Motiv blocare</dt><dd>{html.escape(x.blocked_reason or "—")}</dd>'
            f'<dt>Ultima eroare</dt><dd>{html.escape(x.last_error or "—")}</dd></dl></article>' for x in state.stages if x.stage.value!="episode")
        message='<p class="success" role="status">Proiect creat cu succes</p>' if created else ""
        return self._page(state.project_id,f'<main><a href="/">← Proiecte</a>{message}<h1>Proiect {html.escape(state.project_id)}</h1><section class="stage-grid">{cards}</section></main>')
    @staticmethod
    def _page(title,content): return f'<!doctype html><html lang="ro"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{html.escape(title)}</title><link rel="stylesheet" href="/static/styles.css"></head><body>{content}<script src="/static/app.js"></script></body></html>'
    @staticmethod
    def _json(status,payload): return WebResponse(status,json.dumps(payload,ensure_ascii=False,sort_keys=True),"application/json; charset=utf-8")

def create_application(projects_root=None): return LocalWebApplication(projects_root)
def serve(application,host="127.0.0.1",port=8080):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._respond(application.dispatch(self.path))
        def do_POST(self):
            length=int(self.headers.get("Content-Length","0")); self._respond(application.dispatch(self.path,"POST",self.rfile.read(length),dict(self.headers)))
        def _respond(self,response):
            self.send_response(response.status); self.send_header("Content-Type",response.content_type)
            for key,value in response.headers.items(): self.send_header(key,value)
            self.send_header("Content-Length",str(len(response.body))); self.end_headers(); self.wfile.write(response.body)
        def log_message(self,format,*args): pass
    ThreadingHTTPServer((host,port),Handler).serve_forever()
