from pathlib import Path
from .workflow import WorkflowStateMachine,read_workflow_state

class ReadOnlyProjectService:
    def __init__(self,projects_root=None): self.root=Path(projects_root or Path.cwd()/".runtime"/"projects")
    def list_projects(self):
        if not self.root.is_dir(): return ()
        return tuple(sorted(path.parent.name for path in self.root.glob("*/project.json") if path.is_file()))
    def workflow(self,project_id):
        if not project_id or any(x not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for x in project_id): raise KeyError(project_id)
        project=self.root/project_id; state=project/"workflow"/"state.json"
        if state.is_file(): return read_workflow_state(state)
        if not (project/"project.json").is_file(): raise KeyError(project_id)
        return WorkflowStateMachine().initial(project_id)
