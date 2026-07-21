import argparse
from pathlib import Path
from app.characters import CharacterRegistry,CharacterRegistryError
from app.cli.song_validate import configure_utf8_output

def main():
    configure_utf8_output(); parser=argparse.ArgumentParser(description="Inspect a canonical recurring character.")
    parser.add_argument("--character-id",required=True); parser.add_argument("--runtime-root",type=Path,default=Path(".runtime")/"characters"); args=parser.parse_args()
    try: value=CharacterRegistry(args.runtime_root).get(args.character_id)
    except CharacterRegistryError: print("Character lookup failed safely."); return 1
    print(f"Character ID: {value.character_id}"); print(f"Name: {value.name}"); print(f"Canonical description: {value.canonical_description}")
    print("Behavior rules:"); [print(f"- {rule}") for rule in value.behavior_rules]
    print("Negative rules:"); [print(f"- {rule}") for rule in value.negative_rules]; return 0
if __name__=="__main__": raise SystemExit(main())
