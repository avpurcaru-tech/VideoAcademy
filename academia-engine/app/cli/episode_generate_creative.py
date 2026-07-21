import argparse,json
from pathlib import Path

from pydantic import ValidationError

from app.config.environment import load_application_environment
from app.cli.song_validate import configure_utf8_output
from app.creative import (EducationalCreativeBrief,EpisodeGenerationService,EpisodeGeneratorRegistry,
                          EpisodeOutputConflictError,persist_episode_atomic)


def load_brief(path):
    if not path.exists(): raise RuntimeError(f"Creative brief file not found: {path}")
    if not path.is_file(): raise RuntimeError(f"Creative brief path is not a regular file: {path}")
    try: payload=json.loads(path.read_text(encoding="utf-8"))
    except OSError: raise RuntimeError(f"Creative brief file is unreadable: {path}") from None
    except json.JSONDecodeError: raise RuntimeError(f"Creative brief JSON is malformed: {path}") from None
    return EducationalCreativeBrief.model_validate(payload)


def main():
    configure_utf8_output(); load_application_environment(); parser=argparse.ArgumentParser(description="Generate one provider-neutral Episode from a creative brief.")
    parser.add_argument("--brief",required=True,type=Path); parser.add_argument("--generator",required=True,choices=("deterministic","openai"))
    parser.add_argument("--output",required=True,type=Path); parser.add_argument("--confirm",action="store_true")
    parser.add_argument("--overwrite",action="store_true"); parser.add_argument("--show-summary",action="store_true"); args=parser.parse_args()
    try: brief=load_brief(args.brief)
    except ValidationError:
        print("Creative brief validation failed."); return 1
    except RuntimeError as error: print(str(error)); return 1
    if args.generator!="deterministic" and not args.confirm:
        print("OpenAI Episode generation may consume credits."); print("No Episode was generated. Use --confirm to proceed."); return 2
    try:
        episode=EpisodeGenerationService(EpisodeGeneratorRegistry().resolve(args.generator)).generate(brief)
        persist_episode_atomic(episode,args.output,args.overwrite)
    except EpisodeOutputConflictError: print("Episode output already exists."); return 1
    except Exception as error: print(_safe_generation_error(error)); return 1
    print(f"Saved path: {args.output}")
    if args.show_summary:
        print(f"Episode ID: {episode.id}"); print(f"Title: {episode.title}"); print(f"Language: {episode.metadata.language}")
        print(f"Scene count: {len(episode.scenes)}"); print(f"Character names: {', '.join(value.name for value in episode.characters)}")
        print(f"Location names: {', '.join(dict.fromkeys(scene.location.name for scene in episode.scenes))}")
    return 0


def _safe_generation_error(error):
    from app.providers.openai_episode_provider import (OpenAIEpisodeAuthenticationError,OpenAIEpisodeConfigurationError,
        OpenAIEpisodeNetworkError,OpenAIEpisodeRateLimitError,OpenAIEpisodeStructuredOutputError,OpenAIEpisodeTimeoutError)
    current=error
    while current is not None:
        if isinstance(current,OpenAIEpisodeConfigurationError): return "OpenAI Episode provider configuration is missing."
        if isinstance(current,OpenAIEpisodeAuthenticationError): return "OpenAI Episode authentication failed."
        if isinstance(current,OpenAIEpisodeRateLimitError): return "OpenAI Episode rate limit was reached."
        if isinstance(current,OpenAIEpisodeTimeoutError): return "OpenAI Episode request timed out."
        if isinstance(current,OpenAIEpisodeNetworkError): return "OpenAI Episode network request failed."
        if isinstance(current,OpenAIEpisodeStructuredOutputError): return "OpenAI Episode structured output is invalid."
        current=getattr(current,"__cause__",None)
    return "Episode generation failed at a safe semantic boundary."


if __name__=="__main__": raise SystemExit(main())
