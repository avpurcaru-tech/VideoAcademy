import html,json,mimetypes
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,unquote,urlsplit

from .project_service import ReadOnlyProjectService
from .project_creation import AtomicProjectCreationService,EpisodeCreationInput
from .workflow import WorkflowActionService,WorkflowStage,WorkflowAction,WorkflowStateRepository
from .lyrics import LyricsGenerationFailure,LyricsStageService
from .music import MusicBlockedError,MusicCostConfirmationRequired,MusicStageService,MusicUiError
from pydantic import ValidationError

ROOT=Path(__file__).parent
class WebResponse:
    def __init__(self,status,body,content_type="text/html; charset=utf-8",headers=None):
        self.status=status; self.body=body.encode("utf-8") if isinstance(body,str) else body; self.content_type=content_type; self.headers=headers or {}
class LocalWebApplication:
    def __init__(self,projects_root=None,lyrics_provider=None,music_provider=None):
        self.projects=ReadOnlyProjectService(projects_root); self.lyrics_provider=lyrics_provider; self.music_provider=music_provider
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
        lyrics_parts=route.strip("/").split("/")
        if len(lyrics_parts)>=3 and lyrics_parts[0]=="projects" and lyrics_parts[2]=="lyrics":
            project_id=lyrics_parts[1]; project=self.projects.root/project_id
            if not (project/"project.json").is_file(): return WebResponse(404,"Project not found")
            service=LyricsStageService(project,self.lyrics_provider)
            if method=="GET" and len(lyrics_parts)==3: return WebResponse(200,self._lyrics_page(project_id,service))
            if method=="POST" and len(lyrics_parts)==4 and lyrics_parts[3] in {"edit","generate","regenerate"}:
                values={key:items[-1] for key,items in parse_qs(body.decode("utf-8"),keep_blank_values=True).items()}
                try:
                    if lyrics_parts[3]=="edit": service.edit(values.get("lyrics_text",""))
                    else: service.generate(feedback=values.get("feedback") if lyrics_parts[3]=="regenerate" else None,
                        user_instructions=values.get("user_instructions"))
                except LyricsGenerationFailure: return WebResponse(502,self._lyrics_page(project_id,service,error="Generarea versurilor a eșuat."))
                except ValueError as error: return WebResponse(422,self._lyrics_page(project_id,service,error=str(error)))
                return WebResponse(303,"",headers={"Location":f"/projects/{project_id}/lyrics"})
        music_parts=route.strip("/").split("/")
        if len(music_parts)>=3 and music_parts[0]=="projects" and music_parts[2]=="music":
            project_id=music_parts[1]; project=self.projects.root/project_id
            if not (project/"project.json").is_file(): return WebResponse(404,"Project not found")
            service=MusicStageService(project,self.music_provider)
            if method=="GET" and len(music_parts)==3: return WebResponse(200,self._music_page(project_id,service))
            if method=="GET" and len(music_parts)==7 and music_parts[3]=="assets":
                try:
                    version=int(music_parts[4].removeprefix("version-")); variant_id=music_parts[5]
                    if music_parts[6]!=f"{variant_id}.mp3": raise ValueError()
                    asset=service.audio_path(version,variant_id); return WebResponse(200,asset.read_bytes(),"audio/mpeg")
                except (ValueError,OSError): return WebResponse(404,"Audio variant not found")
            if method=="POST" and len(music_parts)==4:
                action=music_parts[3]; values={key:items[-1] for key,items in parse_qs(body.decode("utf-8"),keep_blank_values=True).items()}
                try:
                    if action in {"generate","regenerate"}: service.generate(confirmed=values.get("confirm_cost")=="yes",feedback=values.get("feedback") if action=="regenerate" else None)
                    elif action=="select": service.select(int(values["version"]),values["variant_id"])
                    elif action=="approve": service.approve(int(values["version"]),values.get("variant_id"))
                    elif action=="reject": service.reject(int(values["version"]))
                    else: return WebResponse(404,"Invalid music action")
                except MusicCostConfirmationRequired as error: return WebResponse(422,self._music_page(project_id,service,error=str(error)))
                except (MusicBlockedError,MusicUiError,ValueError,KeyError) as error: return WebResponse(422,self._music_page(project_id,service,error=str(error)))
                return WebResponse(303,"",headers={"Location":f"/projects/{project_id}/music"})
        action_prefix="/projects/"
        if method=="POST" and route.startswith(action_prefix) and "/stages/" in route:
            parts=route.strip("/").split("/")
            if len(parts)!=5 or parts[0]!="projects" or parts[2]!="stages": return WebResponse(404,"Not found")
            project_id,stage,action=parts[1],parts[3],parts[4]
            action_map={"approve":WorkflowAction.APPROVE,"reject":WorkflowAction.REJECT,"unlock":WorkflowAction.UNLOCK,
                "select-version":WorkflowAction.SELECT_VERSION}
            if action not in action_map or not (self.projects.root/project_id/"project.json").is_file(): return WebResponse(404,"Invalid workflow action")
            values={key:items[-1] for key,items in parse_qs(body.decode("utf-8"),keep_blank_values=True).items()}
            try:
                selected=int(values["version"]) if action=="select-version" else None
                WorkflowActionService(self.projects.root/project_id).execute(project_id,action_map[action],WorkflowStage(stage),
                    reason=values.get("reason","").strip(),version=selected)
            except (ValueError,KeyError) as error: return WebResponse(422,f"Workflow action rejected: {html.escape(str(error))}")
            return WebResponse(303,"",headers={"Location":f"/projects/{project_id}"})
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
        labels["lyrics"]=f'<a href="/projects/{html.escape(state.project_id)}/lyrics">Versuri</a>'
        labels["music"]=f'<a href="/projects/{html.escape(state.project_id)}/music">Muzică</a>'
        cards="".join(f'<article class="stage-card" data-stage="{x.stage.value}"><h2>{labels[x.stage.value]}</h2>'
            f'<dl><dt>Status</dt><dd>{x.status.value}</dd><dt>Versiune curentă</dt><dd>{x.current_version}</dd>'
            f'<dt>Versiune aprobată</dt><dd>{x.approved_version or "—"}</dd><dt>Motiv blocare</dt><dd>{html.escape(x.blocked_reason or "—")}</dd>'
            f'<dt>Ultima eroare</dt><dd>{html.escape(x.last_error or "—")}</dd></dl>{self._stage_actions(state.project_id,x)}</article>' for x in state.stages if x.stage.value!="episode")
        message='<p class="success" role="status">Proiect creat cu succes</p>' if created else ""
        return self._page(state.project_id,f'<main><a href="/">← Proiecte</a>{message}<h1>Proiect {html.escape(state.project_id)}</h1><section class="stage-grid">{cards}</section></main>')
    def _lyrics_page(self,project_id,service,error=None):
        selected=service.selected(); versions=service.versions(); text=html.escape(selected.lyrics_text if selected else "")
        status=selected.status.value if selected else "not_started"; number=selected.version if selected else "—"
        history="".join(f'<li>Versiunea {x.version} — {x.status.value}</li>' for x in reversed(versions)) or "<li>Nicio versiune</li>"
        workflow_stage=WorkflowStateRepository(service.project).resolve(project_id)[0].stage("lyrics"); base=f"/projects/{project_id}/stages/lyrics"
        actions=""
        if workflow_stage.status.value=="generated": actions=(f'<form method="post" action="{base}/approve"><button>Aprobă</button></form>'
            f'<form method="post" action="{base}/reject"><button>Respinge</button></form>')
        options="".join(f'<option value="{x.version}"{" selected" if x.version==workflow_stage.selected_version else ""}>Versiunea {x.version}</option>' for x in versions)
        if options: actions+=f'<form method="post" action="{base}/select-version"><select name="version">{options}</select><button>Selectează versiune</button></form>'
        error_html=f'<p class="errors" role="alert">{html.escape(error)}</p>' if error else ""
        content=(f'<main><a href="/projects/{project_id}">← Proiect</a><h1>Versuri</h1>{error_html}<p>Versiune curentă: {number}</p><p>Status: {status}</p>'
            f'<form method="post" action="/projects/{project_id}/lyrics/edit"><label>Editor text<textarea name="lyrics_text" required>{text}</textarea></label><button>Salvează editarea</button></form>'
            f'<form method="post" action="/projects/{project_id}/lyrics/generate"><label>Instrucțiuni opționale<textarea name="user_instructions"></textarea></label><button>Generează</button></form>'
            f'<form method="post" action="/projects/{project_id}/lyrics/regenerate"><label>Feedback<textarea name="feedback"></textarea></label><button>Regenerare</button></form>'
            f'<div class="stage-actions">{actions}</div><section><h2>Istoric versiuni</h2><ul>{history}</ul></section></main>')
        return self._page("Versuri",content)
    def _music_page(self,project_id,service,error=None):
        versions=service.versions(); workflow=WorkflowStateRepository(service.project).resolve(project_id)[0]; stage=workflow.stage("music")
        error_html=f'<p class="errors" role="alert">{html.escape(error)}</p>' if error else ""
        blocks=[]
        for version in reversed(versions):
            variants=[]
            for item in version.variants:
                asset=f"/projects/{project_id}/music/assets/version-{version.version:03d}/{item.variant_id}/{item.variant_id}.mp3"
                selected=" (selectată)" if item.variant_id==version.selected_variant_id else ""
                variants.append(f'<article class="music-variant"><h3>{item.variant_id}{selected}</h3><audio controls preload="none" src="{asset}"></audio>'
                    f'<p>Durată: {item.duration_seconds or "—"}</p><p>Task ID: {html.escape(version.task_id)}</p><p>Audio ID: {html.escape(item.audio_id)}</p>'
                    f'<form method="post" action="/projects/{project_id}/music/select"><input type="hidden" name="version" value="{version.version}"><input type="hidden" name="variant_id" value="{item.variant_id}"><button>Selectează</button></form></article>')
            approval=""
            if version.selected_variant_id and stage.status.value=="generated": approval=(f'<form method="post" action="/projects/{project_id}/music/approve"><input type="hidden" name="version" value="{version.version}"><button>Aprobă</button></form>'
                f'<form method="post" action="/projects/{project_id}/music/reject"><input type="hidden" name="version" value="{version.version}"><button>Respinge</button></form>')
            blocks.append(f'<section><h2>Versiunea {version.version} — {version.status.value}</h2>{"".join(variants)}<div class="stage-actions">{approval}</div></section>')
        confirmation='<p>Această acțiune poate consuma credite Suno.</p><label><input type="checkbox" name="confirm_cost" value="yes" required> Confirmă generarea</label>'
        content=(f'<main><a href="/projects/{project_id}">← Proiect</a><h1>Muzică</h1>{error_html}<p>Status: {stage.status.value}</p>'
            f'<form method="post" action="/projects/{project_id}/music/generate">{confirmation}<button>Generează muzică</button></form>'
            f'<form method="post" action="/projects/{project_id}/music/regenerate"><label>Feedback<input name="feedback"></label>{confirmation}<button>Regenerare</button></form>{"".join(blocks)}</main>')
        return self._page("Muzică",content)
    @staticmethod
    def _stage_actions(project_id,stage):
        base=f"/projects/{project_id}/stages/{stage.stage.value}"; controls=[]
        if stage.status.value=="generated": controls.extend((f'<form method="post" action="{base}/approve"><button>Aprobă</button></form>',f'<form method="post" action="{base}/reject"><button>Respinge</button></form>'))
        if stage.status.value=="approved": controls.append(f'<form method="post" action="{base}/unlock"><button>Deblochează</button></form>')
        options="".join(f'<option value="{x.version}"{" selected" if x.version==stage.selected_version else ""}>Versiunea {x.version}</option>' for x in stage.versions)
        if options: controls.append(f'<details><summary>Vezi versiuni</summary><form method="post" action="{base}/select-version"><select name="version">{options}</select><button>Selectează versiune</button></form></details>')
        return '<div class="stage-actions">'+"".join(controls)+"</div>"
    @staticmethod
    def _page(title,content): return f'<!doctype html><html lang="ro"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{html.escape(title)}</title><link rel="stylesheet" href="/static/styles.css"></head><body>{content}<script src="/static/app.js"></script></body></html>'
    @staticmethod
    def _json(status,payload): return WebResponse(status,json.dumps(payload,ensure_ascii=False,sort_keys=True),"application/json; charset=utf-8")

def create_application(projects_root=None,lyrics_provider=None,music_provider=None): return LocalWebApplication(projects_root,lyrics_provider,music_provider)
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
