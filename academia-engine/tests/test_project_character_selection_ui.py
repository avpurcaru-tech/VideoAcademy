import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from app.characters import CanonicalCharacterProfile,CanonicalVisualReference,CharacterRegistry
from app.web_ui.project_creation import AtomicProjectCreationService,WebProjectManifest
from app.web_ui.server import create_application

class ProjectCharacterSelectionUiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.runtime=Path(self.temp.name); self.projects=self.runtime/"projects"; self.projects.mkdir(); self.characters=CharacterRegistry(self.runtime/"characters")
        self.image=self.runtime/"references"/"luca.png"; self.image.parent.mkdir(); self.image.write_bytes(b"png-reference")
        self.luca=self._profile("luca","Luca",self.image); self.characters.register(self.luca)
    def tearDown(self): self.temp.cleanup()
    def _profile(self,character_id,name,image=None):
        reference=CanonicalVisualReference(local_path=image,sha256=hashlib.sha256(image.read_bytes()).hexdigest()) if image else None
        return CanonicalCharacterProfile(character_id=character_id,name=name,canonical_description="Brunet, ochi căprui, prietenos.",personality_traits=("prietenos",),behavior_rules=("zâmbește",),negative_rules=("fără schimbări",),character_type="personaj principal",visual_reference=reference)
    def _payload(self,ids=("luca",),primary="luca"):
        return {"title":"Numere","description":"Învățăm numerele.","language":"ro","target_age":"2-5","aspect_ratio":"16:9","episode_theme":"Numere","educational_goal":"Numărare","notes":None,"selected_character_ids":ids,"primary_character_id":primary}
    def test_new_project_form_lists_existing_characters(self): self.assertIn("Luca",create_application(self.projects).dispatch("/projects/new").body.decode())
    def test_character_cards_show_reference_images(self): self.assertIn('/characters/luca/reference',create_application(self.projects).dispatch("/projects/new").body.decode())
    def test_character_selector_uses_character_ids(self): self.assertIn('name="selected_character_ids" value="luca"',create_application(self.projects).dispatch("/projects/new").body.decode())
    def test_single_selected_character_becomes_primary(self): self.assertEqual("luca",AtomicProjectCreationService(self.projects,self.characters).create(self._payload(primary=None)).characters.primary_character_id)
    def test_multiple_characters_require_primary_selection(self):
        max_profile=self._profile("max","Max",self.image); self.characters.register(max_profile)
        with self.assertRaises(ValueError): AtomicProjectCreationService(self.projects,self.characters).create(self._payload(("luca","max"),None))
    def test_primary_character_must_be_selected(self):
        with self.assertRaises(ValueError): AtomicProjectCreationService(self.projects,self.characters).create(self._payload(("luca",),"max"))
    def test_unknown_character_id_is_rejected(self):
        with self.assertRaises(ValueError): AtomicProjectCreationService(self.projects,self.characters).create(self._payload(("unknown",),"unknown"))
    def test_duplicate_character_ids_are_rejected(self):
        with self.assertRaises(ValueError): AtomicProjectCreationService(self.projects,self.characters).create(self._payload(("luca","luca"),"luca"))
    def test_character_without_required_reference_image_cannot_be_selected(self):
        self.characters.register(self._profile("tobi","Tobi")); body=create_application(self.projects).dispatch("/projects/new").body.decode(); self.assertIn("Fără imagine de referință",body)
        with self.assertRaises(ValueError): AtomicProjectCreationService(self.projects,self.characters).create(self._payload(("tobi",),"tobi"))
    def test_character_reference_route_prevents_path_traversal(self):
        app=create_application(self.projects); self.assertEqual(200,app.dispatch("/characters/luca/reference").status); self.assertEqual(404,app.dispatch("/characters/../reference").status); self.assertNotIn(b"png-reference",app.dispatch("/characters/../reference").body)
    def test_created_project_persists_character_ids_only(self):
        AtomicProjectCreationService(self.projects,self.characters).create(self._payload()); data=json.loads((self.projects/"008"/"project.json").read_text()); self.assertEqual({"primary_character_id":"luca","selected_character_ids":["luca"]},data["characters"])
    def test_project_manifest_does_not_duplicate_character_profile(self):
        AtomicProjectCreationService(self.projects,self.characters).create(self._payload()); text=(self.projects/"008"/"project.json").read_text(); self.assertNotIn("canonical_description",text); self.assertNotIn("visual_reference",text); self.assertNotIn("Brunet",text)
    def test_visual_plan_can_resolve_selected_character_ids(self):
        manifest=AtomicProjectCreationService(self.projects,self.characters).create(self._payload()); self.assertEqual(("luca",),manifest.selected_character_ids); self.assertEqual("luca",self.characters.require_many(manifest.selected_character_ids)[0].character_id)
    def test_prompt_generation_uses_existing_character_registry(self):
        manifest=AtomicProjectCreationService(self.projects,self.characters).create(self._payload()); self.assertEqual(self.luca,manifest.resolve_primary_character(self.characters))
    def test_empty_character_registry_shows_clear_message(self):
        empty=self.runtime/"empty-runtime"/"projects"; empty.mkdir(parents=True); body=create_application(empty).dispatch("/projects/new").body.decode(); self.assertIn("Nu există personaje create",body)
    def test_legacy_project_manifest_remains_readable(self):
        legacy={"project_id":"008","episode":{"title":"Vechi","description":"Test","language":"ro","target_age":"2-5","aspect_ratio":"16:9"},"main_character":{"name":"Luca","description":"Copil"}}; manifest=WebProjectManifest.model_validate(legacy); self.assertEqual("Luca",manifest.main_character.name)
    def test_project_007_is_not_modified(self):
        seven=self.projects/"007"; seven.mkdir(); target=seven/"project.json"; target.write_bytes(b"protected"); AtomicProjectCreationService(self.projects,self.characters).create(self._payload()); self.assertEqual(b"protected",target.read_bytes())
    def test_character_selection_performs_zero_external_calls(self):
        with patch("requests.get") as get,patch("requests.post") as post,patch("subprocess.run") as run: create_application(self.projects).dispatch("/projects/new"); AtomicProjectCreationService(self.projects,self.characters).create(self._payload()); get.assert_not_called(); post.assert_not_called(); run.assert_not_called()

if __name__=="__main__": unittest.main()
