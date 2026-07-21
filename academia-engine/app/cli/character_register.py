import argparse
from pathlib import Path
from pydantic import ValidationError
from app.characters import CanonicalCharacterProfile,CharacterRegistry,CharacterRegistryError
from app.cli.song_validate import configure_utf8_output

def main():
    configure_utf8_output(); parser=argparse.ArgumentParser(description="Register a canonical recurring character.")
    parser.add_argument("--input",required=True,type=Path); parser.add_argument("--runtime-root",type=Path,default=Path(".runtime")/"characters"); args=parser.parse_args()
    try:
        profile=CanonicalCharacterProfile.model_validate_json(args.input.read_text(encoding="utf-8")); path=CharacterRegistry(args.runtime_root).register(profile)
    except (OSError,ValidationError,CharacterRegistryError): print("Character registration failed safely."); return 1
    print(f"Character ID: {profile.character_id}"); print(f"Name: {profile.name}"); print(f"Saved path: {path}"); print("Registration: succeeded"); return 0
if __name__=="__main__": raise SystemExit(main())
