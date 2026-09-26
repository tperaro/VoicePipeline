**Probe report: capture-av (single ffmpeg process for webcam + mic + preview tap)**

The camera and GPU were free at the start (`fuser /dev/video0` rc=1, no competing processes, GPU at 0%), so every test below ran. About 30 short takes were recorded and every .mkv and .wav has been deleted. No preview frame was ever saved or looked at. Only text logs and ffprobe JSON are kept, in /tmp/claude-1000/-home-peras-gitperaro-thiago-knowledge/ed300675-58f2-4b29-8f05-0e27e4d61468/scratchpad/probes/capture-av/ (harness.py, analyze.py, t3–t11 scripts, *.stderr.log, *.probe.json). Nothing under /home/peras/orochi-ia-homenagem was changed. The one device change was a camera setting in test 9, which I put back to its original value and re-checked.

## 1. Working command (verified)

This is the recommended variant. It puts the mic **first** and adds `make_zero`; it was verified in the t7 audiofirst runs:
```python
["ffmpeg","-hide_banner","-loglevel","info","-y","-copyts",
 "-f","pulse","-thread_queue_size","1024","-sample_rate","48000","-channels","1","-i",MIC,
 "-f","v4l2","-input_format","mjpeg","-video_size","1280x720","-framerate","30","-ts","mono2abs","-thread_queue_size","512","-i","/dev/video0",
 "-map","1:v","-map","0:a","-c:v","copy","-c:a","pcm_s16le","-avoid_negative_ts","make_zero","-f","matroska",OUT_MKV,
 "-map","1:v","-vf","scale=480:-2,fps=15","-pix_fmt","rgb24","-f","rawvideo","pipe:1"]
```
- **Flag order:** the order from the spec is valid and ffmpeg raised no complaints. `-copyts` is global, the `-ts` flag and the `-thread_queue_size` flags are per input, and `-avoid_negative_ts` is an option on output A. The log line "Detected monotonic timestamps, converting" confirms `-ts mono2abs` took effect.
- **Preview frame size:** exactly 480x270 rgb24, which is 388,800 bytes per frame. There were 0 leftover partial bytes in every run, so no size adjustment was needed.

## 2. Measured numbers (verified)

**Stopping**
- **`q` via stdin:** exits in 0.21–0.26 s with rc=0 and the file is finalized. After a 3 s stall it took 0.565 s.
- **SIGTERM or SIGINT:** exits in about 0.21 s with **rc=255** ("Exiting normally, received signal 15"). The MKV and WAV are finalized.

**Startup**
- **First preview frame:** 0.44–0.56 s after Popen. When the mic input opened slowly it took 1.1–1.2 s, because no output starts until every input is open.
- **Reopening the camera:** a new recording process gets its first frame 0.46 s after the previous process exits, or 0.68–0.73 s after the stop command. No "busy" error appeared, with `q`, SIGTERM or SIGINT.

**Real frame rate**
- **As the camera is set now:** 14.2–14.8 fps, with frame gaps of 63–68 ms. The stream header still claims 30/1.
- **Cause:** the camera setting "Exposure, Dynamic Framerate" (V4L2 control 0x9a0903) is currently 1, although the driver default is 0. With it set to 0 the camera gave 29.3 fps (gaps of 32–33 ms). I restored it to 1 and checked.
- **First-frame gap:** 132–164 ms normally, and 496 ms on a cold start.

**Sizes and cost**
- **File size:** MJPEG copy is about 0.65 MB/s at 15 fps and 1.0 MB/s at 30 fps. PCM audio adds 96 KB/s.
- **ffmpeg CPU:** 10.7% of one core at 15 fps and 18.5% at 30 fps. Memory (RSS) is 115 MB.

**Audio vs video start (audio minus video, from the input start values)**

| Input order | Offsets seen (s) |
|---|---|
| Video first | +0.039, +0.078, +0.08, +0.115, +0.54 (x3), +1.58, +2.21 |
| Audio first | −0.211, −0.217, −0.293 |

With audio first, the audio always covers video frame 0. With video first, up to 2.2 s at the start of the video had no audio.

**Timestamp variants: does the file keep the offset?**

| Run | Flags | Video start | Audio start | Result |
|---|---|---|---|---|
| A | mono2abs, copyts | 1790389710.834 | 1790389711.375 | Offset 0.541 kept, but timestamps are huge: format.duration is 1,790,389,716 s and the MKV DURATION tag reads "497330:28:36". Broken for players and later tools. |
| B | mono2abs, copyts, make_zero | 0.000 | 0.538 | Duration 5.473 s. Offset kept exactly. **Use this.** |
| D | mono2abs, no copyts | 0.000 | 0.000 | Inputs were 0.039 apart. The offset is thrown away. |
| E | mono2abs, copyts, start_at_zero | 0.000 | 0.000 | Same as D: each input is zeroed separately. |
| C | no mono2abs, copyts | 51522.409 | 1790389775.899 | Video uses uptime, audio uses wall clock. Only 2,400 samples (0.05 s) of about 5.7 s of audio were written, with rc=0 and **no warning**. |
| F | no mono2abs, copyts, make_zero | 0.000 | 1790338253.489 | Same silent audio loss. |

**Audio timestamp jitter:** the first mic packet is stamped 60–100 ms late. In steady state, packet time ≈ first_pts + samples/48000 − 62 to 70 ms. The end of the audio lines up with the end of the video within −60 to +80 ms.

**Failure detection**

| Case | Exit code | Time to fail | stderr |
|---|---|---|---|
| Camera busy (second process) | 240 (−EBUSY) | 64 ms | "Error opening input: Device or resource busy" |
| `/dev/video1` (metadata node) | 231 | 64 ms | "Inappropriate ioctl for device" |
| Missing device node | 254 | 64 ms | "No such file or directory" |
| **Invalid mic (pulse) name** | **0, no error** | n/a | PipeWire silently records from the **default** mic. `pactl list source-outputs` showed "Source: 54" with target.object set to the bogus name. |

**Audio-only ffmpeg as a parecord replacement**

`ffmpeg -f pulse -thread_queue_size 1024 -sample_rate 48000 -channels 1 -i MIC -c:a pcm_s16le -f wav out.wav`

| Stop | Exit code | Time to exit | WAV header |
|---|---|---|---|
| `q` | 0 | 0.164 s | RIFF and data sizes correct, 3.95 s |
| SIGTERM | 255 | 0.063 s | Correct, 4.00 s |
| SIGINT | 255 | 0.314 s | Correct, 4.00 s |

soundfile and ffprobe agreed in every case.

**When the preview reader stops draining the pipe**
- **Reader paused for 3 s:** nothing was lost. There were no video gaps, audio was continuous, and `q` still worked. The input queues (512 and 1024 packets) absorb the pause.
- **Reader dead:** **ffmpeg hangs.** `q` was ignored, SIGTERM was ignored for 3 s, and only SIGKILL worked. The MKV was not finalized and held only the first 2.2 s of a 7 s take; the rest was lost.
- **Reader dead, then the app closes its end of the pipe:** ffmpeg logs "Error submitting a packet to the muxer: Broken pipe" for the preview output and keeps recording the file. `q` then works in 0.26 s but **rc=224**. The MKV is complete (7.78 s, audio and video aligned).
- **Risk:** high. A stuck or crashed reader silently loses the take.

**If the Python app crashes (`os._exit`)**
- **Without protection:** ffmpeg keeps recording camera and mic. It was still alive 5 s later. This also happened by accident once during the probe: an ffmpeg left behind by a crashed test script kept recording until I killed it.
- **With `prctl(PR_SET_PDEATHSIG, SIGTERM)` in `preexec_fn`:** ffmpeg exits 0.2 s after the app dies and the MKV is finalized.

**Showing the preview in Tk** (tested with synthetic frames only, Tk 8.6, Pillow ImageTk available in the Applio venv)

| Method | Time per frame |
|---|---|
| New `ImageTk.PhotoImage` each frame | 0.44 ms |
| `paste()` into one reused `PhotoImage` | 0.15 ms |
| `tk.PhotoImage(data=PPM)` | 0.54 ms |

**Other**
- **Truncated first frame:** the first MJPEG frame is often cut short ("EOI missing, emulating" in 11 of 30 logs). It is harmless.
- **Full decode check:** 0 errors.
- **At 30 fps:** there was a "non monotonically increasing dts 145 >= 145" warning when retiming to constant frame rate. Set the fps explicitly (`-fps_mode` or the `fps` filter) when encoding later.

## 3. Pitfalls and what is not verified
- **Timestamp flags:** the video camera must use `-ts mono2abs` (or `abs`) together with `-copyts` and `-avoid_negative_ts make_zero`. Leaving out mono2abs silently loses the audio. Leaving out copyts (or using start_at_zero) throws away the audio/video offset.
- **Wrong mic name:** a bad or unplugged mic name does not fail. It records whatever the default mic is.
- **Real frame rate:** the header says 30 but the camera delivers about 15 fps with the current exposure setting.
- **Lip sync (not verified):** true sync was not checked. That needs a clap test, which I could not do without the user. The mic's steady-state skew (about −65 ms) and the camera's internal delay (unknown) partly cancel each other out.
- **Image quality at 30 fps (not verified):** with dynamic framerate off, the image may be darker in low light. I did not look at the image.
- **Thread rule (inferred from the prctl(2) documentation):** PDEATHSIG fires when the *thread* that started ffmpeg exits. So Popen must run on a thread that lives as long as the app, such as the Tk main thread, not a short-lived worker.

## 4. Recommendations for the Recorder class

**Starting a recording**
- Check the mic name against `pactl list short sources` before starting.
- About 1 s after starting, run `LC_ALL=C pactl list source-outputs`. Find the block with `application.process.id="<pid>"` and check that its "Source: <idx>" matches the chosen mic.
- Call Popen on the Tk main thread with:
  - `stdin=PIPE` and `stdout=PIPE`;
  - `stderr` to a **log file**, not a pipe, so there is no third pipe that can block;
  - `bufsize=0`;
  - `preexec_fn` setting `prctl(1, SIGTERM)` (PR_SET_PDEATHSIG).
- If ffmpeg exits within 0.5 s, read the end of the log and map the exit code: 240 means camera busy, 254 means no device, 231 means wrong video node.

**Preview reader thread**
- It never touches Tk.
- It loops reading exactly 388,800 bytes per frame and stores only the latest frame and a sequence number under a lock.
- It stops on end of file.
- Wrap it in try/except. If anything goes wrong, close `p.stdout` so ffmpeg cannot block.
- Keep draining until ffmpeg reaches end of file, even after the stop command.

**Tk side**
- Poll every 66 ms with `root.after(66, …)`. When the sequence number changes, use `Image.frombuffer("RGB",(480,270),buf,"raw","RGB",0,1)` and paste it into one reused `ImageTk.PhotoImage`.
- As a watchdog, warn if no new frame arrives for more than 2 s while recording.

**Stop protocol (without blocking the UI)**
1. Write `b"q\n"` to stdin.
2. Keep the reader draining.
3. Wait up to 5 s on a worker thread.
4. If ffmpeg is still running, close `p.stdout` and wait 3 s.
5. Then send SIGTERM and wait 3 s.
6. Then SIGKILL.
7. Report the result with `root.after`.
8. Accept exit codes 0, 255 or 224, but decide success by running ffprobe on the file: both streams present and duration greater than 0.

**Aligning audio for the voice replacement**
- Keep the mic as the first input.
- After recording, run ffprobe on the MKV and compute `offset = video.start_time - audio.start_time` (about 0.21–0.29 s).
- Extract the audio for RVC with `-af atrim=start=<offset>,asetpts=N/SR/TB` so sample 0 lines up with video frame 0. This step is inferred; it was not run through RVC.
- Add a user-adjustable `av_offset_ms` setting (default 0) and calibrate it once with a clap test.

**Preview-only mode and switching to recording**
- Run preview-only as a separate process that has only the pipe output.
- To record, stop it with `q` and start the recorder. Expect about a 0.7 s gap in the preview.

**Other choices**
- Replace parecord with ffmpeg audio-only for audio-only takes. It was verified above, and it gives one code path and the same stop protocol.
- Optionally offer a "30 fps" toggle that sets control 0x9a0903 to 0 with `fcntl.ioctl(fd, VIDIOC_S_CTRL=0xC008561C, struct("<Ii", 0x9a0903, 0))` and restores the previous value afterwards. The user's current value is 1.