"""Structured English visual directions for non-English lyric scenes."""
from pydantic import BaseModel,ConfigDict,Field


class TranslatedVisualScene(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    scene_id:str=Field(min_length=1)
    english_visual_direction:str=Field(min_length=1,max_length=1800)


class TranslatedVisualScenes(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    scenes:tuple[TranslatedVisualScene,...]=Field(min_length=1)


class OpenAIVisualPromptTranslator:
    def __init__(self,api_key,model="gpt-5-mini",client=None):
        if not api_key and client is None: raise ValueError("OpenAI API key is required for visual prompt translation.")
        if client is None:
            from openai import OpenAI
            client=OpenAI(api_key=api_key)
        self.client=client; self.model=model

    def translate(self,scenes):
        requested={str(scene_id):tuple(str(x).strip() for x in texts if str(x).strip()) for scene_id,texts in scenes.items() if texts}
        if not requested: return {}
        source="\n\n".join(f"SCENE_ID: {scene_id}\nLYRICS:\n"+"\n".join(texts) for scene_id,texts in requested.items())
        try:
            response=self.client.responses.parse(model=self.model,
                input=[{"role":"system","content":("Translate each Romanian lyric stanza into a concise English visual direction for a children's animated video generator. "
                    "Preserve the exact scene_id. Describe concrete setting, visible objects, events, weather, and character actions. Do not add dialogue, text on screen, new named characters, or technical IDs. Return every scene exactly once.")},
                    {"role":"user","content":source}],text_format=TranslatedVisualScenes)
        except Exception as error:
            raise ValueError("English visual prompt translation failed. Check the OpenAI configuration and try again.") from error
        parsed=getattr(response,"output_parsed",None)
        if parsed is None: raise ValueError("OpenAI did not return translated visual directions.")
        result={item.scene_id:item.english_visual_direction.strip() for item in parsed.scenes}
        if len(parsed.scenes)!=len(requested) or set(result)!=set(requested):
            raise ValueError("OpenAI translation did not return every requested scene exactly once.")
        return result
