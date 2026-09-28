# Media-correctness review of Voice Studio v2: lip-sync anchor, mic drift, capture and render

Tags: **VERIFIED** means I ran it here. **PROBE** means another probe measured it. **INFERRED** means I did not run it.

I changed nothing under /home/peras/orochi-ia-homenagem; a check for newer files came back empty. My scratch dir is `/tmp/claude-1000/-home-peras-gitperaro-thiago-knowledge/ed300675-58f2-4b29-8f05-0e27e4d61468/scratchpad/probes/review-media/`. It holds `drift.py`, `drift_*.framemd5` (packet timestamps and MD5 hashes only, no audio), their logs, and `pdeath.py`. All media test files are deleted.

## Issues

### 1. MAJOR — audio is anchored on the wrong timestamp and ignores the mic's clock drift
`extract_aligned_audio` takes the audio start from the stream's `start_time` (the first pulse packet) and then assumes exactly 48000 samples per second (`asetpts=N/SR/TB`). Three problems follow.

- **The first packet's timestamp is unreliable in A/V takes.** It does not match the timestamps of the packets that follow. I measured this from the capture probe's JSON, comparing `first_pts + samples/48000` with the end of the last packet.
  - Across 20 A/V takes it was off by −13 to +91 ms.
  - In the recommended audio-first takes it was +25, +66 and +71 ms.
  - This error changes from take to take, so a fixed `av_offset_ms` cannot cancel it.
  - In audio-only 240 s runs, the error was only −10.6 and +3.2 ms, and after about 2 s the timestamps stayed within 1.1 ms (standard deviation) of a straight line (**VERIFIED**).
- **Both mics drift against the system clock (VERIFIED, 240 s runs, timestamps only).**

  | Mic | Drift | Effect after 5 min |
  |---|---|---|
  | Generalplus | +48.6 ppm | audio 14.6 ms early |
  | ME6S | −56.1 ppm | audio 16.8 ms late |

  `N/SR/TB` throws this information away. The camera's timestamps come from the kernel clock, so the mic drift becomes A/V drift. It adds to RVC's own drift (audio 20 ms early at 300 s, from the RVC probe).
- **A gap in the audio shifts everything after it (VERIFIED).** I made a synthetic MKV with a 0.5 s timestamp gap.
  - `asetpts=N/SR/TB` put the beep at 4.01 s instead of 4.5 s, so all later audio was 0.5 s early.
  - `aresample=async=1:min_hard_comp=0.03:first_pts=0` put it at 4.51 s, which is correct.

**Change:** fit `pts_i = a + b·cum_samples_i/48000` over packets after 2 s. Get the packet list from `ffprobe -show_entries packet=pts_time,size`.
- Use `a` as the audio start.
- If `|b|·duration` is over 5 ms, add `asetrate=round(48000/(1+b)),aresample=48000:resampler=soxr` after `asetpts` (INFERRED; standard filters, not run here).
- If any leftover step is over 30 ms, warn the user and switch to the async `aresample` path. Only do that when a gap is found: on the start bias it would cut up to about 60 ms of real audio (INFERRED).
- Save `a`, `b` and the gap count to `take.json`.
- Unit-test the fit with the real timestamp tables in `drift_*.framemd5`. Synthetic lavfi media has neither the start bias nor the drift.

### 2. MAJOR — a duration-limited take can come out empty
If the duration field is implemented with an output `-t` together with `-copyts`, the take is empty (**VERIFIED**). I ran this on an MKV whose timestamps are shifted by 1.79e9 s: ffmpeg printed "Output file is empty, nothing was encoded", exited with rc=0, and wrote a 711-byte file ffprobe could not read. The same command without `-copyts` gave 4.000 s.

**Change:** enforce the duration with a Tk `after()` timer that runs the normal `q` stop. Never use `-t`, `-to` or `-frames` on the recorder. Keep checking success with ffprobe (both streams present, length above 0).

### 3. MAJOR — the watermark disappears completely if the frame size is not 1280x720
The overlay puts a full-frame PNG at 0:0. With the probe's `wm_1280x720.png` over a 640x480 or 800x600 frame, 0 watermark pixels end up visible (**VERIFIED**, cropping the PNG's alpha channel with PIL). This can happen two ways:
- `/dev/video0` can point to a different camera after USB re-enumeration.
- ffmpeg's v4l2 input only logs "driver changed the video from …" at info level and carries on at another size (INFERRED from the ffmpeg source).

**Change:**
- Use `/dev/v4l/by-id/usb-Sonix_Technology_Co.__Ltd._A4tech_HD_720P_PC_Camera_SN0001-video-index0` (it exists, **VERIFIED**).
- Generate the PNG from the frame size ffprobe reports for each take, and assert that PNG size equals frame size before rendering.
- After rendering, decode frames 0, middle and last, and check that the band and badge areas are darker than the source (the render probe's brightness test).
- If any check fails, do not move the file to `videos_finais`.

### 4. MAJOR — partial or unchecked MP4s can be uploaded
Rendering straight into the synced `videos_finais/` means a crash, timeout or failed nvenc run leaves a broken file. With `+faststart` the moov atom is only written at the end, so a killed render leaves a file with no moov (INFERRED). The batch CLI would then upload it.

**Change:**
- Render to `recordings/<ts>/render.part.mp4`.
- Check it: frame count equals `n_frames`, audio duration equals video duration, the watermark check from issue 3 passes, and the tags are present.
- Then `os.replace` it into `videos_finais/`.
- Give rclone an include filter of `*_IA.mp4`.

### 5. MAJOR — two capture-probe failure modes are not handled in the design
- **Stuck or dead preview reader loses the take (PROBE).** Wrap the reader in `try/finally: p.stdout.close()` and add a 2 s no-frame watchdog. Read with a `readinto` loop into a 388,800-byte buffer: with `bufsize=0` a pipe read returns at most 64 KiB at a time. The probe harness already loops like this.
- **A missing or wrong mic silently records the default mic (PROBE).** Check the name against `pactl list short sources` before starting. About 1 s after start, check that the matching entry in `pactl list source-outputs` points at the chosen source.

### 6. MINOR — `preexec_fn` is risky in a process full of threads
The app will have Tk, reader threads and torch/OpenMP threads loaded in-process. Python documents `preexec_fn` as unsafe with threads: the child can deadlock before exec, and `Popen` then blocks the Tk main thread.

**Change:** start the recorder with `["setpriv","--pdeathsig","TERM","--","ffmpeg",…]`. setpriv is util-linux 2.39.3; the child died together with a parent that called `os._exit` (**VERIFIED**). This also lets Python start the child without running Python code in it. Keep `Popen` on the main thread.

### 7. MINOR — run RVC in a long-lived worker subprocess, not in the Tk process
- Torch keeps 2.7–5.3 GB of VRAM reserved for as long as the app is open (PROBE).
- A native crash in RVC would kill the app, and the parent-death signal would then stop any recording in progress.
- A conversion running on a thread cannot be cancelled.

GPU numbers (**VERIFIED**): idle 833 MiB; one 720p nvenc session adds about 210 MiB (peak 1059 MiB total). RVC peak (6367 MiB) plus nvenc is about 6.6 GB of 8 GB, so the two can coexist, but run GPU jobs one at a time through a single queue. Call `torch.cuda.empty_cache()` after each conversion. For takes over 40 s use the anchored chunking: it removes RVC drift and lowers peak VRAM to about 3.3 GB (PROBE).

### 8. MINOR — render ffmpeg can stop if the app is started from a terminal
Every ffmpeg call except the recorder should get `-nostdin` and `stdin=DEVNULL`. Otherwise, if the app is started from a terminal in the background, ffmpeg's terminal handling can suspend it (SIGTTOU/SIGTTIN) and the render hangs until it times out (INFERRED). Nice the render (`os.nice(10)`) so a take recording at the same time keeps CPU priority.

### 9. MINOR — the first frame of each video may show a broken image
The first MJPEG frame is often cut short (11 of 30 logs), and the gap to the next frame is 132–496 ms (PROBE). `fps=30` shows that frame repeatedly for up to 0.5 s. **Change:** anchor video on packet 1 in both `extract_aligned_audio` and `render`. That the frame looks broken is INFERRED; the probe never looked at the image.

### 10. MINOR — frame rate limits lip-sync precision and calibration
- With "Exposure, Dynamic Framerate" on, the real rate is about 14.5 fps while the header says 30. At 15 fps, mouth positions are only accurate to ±33 ms, so a clap-test calibration at 15 fps is noise.
- **Change:**
  - Calibrate `av_offset_ms` with the camera at 30 fps (dynamic framerate off) and take the median of at least 5 claps.
  - Apply `av_offset_ms` at render time so changing it does not need a new RVC run, and fix its sign (positive means audio delayed).
  - Show the measured fps from the preview sequence counter and warn below 12 fps.
  - If the app changes camera control 0x9a0903, save the original value to a state file, restore it on exit, and restore it at the next start after a crash. The setting persists in the driver and affects other apps.

### 11. MINOR — audio-level pitfalls
- A +15 dB `volume` boost on s16 audio hard-clips.
- If a limiter is added, `alimiter` defaults to `latency=false` (**VERIFIED** with `ffmpeg -h`), which shifts audio by its 5 ms attack. Use `latency=1`.
- Never put `silenceremove`, or `loudnorm` without `-ar`, in the chain.
- Render from the converted WAV, never from the MP3 (encoder priming).
- Take the output sample rate from the converted WAV, since each model may differ (silvio is 40 kHz). The probe verified the render at 32 and 40 kHz.

### 12. MINOR — sizes, quota and camera use
- The final MP4 comes out around 8.3 Mbps with nvenc and 16.7 Mbps with the libx264 fallback (PROBE, noisy synthetic source). Uploads count against the user's own 15 GB. Add `-maxrate 6M -bufsize 12M` and use 128k mono AAC.
- Raw MJPEG is 0.65–1.0 MB/s. There is 291 GB free (**VERIFIED**), but add a free-space check before recording and a maximum take length; RVC timing was validated up to 300 s.
- Idle preview mode keeps the camera exclusively open (LED on, other apps such as Meet locked out, about 10% of a core). Run it only while the recording section is active.
- Light the REC indicator only when the recorder's first frame arrives. Start-up takes 0.44–1.2 s, plus about 0.7 s when switching from preview (PROBE).

## Draft decisions the evidence confirms
- One ffmpeg process with `-ts mono2abs`, `-copyts`, `-avoid_negative_ts make_zero`, mic as the first input, and MKV output with MJPEG stream copy plus PCM (PROBE runs B and t7). MKV also survives crashes better.
- Preview tap as rawvideo rgb24 at 480x270 and 15 fps, with a thread that keeps only the latest frame, polled from Tk and pasted into one reused `PhotoImage` (0.15 ms per frame).
- Stopping with `q` (0.21–0.26 s, rc=0), with rc 255 and 224 accepted and success decided by ffprobe.
- ffmpeg recording audio-only takes in place of parecord.
- The general shape of `extract_aligned_audio`: within 0.08 ms on synthetic takes (PROBE), but see issue 1 for where the audio start must come from.
- Render: `setpts=PTS-STARTPTS,fps=30` constant frame rate, correct on the VFR take. Frame count from packet timestamps, not the header's 30 fps. `apad=whole_len` plus `atrim` rather than `-shortest`, which hangs on 6.1.1. Explicit `aresample=48000`, since without it the aac encoder silently goes to 44.1 kHz. `+faststart`. nvenc with libx264 fallback, both verified.
- PIL watermark PNG with DejaVu fonts by file path, staying on screen to the last frame with no `-loop`. Note that fontconfig's "Sans" resolves to Noto, which has no "●".
- RVC with `split_audio=False` and the existing parameters keeps timing up to about 41 s. Loading Applio once and reusing it is fine.
- Handing results from threads to Tk through a queue polled with `root.after`.
- The `av_offset_ms` setting is needed, but only for the constant camera delay once issue 1 is fixed.
- nvenc and RVC fit in 8 GB of VRAM together (about 7.5 GB worst case), provided GPU jobs run one at a time.