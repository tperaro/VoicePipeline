**Adversarial review of Voice Studio v2 (robustness and UX)**

I changed nothing under /home/peras/orochi-ia-homenagem and nothing in ~/.config (`~/.config/rclone` still does not exist). My own checks are in /tmp/claude-1000/-home-peras-gitperaro-thiago-knowledge/ed300675-58f2-4b29-8f05-0e27e4d61468/scratchpad/probes/ux-review/ (pdeath.py, pdeath2.py, crop_9x16.png, crop_1x1.png).

## Issues

### BLOCKER

**B1. ffmpeg keeps recording after the app dies.**
- **What goes wrong:** the draft only stops ffmpeg with `q`. The capture probe verified that after an app crash, ffmpeg keeps recording camera and mic indefinitely: the camera light stays on, a hidden recording continues, and the disk fills at about 1 MB/s.
- **Change:** start every child process (recorder, preview, render, rclone) through a wrapper instead of `preexec_fn`:
  ```
  ["setpriv","--pdeathsig","TERM","--","ffmpeg",...]
  ```
  - `setpriv` is at /usr/bin/setpriv.
  - Python's docs say `preexec_fn` is not safe in a process with threads, and after Applio loads torch the process has many.
- **What I verified here:**
  - When the parent called `os._exit`, the child died.
  - When a worker thread spawned the child and then exited, the child was killed at once (state Z) while the app kept running. So a Recorder started from a short-lived worker thread would kill its own recording.
  - Race: `Popen` returns before `setpriv` has run its prctl call. If the spawning thread exits immediately, the child survives with no protection (first test).
- **Rules that follow:**
  - The Recorder and the preview process are started on the Tk main thread.
  - Render, upload and convert children are started by the same worker thread that waits for them.

### MAJOR

**M1. A stuck preview reader loses the take.**
- **What goes wrong (verified by the capture probe):** if the preview reader dies, ffmpeg hangs, ignores `q` and SIGTERM, and only SIGKILL works. The MKV then holds 2.2 s of a 7 s take. The draft has no escalation.
- **Change:**
  - The reader catches any error and closes `p.stdout` in its handler.
  - Stop in steps: `q`, wait 5 s → close stdout, wait 3 s → SIGTERM, wait 3 s → SIGKILL. Run these on a worker thread and report the result through the queue.
  - Accept exit codes 0, 255 and 224. Decide success by running ffprobe on the file: both streams present and duration > 0.

**M2. Capture flags as drafted are broken.**
- **What goes wrong (verified):** the draft puts video first and leaves out `-avoid_negative_ts make_zero`.
  - Video first left up to 2.2 s at the start with no audio.
  - Without make_zero the file reports a duration of about 1.79e9 s, which breaks players and later tools.
- **Change:** use the probe's command: mic as the first input, plus `make_zero`.

**M3. A wrong mic records the default mic without any error.**
- **What goes wrong:** verified for an invalid mic name. For a mic unplugged mid-take, I infer the same fallback from how PipeWire moves streams (not tested).
- **Change:**
  - Before starting, check the name against `pactl list short sources`.
  - About 1 s after start, check `LC_ALL=C pactl list source-outputs` for our pid, then repeat every 2 s during the take. If the source changes or disappears, stop and show "Microfone desconectado ou trocado".
  - After the take, run volumedetect (the app already does). If max < −45 dB, show "Microfone mudo?".
  - Apply the same checks to audio-only takes.

**M4. The camera is identified by an unstable path.**
- **What goes wrong:** the draft uses /dev/video0. The number can shift on replug. /dev/video1 (the metadata node) fails with rc 231.
- **Change:**
  - Use `/dev/v4l/by-id/usb-Sonix_Technology_Co.__Ltd._A4tech_HD_720P_PC_Camera_SN0001-video-index0` (verified it exists and points to video0).
  - The camera combobox lists only `*-video-index0` entries.
  - Camera unplugged mid-take (not tested): the watchdog sees no preview frame for 2 s, stops the take gracefully, keeps what was recorded and says "A câmera parou de enviar imagem".

**M5. Preview while idle locks the camera.**
- **What goes wrong:** a second process opening the camera gets EBUSY (verified). With preview running whenever the app is open, Meet, Zoom or OBS cannot use the camera while Voice Studio is merely open, and the light is always on.
- **Change:**
  - Preview runs only while "Gravar vídeo" is checked and a "Câmera ligada" toggle is on.
  - Stop it when the window is minimized (`<Unmap>`) and after 5 min idle.
  - When our process gets EBUSY (rc 240), show "Câmera em uso por outro programa (Meet/Zoom/OBS?). Feche e clique Tentar de novo."

**M6. Applio depends on the process working directory.**
- **Evidence (read in the code):**
  - `now_dir = os.getcwd()` runs at import in core.py, rvc/lib/utils.py, rvc/infer/infer.py and pipeline.py.
  - `RMVPE` loads `os.path.join("rvc","models","predictors","rmvpe.pt")` on every conversion.
  - `assets/config.json` is also relative, and a `FileNotFoundError` there is swallowed, which silently changes the high-register pitch settings.
  - The current app calls `os.chdir` from inside worker threads.
- **What goes wrong:** any other module that changes directory or uses a relative path can break a conversion in progress. ffmpeg outputs with relative paths would land inside Applio/.
- **Change:**
  - Call `os.chdir(APPLIO_DIR)` once in `main`, before any thread starts, and never again.
  - Every studio path and every subprocess argument is absolute.

**M7. Errors are invisible.**
- **What goes wrong:** by default, exceptions from Tk callbacks and from threads go to stderr. When the app is started from the .sh or an icon, the user sees nothing and buttons stay disabled.
- **Change:**
  - Set `root.report_callback_exception` and `threading.excepthook` to write a short PT-BR line in the log panel and the full traceback to `studio.log`.
  - Every job sends a done or failed event in a `finally` block that re-enables the buttons.
  - Map exit codes to PT-BR messages:
    - 240: câmera em uso
    - 254: câmera não encontrada
    - 231: nó errado

**M8. The watermark does not survive cropping.**
- **What goes wrong (verified by cropping the probe's final nvenc frame):**
  - A 9:16 center crop (Reels, TikTok, Status) removes the "IA" badge entirely, and the first line reads "DA POR IA · PARÓDIA/HO".
  - A 1:1 crop also loses the badge and cuts the edges of the first line.
  - The first line is 764 px wide at 30 px, but the crop window is only 405 px.
- **Change:**
  - Fit all text inside the central 9:16 column: `max_w = round(h*9/16) − 2*margin`, which is 361 px at 720p.
  - Measured fits:
    - "VOZ GERADA POR IA" in bold 30 px is 347 px.
    - "Não é a voz real de Silvio Santos" in regular 22 px is 360 px.
    - "PARÓDIA / HOMENAGEM" at 24 px is 325 px; put it in the badge or on a third line.
  - Place the badge inside the safe column, at the top.
  - Add a unit test asserting that every opaque text pixel falls inside the safe column.
- **Not measured:** Reels and TikTok interface overlays also cover roughly the bottom 15–20%, which is where the band sits.

**M9. Output is not fail-closed: a broken or unwatermarked video can reach Drive.**
- **What goes wrong:**
  - A render that is killed partway leaves an MP4 without its moov atom in videos_finais/.
  - `rclone copy videos_finais/` then uploads the broken file to the shared folder. It can also pick up a file while a render is still writing it.
- **Change:**
  - Render to a `.part` name with an explicit `-f mp4`, then `os.replace` into videos_finais. Do the same for Applio's `output_path` and all other outputs.
  - The uploader includes only `*_IA.mp4`, excludes `.*`, and checks with ffprobe that the comment tag contains "IA".
  - `render()` refuses to run if the watermark PNG is missing or the model has no disclosure text. There is no code path that renders without the overlay.

**M10. First-run Drive setup and re-auth are missing from the draft.**
- **What the draft leaves out:**
  - Its own Google OAuth client_id is required: rclone's shared client_id is being retired during 2026.
  - The app must be moved to production ("PUBLISH APP"). In Testing mode the token expires after 7 days and uploads fail with `invalid_grant`.
- **Change:**
  - A PT-BR setup checklist, plus a "Configurar Drive" state in the app.
  - Map the states to messages:
    - rclone missing
    - remote not configured: exit 1, verified for an unknown remote
    - `invalid_grant`: "Reconectar" button running `rclone config update iavoz config_refresh_token=true` (not tested), with a timeout and cancel, because it waits on the browser
    - wrong Google account or no access: exit 3, "directory not found" → "Essa conta Google não tem acesso à pasta"
  - Recording and rendering work fully without rclone.

**M11. Token security.**
- **What goes wrong:** the refresh token is stored in plain text in `~/.config/rclone/rclone.conf`. With `scope=drive` it can read and delete the user's entire Drive.
- **Change:**
  - Authenticate rclone with a dedicated Google account that has edit access only to the shared folder. This needs no install. Encrypting the config with `secret-tool` would need an apt install; secret-tool is not installed here (verified).
  - Never use `-vv` or dump flags, never log `config show`, and never put rclone.conf in the project.

**M12. The window will not fit on screen.**
- **What goes wrong:** the window is fixed at 560x680 and cannot be resized. Adding the 480x270 preview plus steps 5 and 6 makes it roughly 1100 px tall (my estimate). The user has a 1920x1080 monitor (verified with xrandr), so the Drive button ends up off-screen.
- **Change:** use two columns (preview and recording on the left, steps and log on the right), about 1100x700, and make the window resizable.

### MINOR
- **Timer and thread discipline:**
  - Schedule the duration stop with `root.after(ms)` on the main thread, not `threading.Timer`.
  - Workers get a snapshot of the inputs when the button is clicked and never read a StringVar.
  - Only the main thread changes App state; the current app sets `self.boosted_wav` from a worker.
  - Keep a reference to the PhotoImage so Tk does not blank it.
- **Watermark on the preview:** draw the watermark over the preview so the user keeps their lips above the band. Measured cost is 0.344 ms per frame at 480x270, which is negligible at 15 fps. Add a faint 9:16 guide as well.
- **Cancel paths:**
  - Render: give it a timeout (the probe showed `-shortest` hanging) and a cancel button.
  - Upload: show progress and a Cancel button that sends SIGTERM. With no internet, rclone retries for minutes.
  - Conversion: no cancel needed. Measured times are 1.2–10 s, so just disable the buttons while it runs.
- **Closing the window:** during a recording, send `q` and wait up to 5 s while showing "Finalizando gravação…". During render or upload, ask for confirmation first.
- **Recovery on startup:**
  - Look for any `recordings/*/raw.mkv` without a `take.json` marked ok and check it with ffprobe. If it was never finalized (power loss or SIGKILL), remux it with `-c copy` to recover it.
  - Load the latest take into the UI so the user can convert or render after a restart.
  - If the camera is busy because of our own orphaned ffmpeg (check with `fuser -v`), offer "Encerrar gravação órfã".
- **rclone flags:**
  - Add `--drive-stop-on-upload-limit`. In the source, this makes `storageQuotaExceeded` a fatal error (exit 7) → "Seu Drive está cheio". The files count against the user's own 15 GB.
  - Drop `--checksum`: the default size + mtime check is enough, and `--checksum` rereads every local file on every run.
  - Use one lock shared by the app and the CLI (`flock` on `videos_finais/.envio.lock`) so two uploads cannot race and create duplicates.
  - Allow only copy, copyto and lsjson. `sync` would delete other people's files in the shared folder.
- **Extra disclosure:**
  - Drive description: `-M --metadata-set description="Voz gerada por IA…"`. The `description` key is writable in rclone 1.75.1 (`help backend drive`, verified); I did not test it against Drive.
  - Converted MP3s currently carry no disclosure at all. Add ID3 title and comment tags and name them `_IA.mp3`.
  - Do not add C2PA: there is no tool installed and platforms strip it.
- **Config:**
  - `tomllib` can only read, so settings the GUI changes (mic, camera, Drive link) go in `estado.json`, written with stdlib json and atomically.
  - Keep the per-model watermark texts as defaults in the code.
  - A malformed config shows a messagebox and falls back to defaults instead of crashing at startup.
  - `tomlkit` exists in the venv only as a transitive Applio dependency; do not rely on it.
- **Frame rate display:** the stream header says 30 fps but the camera delivers about 15 fps with the current exposure setting. Show the measured fps in the status line. Do not add the ioctl toggle: if the app crashes, the camera setting stays changed.
- **Long takes (outside my lens):** takes over about 41 s drift with plain `run_infer_script`. Either cap the take length or use the anchored chunking.

### YAGNI
- **pytest in the Applio venv:** use stdlib `unittest` instead. It needs no install and leaves Applio's venv alone.
- **`git init`:** not requested. If you keep it, add `__pycache__/`, `*.mp4`, `*.mkv`, `*.wav`, `*.mp3`, `*.part*` and `estado.json` to .gitignore.
- **`av_offset_ms`:** keep it in the config file only, with no control in the GUI.
- **"Carregar modelo" button:** conversion already loads the model lazily, so the button can go.
- **Module split:** the split is fine. Keep `drive.py` standard-library only so `enviar_drive.py` runs with the system python3. Merge `watermark.py` into `media.py` if it stays small.

## Draft decisions the evidence confirms
- **Single ffmpeg process:** copying MJPEG plus PCM, with a rawvideo preview tap, costs 18.5% of one core and 115 MB RSS at 30 fps.
- **Preview in Tk:** paste into one reused PhotoImage takes 0.15 ms per frame.
- **Stopping with `q`:** it exits in 0.21–0.26 s with rc 0, which makes it the right first step.
- **ffmpeg instead of parecord for audio-only takes:** the WAV header is correct whether it is stopped with `q`, SIGTERM or SIGINT.
- **Queue polled with `root.after`:** the current code calls `root.after` from worker threads and from `threading.Timer`, so this is the right fix.
- **Watermark:** a PIL PNG plus the overlay filter, DejaVu fonts loaded by path, and the image stays on screen to the last frame.
- **Audio alignment:** `extract_aligned_audio` measured an offset of 0.08 ms.
- **Encoder fallback:** the nvenc → libx264 fallback was verified.
- **MP4 output:** the metadata tags and `+faststart` work. Platforms are likely to strip the metadata, so the burned-in watermark is the protection that actually holds.
- **rclone install:** a static binary in `~/.local/bin`, with a SHA256 check against the PGP-signed sums (verified). `~/.local/bin` is already on PATH (verified).
- **Drive target:**
  - Only `videos_finais/` syncs, and `copy` never deletes anything.
  - Pass the folder ID from the link on each run (`--drive-root-folder-id`), so the user can change folders without signing in again.
  - Never use `--drive-shared-with-me`.
- **Folders:** one `recordings/<timestamp>/` folder per take, and the old flat takes are left untouched.