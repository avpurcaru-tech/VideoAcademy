# First real project runbook

Acest runbook este pentru o execuție locală, supravegheată. Nu selecta proiectul `007`. Folosește proiectul `008` numai dacă există deja și operatorul îl selectează explicit; altfel creează un proiect nou din UI.

## Pornire și verificări

1. Pornește aplicația cu `python -m app.web_ui --config config/local.json --runtime-mode production`. Serverul trebuie să rămână pe `127.0.0.1:8080`.
2. Deschide `/settings`. Verifică providerii, runtime mode, projects root și FFmpeg. Cheile nu trebuie să apară.
3. Rulează `python -m app.cli.operational_preflight --config config/local.json --format text`. Pentru proiect folosește explicit `--project-id ID`. Nu activa connectivity decât dacă dorești un check extern read-only.
4. Creează episodul din `/projects/new`. Se creează `project.json`, `workflow/state.json` și directoarele locale; nu se generează conținut.

## Workflow supravegheat

5. În Versuri apasă **Generează**. OpenAI poate costa bani; confirmă numai această acțiune. Se creează `lyrics/version-NNN.json`.
6. Revizuiește, editează/regenerază numai versurile și aprobă versiunea dorită. Aprobarea creează `workflow/checkpoints/lyrics-approved.json`.
7. În Muzică apasă **Generează muzică** și confirmă separat costul Suno. Se creează `music/version-NNN/job.json`, metadata variantelor și MP3-urile.
8. Ascultă variantele, selectează una și aprob-o. Verifică taskId, audioId, durată și SHA. Se creează checkpoint-ul muzicii.
9. Apasă **Construiește alignment**, verifică coverage, cuvinte/linii nemapate și `review_required`, apoi aprobă.
10. Construiește ScenePlan, verifică timpii, source lines și warnings, apoi aprobă.
11. Construiește VisualPlan, verifică subjects, actions, environment, camera și constraints, apoi aprobă.
12. Construiește și revizuiește prompturile. Regenerarea unei scene nu trebuie să schimbe alte prompturi. Aprobă bundle-ul.
13. Generează fiecare asset separat. Fiecare scenă necesită confirmare separată și poate consuma credite. Se creează `assets/<scene-id>/version-NNN/`.
14. Previzualizează și aprobă fiecare asset. Regenerează numai scena necorespunzătoare; doar compoziția trebuie să devină stale.
15. Rulează composition preflight. Acesta nu trebuie să execute FFmpeg. Verifică muzica, EDL, duratele, fișierele și aprobările.
16. Apasă **Compune videoclipul** și confirmă separat FFmpeg. Se creează `composition/version-NNN/`; un fișier `.part` nu este output valid.
17. Previzualizează și aprobă rezultatul final. Se creează `workflow/checkpoints/composition-approved.json`.

## Costuri și recuperare

Acțiunile OpenAI, Suno și providerul de asset-uri pot consuma credite. Prețul este raportat numai dacă providerul îl configurează; altfel mesajul corect este „Cost necunoscut — această acțiune poate consuma credite.” Nu există o confirmare globală și nu există „run entire pipeline”. FFmpeg este local, dar randarea necesită confirmare separată.

Pentru o întrerupere, deschide `/jobs` sau `/projects/ID/jobs`. Folosește **Verifică status** înainte de **Reia jobul**. Pentru Suno cu task complet se reia numai downloadul MP3 lipsă, nu generarea. Pentru asset-uri se reia numai scena selectată. Nu abandona un job până când provider_job_id și riscul de duplicare a costului nu au fost verificate.

## Rollback și verificări

Din istoricul etapei selectează versiunea anterioară dorită: lyrics `version-002`, music `version-001` și varianta sa, sau asset-ul scenei respective. Versiunile noi nu sunt șterse. Selecția marchează strict etapele downstream stale; acestea se reconstruiesc și se aprobă manual, fără regenerare automată.

După fiecare aprobare verifică checkpoint-ul din `workflow/checkpoints/`: stage, approved version, dependency hashes, artifact paths relative și validation summary. Fișierele nu trebuie să conțină secrete. Înaintea fiecărei acțiuni plătite rerulează preflight-ul proiectului și verifică statusul joburilor.
