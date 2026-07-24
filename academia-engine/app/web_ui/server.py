import html,json,mimetypes
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,unquote,urlsplit

from .project_service import ReadOnlyProjectService
from .project_creation import AtomicProjectCreationService,EpisodeCreationInput
from .workflow import WorkflowActionService,WorkflowStage,WorkflowAction,WorkflowStateRepository
from .lyrics import LyricsGenerationFailure,LyricsStageService
from .music import MusicBlockedError,MusicCostConfirmationRequired,MusicStageService,MusicUiError
from .planning_review import PlanningReviewError,PlanningReviewService,PlanningStageBlocked
from .assets import (AssetBlockedError,AssetCostConfirmationRequired,AssetGenerationFailure,
    AssetReviewError,AssetReviewService)
from .composition import (CompositionBlockedError,CompositionReviewService,CompositionUiError,
    RenderConfirmationRequired)
from .job_recovery import (DuplicateCostWarningRequired,JobConfirmationRequired,JobNotFound,
    JobRecoveryService)
from pydantic import ValidationError

ROOT=Path(__file__).parent
class WebResponse:
    def __init__(self,status,body,content_type="text/html; charset=utf-8",headers=None):
        self.status=status; self.body=body.encode("utf-8") if isinstance(body,str) else body; self.content_type=content_type; self.headers=headers or {}
class LocalWebApplication:
    def __init__(self,projects_root=None,lyrics_provider=None,music_provider=None,planning_builders=None,asset_provider=None,composition_renderer=None,services=None,recovery_service=None):
        if services is not None:
            lyrics_provider=services.lyrics_provider; music_provider=services.music_provider; planning_builders=services.planning_builders
            asset_provider=services.asset_provider; composition_renderer=services.composition_renderer
        self.projects=ReadOnlyProjectService(projects_root); self.lyrics_provider=lyrics_provider; self.music_provider=music_provider
        self.planning_builders=planning_builders or {}; self.asset_provider=asset_provider; self.composition_renderer=composition_renderer; self.services=services; self.settings=None
        self.recovery_service=recovery_service or JobRecoveryService(self.projects.root)
    def dispatch(self,path,method="GET",body=b"",headers=None):
        route=unquote(urlsplit(path).path)
        job_parts=route.strip("/").split("/")
        if method=="GET" and route=="/jobs": return WebResponse(200,self._jobs_page(self.recovery_service.scan()))
        if method=="GET" and len(job_parts)==3 and job_parts[0]=="projects" and job_parts[2]=="jobs":
            return WebResponse(200,self._jobs_page(self.recovery_service.scan(job_parts[1]),job_parts[1]))
        if method=="POST" and len(job_parts)==3 and job_parts[0]=="jobs" and job_parts[2] in {"refresh","resume","fail","abandon"}:
            values={key:items[-1] for key,items in parse_qs(body.decode("utf-8"),keep_blank_values=True).items()}; action=job_parts[2]
            try:
                if action=="refresh": self.recovery_service.refresh_job(job_parts[1],confirm_external_check=values.get("confirm") == "yes")
                elif action=="resume": self.recovery_service.resume_job(job_parts[1],confirm_resume=values.get("confirm") == "yes")
                elif action=="fail": self.recovery_service.mark_failed(job_parts[1],values.get("reason") or "Marked failed by user.")
                else: self.recovery_service.abandon(job_parts[1])
            except (JobConfirmationRequired,DuplicateCostWarningRequired) as error: return WebResponse(422,self._jobs_page(self.recovery_service.scan(),error=str(error)))
            except JobNotFound: return WebResponse(404,"Job not found")
            return WebResponse(303,"",headers={"Location":"/jobs"})
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
        planning_parts=route.strip("/").split("/"); route_stages={"alignment":"alignment","scene-plan":"scene_plan","visual-plan":"visual_plan","prompts":"prompts"}
        if len(planning_parts)>=3 and planning_parts[0]=="projects" and planning_parts[2] in route_stages:
            project_id=planning_parts[1]; route_stage=planning_parts[2]; stage=route_stages[route_stage]; project=self.projects.root/project_id
            if not (project/"project.json").is_file(): return WebResponse(404,"Project not found")
            service=PlanningReviewService(project,self.planning_builders)
            if method=="GET" and len(planning_parts)==3: return WebResponse(200,self._planning_page(project_id,route_stage,stage,service))
            if method=="POST" and len(planning_parts)==4:
                action=planning_parts[3]; values={key:items[-1] for key,items in parse_qs(body.decode("utf-8"),keep_blank_values=True).items()}
                try:
                    if action in {"build","rebuild"}: service.build(stage,rebuild=action=="rebuild")
                    elif stage=="prompts" and action=="edit": service.edit_prompt(values["scene_id"],values.get("positive_prompt",""),values.get("negative_prompt",""))
                    elif stage=="prompts" and action=="regenerate-scene": service.regenerate_prompt(values["scene_id"],values.get("feedback"))
                    elif stage=="prompts" and action=="regenerate-all": service.build("prompts",rebuild=True)
                    else: return WebResponse(404,"Invalid planning action")
                except (PlanningReviewError,PlanningStageBlocked,ValueError,KeyError) as error:
                    return WebResponse(422,self._planning_page(project_id,route_stage,stage,service,error=str(error)))
                return WebResponse(303,"",headers={"Location":f"/projects/{project_id}/{route_stage}"})
        asset_parts=route.strip("/").split("/")
        if len(asset_parts)>=3 and asset_parts[0]=="projects" and (asset_parts[2]=="assets" or (len(asset_parts)>=5 and asset_parts[2]=="scenes" and asset_parts[4]=="assets")):
            project_id=asset_parts[1]; project=self.projects.root/project_id
            if not (project/"project.json").is_file(): return WebResponse(404,"Project not found")
            service=AssetReviewService(project,self.asset_provider)
            if method=="GET" and len(asset_parts)==3: return WebResponse(200,self._assets_page(project_id,service))
            if len(asset_parts)>=6 and asset_parts[2]=="scenes":
                scene_id=asset_parts[3]
                if method=="POST" and len(asset_parts)==6:
                    action=asset_parts[5]; values={key:items[-1] for key,items in parse_qs(body.decode("utf-8"),keep_blank_values=True).items()}
                    try:
                        if action in {"generate","regenerate"}: service.generate(scene_id,confirmed=values.get("confirm_cost")=="yes",feedback=values.get("feedback"))
                        elif action=="approve": service.approve(scene_id)
                        elif action=="reject": service.reject(scene_id)
                        elif action=="select-version": service.select(scene_id,int(values["version"]))
                        else: return WebResponse(404,"Invalid asset action")
                    except (AssetReviewError,AssetBlockedError,AssetCostConfirmationRequired,AssetGenerationFailure,ValueError,KeyError) as error:
                        return WebResponse(422,self._assets_page(project_id,service,error=str(error)))
                    return WebResponse(303,"",headers={"Location":f"/projects/{project_id}/assets"})
                if method=="GET" and len(asset_parts)==7 and asset_parts[6]=="preview":
                    try:
                        version=int(asset_parts[5].removeprefix("version-")); path,metadata=service.preview_path(scene_id,version)
                        return WebResponse(200,path.read_bytes(),metadata.content_type)
                    except (ValueError,OSError): return WebResponse(404,"Asset preview not found")
        composition_parts=route.strip("/").split("/")
        if len(composition_parts)>=3 and composition_parts[0]=="projects" and composition_parts[2]=="composition":
            project_id=composition_parts[1]; project=self.projects.root/project_id
            if not (project/"project.json").is_file(): return WebResponse(404,"Project not found")
            service=CompositionReviewService(project,self.composition_renderer)
            if method=="GET" and len(composition_parts)==3: return WebResponse(200,self._composition_page(project_id,service))
            if method=="GET" and len(composition_parts)==6 and composition_parts[3]=="versions" and composition_parts[5]=="preview":
                try: path=service.preview_path(int(composition_parts[4])); return WebResponse(200,path.read_bytes(),"video/mp4")
                except (ValueError,OSError): return WebResponse(404,"Final preview not found")
            if method=="POST" and len(composition_parts)==4:
                action=composition_parts[3]; values={key:items[-1] for key,items in parse_qs(body.decode("utf-8"),keep_blank_values=True).items()}
                try:
                    if action=="preflight": service.preflight()
                    elif action=="render": service.render(confirmed=values.get("confirm_render")=="yes")
                    elif action=="approve": service.approve(int(values["version"]))
                    elif action=="reject": service.reject(int(values["version"]))
                    else: return WebResponse(404,"Invalid composition action")
                except (CompositionUiError,CompositionBlockedError,RenderConfirmationRequired,ValueError,KeyError) as error:
                    return WebResponse(422,self._composition_page(project_id,service,error=str(error)))
                return WebResponse(303,"",headers={"Location":f"/projects/{project_id}/composition"})
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
        if route=="/settings": return WebResponse(200,self._settings_page())
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
        availability=""
        if self.services is not None: availability='<section><h2>Provider availability</h2><ul>'+"".join(f'<li>{html.escape(x.provider_name)}: {html.escape(x.label)}</li>' for x in self.services.availability)+"</ul></section>"
        return self._page("Proiecte",f'<main><h1>Academia Video Engine</h1><h2>Proiecte</h2><a class="button" href="/projects/new">Episod nou</a><ul>{projects}</ul>{availability}</main>')
    def _jobs_page(self,report,project_id=None,error=None):
        error_html=f'<p class="errors" role="alert">{html.escape(error)}</p>' if error else ""; rows=[]
        for job in report.jobs:
            provider_id="—" if not job.provider_job_id else (job.provider_job_id[:4]+"…"+job.provider_job_id[-3:] if len(job.provider_job_id)>9 else "***")
            base=f"/jobs/{job.job_id}"; actions=(f'<form method="post" action="{base}/refresh"><input type="hidden" name="confirm" value="yes"><button>Verifică status</button></form>'
                f'<form method="post" action="{base}/resume"><input type="hidden" name="confirm" value="yes"><button>Reia jobul</button></form>'
                f'<form method="post" action="{base}/fail"><button>Marchează eșuat</button></form><form method="post" action="{base}/abandon"><button>Abandonează jobul</button></form>')
            rows.append(f'<article class="stage-card"><h2>{html.escape(job.project_id)} · {html.escape(job.stage)}</h2><dl><dt>Provider</dt><dd>{html.escape(job.provider)}</dd><dt>Status local</dt><dd>{job.status.value}</dd><dt>Status provider</dt><dd>{html.escape(job.last_known_provider_status or "—")}</dd><dt>Provider job ID</dt><dd>{html.escape(provider_id)}</dd><dt>Ultima eroare</dt><dd>{html.escape(job.error_message or "—")}</dd></dl><div class="stage-actions">{actions}</div></article>')
        scope=f" pentru proiectul {html.escape(project_id)}" if project_id else ""
        return self._page("Job recovery",f'<main><a href="/">← Proiecte</a><h1>Joburi întrerupte{scope}</h1>{error_html}{"".join(rows) or "<p>Nu există joburi.</p>"}</main>')
    def _settings_page(self):
        if self.settings is None: return self._page("Settings","<main><h1>Settings</h1><p>Configuration unavailable.</p></main>")
        settings=self.settings; available={x.provider_name:x.label for x in (self.services.availability if self.services else ())}
        rows=(("Lyrics",settings.lyrics.provider,settings.lyrics.enabled,bool(settings.lyrics.api_key),available.get("lyrics","Unavailable")),
            ("Suno","sunoapi.org",settings.suno.enabled,bool(settings.suno.api_key),available.get("music","Unavailable")),
            ("Assets",settings.assets.provider,settings.assets.enabled,bool(settings.assets.api_key),available.get("assets","Unavailable")),
            ("FFmpeg",settings.ffmpeg.executable,settings.ffmpeg.enabled,True,available.get("composition","Unavailable")))
        providers="".join(f'<tr><th>{html.escape(label)}</th><td>{html.escape(name)}</td><td>{"yes" if enabled else "no"}</td><td>{"yes" if configured else "no"}</td><td>{html.escape(status)}</td></tr>' for label,name,enabled,configured,status in rows)
        content=(f'<main><a href="/">← Proiecte</a><h1>Settings</h1><dl><dt>Runtime mode</dt><dd>{html.escape(settings.runtime_mode.value)}</dd>'
            f'<dt>Projects root</dt><dd>{html.escape(str(settings.projects_root))}</dd><dt>Server</dt><dd>{html.escape(settings.server.host)}:{settings.server.port}</dd></dl>'
            f'<table><thead><tr><th>Type</th><th>Provider</th><th>Enabled</th><th>Configured</th><th>Availability</th></tr></thead><tbody>{providers}</tbody></table></main>')
        return self._page("Settings",content)
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
        labels["alignment"]=f'<a href="/projects/{html.escape(state.project_id)}/alignment">Alignment</a>'
        labels["scene_plan"]=f'<a href="/projects/{html.escape(state.project_id)}/scene-plan">Scene Plan</a>'
        labels["visual_plan"]=f'<a href="/projects/{html.escape(state.project_id)}/visual-plan">Visual Plan</a>'
        labels["prompts"]=f'<a href="/projects/{html.escape(state.project_id)}/prompts">Prompturi</a>'
        labels["assets"]=f'<a href="/projects/{html.escape(state.project_id)}/assets">Assets</a>'
        labels["composition"]=f'<a href="/projects/{html.escape(state.project_id)}/composition">Compoziție</a>'
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
    def _planning_page(self,project_id,route_stage,stage,service,error=None):
        state=WorkflowStateRepository(service.project).resolve(project_id)[0]; stage_state=state.stage(stage); selected=service.selected(stage)
        labels={"alignment":"Alignment","scene_plan":"Scene Plan","visual_plan":"Visual Plan","prompts":"Prompturi"}; error_html=f'<p class="errors" role="alert">{html.escape(error)}</p>' if error else ""
        details="<p>Nicio versiune construită.</p>"
        if stage=="prompts" and selected:
            rows=[]
            for prompt in service.effective_prompts():
                scene_id=html.escape(prompt.scene_id); positive=html.escape(prompt.positive_prompt); negative=html.escape(prompt.negative_prompt)
                rows.append(f'<article class="prompt-scene"><h2>{scene_id}</h2><form method="post" action="/projects/{project_id}/prompts/edit"><input type="hidden" name="scene_id" value="{scene_id}"><label>Prompt pozitiv<textarea name="positive_prompt">{positive}</textarea></label><label>Prompt negativ<textarea name="negative_prompt">{negative}</textarea></label><pre>{html.escape(json.dumps(prompt.structured_parameters,ensure_ascii=False,sort_keys=True,indent=2))}</pre><button>Salvează versiune nouă</button></form>'
                    f'<form method="post" action="/projects/{project_id}/prompts/regenerate-scene"><input type="hidden" name="scene_id" value="{scene_id}"><label>Feedback<input name="feedback"></label><button>Regenerează scena</button></form></article>')
            details="".join(rows)
        elif selected:
            review='<strong>Review required</strong>' if selected.review_required else ""
            details=f'{review}<pre>{html.escape(json.dumps(selected.data,ensure_ascii=False,sort_keys=True,indent=2))}</pre><h2>Warnings</h2><ul>{"".join(f"<li>{html.escape(x)}</li>" for x in selected.warnings)}</ul>'
        base=f"/projects/{project_id}/{route_stage}"; workflow_base=f"/projects/{project_id}/stages/{stage}"
        controls=f'<form method="post" action="{base}/build"><button>Construiește</button></form><form method="post" action="{base}/rebuild"><button>Reconstruiește</button></form>'
        if stage=="prompts": controls+=f'<form method="post" action="{base}/regenerate-all"><button>Regenerează toate prompturile</button></form>'
        if stage_state.status.value=="generated": controls+=f'<form method="post" action="{workflow_base}/approve"><button>Aprobă</button></form><form method="post" action="{workflow_base}/reject"><button>Respinge</button></form>'
        content=f'<main><a href="/projects/{project_id}">← Proiect</a><h1>{labels[stage]}</h1>{error_html}<p>Status: {stage_state.status.value}</p><p>Versiune: {stage_state.selected_version or "—"}</p><div class="stage-actions">{controls}</div>{details}</main>'
        return self._page(labels[stage],content)
    def _assets_page(self,project_id,service,error=None):
        state=service.state(); error_html=f'<p class="errors" role="alert">{html.escape(error)}</p>' if error else ""; cards=[]
        for prompt in service.prompts():
            scene=state.scene(prompt.scene_id); versions=[]
            for number in reversed(scene.versions):
                try: metadata=service.metadata(prompt.scene_id,number)
                except ValueError: continue
                preview=f"/projects/{project_id}/scenes/{prompt.scene_id}/assets/version-{number:03d}/preview"
                media=(f'<img class="asset-preview" src="{preview}" alt="Asset {html.escape(prompt.scene_id)}">' if metadata.media_type.value=="image"
                    else f'<video class="asset-preview" controls preload="none" src="{preview}"></video>')
                versions.append(f'<div class="asset-version"><h3>Versiunea {number}</h3>{media}<p>Provider: {html.escape(metadata.provider)}</p><p>Durată: {metadata.duration_seconds or "—"}</p>'
                    f'<form method="post" action="/projects/{project_id}/scenes/{prompt.scene_id}/assets/select-version"><input type="hidden" name="version" value="{number}"><button>Selectează versiune</button></form></div>')
            base=f"/projects/{project_id}/scenes/{prompt.scene_id}/assets"; confirmation='<label><input type="checkbox" name="confirm_cost" value="yes" required> Confirmă: această acțiune poate consuma credite.</label>'
            actions=f'<form method="post" action="{base}/generate">{confirmation}<button>Generează asset</button></form><form method="post" action="{base}/regenerate"><label>Feedback<input name="feedback"></label>{confirmation}<button>Regenerează</button></form>'
            if scene.selected_version is not None: actions+=f'<form method="post" action="{base}/approve"><button>Aprobă</button></form><form method="post" action="{base}/reject"><button>Respinge</button></form>'
            cards.append(f'<article class="asset-scene"><h2>{html.escape(prompt.scene_id)}</h2><p>Prompt: {html.escape(prompt.positive_prompt)}</p><p>Status: {scene.status.value}</p><p>Versiune: {scene.selected_version or "—"}</p><div class="stage-actions">{actions}</div><details><summary>Vezi istoric</summary>{"".join(versions)}</details></article>')
        return self._page("Assets",f'<main><a href="/projects/{project_id}">← Proiect</a><h1>Assets</h1>{error_html}{"".join(cards) or "<p>Nu există prompturi.</p>"}</main>')
    def _composition_page(self,project_id,service,error=None):
        error_html=f'<p class="errors" role="alert">{html.escape(error)}</p>' if error else ""; preflight=service.last_preflight()
        if preflight:
            checks="".join(f'<li class="{"pass" if x.passed else "fail"}">{html.escape(x.name)}: {"OK" if x.passed else "FAIL"} — {html.escape(x.detail)}</li>' for x in preflight.checks)
            preflight_html=f'<section><h2>Preflight: {"READY" if preflight.ready else "BLOCKED"}</h2><ul>{checks}</ul><pre>{html.escape(json.dumps({"assets":preflight.asset_summary,"music":preflight.music_summary,"edl":[x.model_dump(mode="json") for x in (preflight.request.edl if preflight.request else ())]},ensure_ascii=False,sort_keys=True,indent=2))}</pre></section>'
        else: preflight_html="<p>Preflight nu a fost rulat.</p>"
        versions=[]
        for value in reversed(service.versions()):
            preview=f"/projects/{project_id}/composition/versions/{value.version}/preview"
            actions=f'<form method="post" action="/projects/{project_id}/composition/approve"><input type="hidden" name="version" value="{value.version}"><button>Aprobă final</button></form><form method="post" action="/projects/{project_id}/composition/reject"><input type="hidden" name="version" value="{value.version}"><button>Respinge</button></form>'
            versions.append(f'<section><h2>Randare {value.version} — {value.status.value}</h2><p>Durată: {value.duration_seconds}</p><video controls preload="none" src="{preview}"></video><div class="stage-actions">{actions}</div></section>')
        controls=(f'<form method="post" action="/projects/{project_id}/composition/preflight"><button>Preflight</button></form>'
            f'<form method="post" action="/projects/{project_id}/composition/render"><label><input type="checkbox" name="confirm_render" value="yes" required> Confirmă randarea FFmpeg</label><button>Compune videoclipul</button></form>')
        if versions: controls+=f'<form method="post" action="/projects/{project_id}/composition/render"><input type="hidden" name="confirm_render" value="yes"><button>Randează din nou</button></form>'
        return self._page("Compoziție",f'<main><a href="/projects/{project_id}">← Proiect</a><h1>Compoziție finală</h1>{error_html}<div class="stage-actions">{controls}</div>{preflight_html}{"".join(versions)}</main>')
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

def create_application(projects_root=None,lyrics_provider=None,music_provider=None,planning_builders=None,asset_provider=None,composition_renderer=None,services=None,recovery_service=None):
    return LocalWebApplication(projects_root,lyrics_provider,music_provider,planning_builders,asset_provider,composition_renderer,services,recovery_service)
def create_app(*,settings,services):
    application=create_application(settings.projects_root,services=services); application.settings=settings; return application
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
