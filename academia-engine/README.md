# Academia Engine

## Kling Text-to-Video (contract provizoriu)

Serializarea Kling folosește numai valorile confirmate în exemplul oficial disponibil:
`resolution: "720p"`, `aspect_ratio: "16:9"`, `duration: 15`, `audio: "off"` și
`multi_shot: true`. Alte enum-uri sau durate nu sunt presupuse și nu sunt trimise.

Valorile de generare sunt încărcate din `KLING_RESOLUTION`, `KLING_DURATION`,
`KLING_AUDIO` și `KLING_MULTI_SHOT`.

Development:

```text
KLING_RESOLUTION=720p
```

Production:

```text
KLING_RESOLUTION=720p
```

Rezoluția validată este doar `720p`; lipsa lui `KLING_RESOLUTION` folosește `720p`.
Spațiile din jurul valorii sunt eliminate, dar literele și ortografia nu sunt modificate.

Comanda `python -m app.cli.kling_submit_test --confirm` nu trimite încă cereri și nu
consumă credite. Trimiterea reală rămâne blocată până este disponibilă schema oficială
pentru răspunsul create-task, inclusiv locația documentată a identificatorului de task.

Decizia de produs confirmă `720p` pentru Kling v3 Text-to-Video. Comanda
`python -m app.cli.kling_submit_test --confirm` poate trimite un singur task real și
poate consuma credite; fără `--confirm` nu trimite cereri.

## Story Engine

Configurează `OPENAI_API_KEY` și, opțional, `OPENAI_STORY_MODEL`. Furnizează
personajele ca un fișier JSON cu o listă de obiecte `Character`, apoi rulează:

```powershell
python main.py "Sistemul solar" --language ro --duration 120 --characters characters.json
```

Fișierul rezultat este `storage/projects/episode.json`.
