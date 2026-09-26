**Probe result: RVC keeps timing. Converted audio can replace the original under the video with lips in sync. One small drift on takes over about 41 s has a verified fix.**

Nothing under /home/peras/orochi-ia-homenagem was written. I ran with `PYTHONDONTWRITEBYTECODE=1`, and `find -newer marker` came back empty after every run. Scratch dir: `/tmp/claude-1000/-home-peras-gitperaro-thiago-knowledge/ed300675-58f2-4b29-8f05-0e27e4d61468/scratchpad/probes/rvc-timing/`. It now holds only `measurements.json` (13.5 KB) and the small scripts: `runner.py`, `anchored.py`, `analyze.py`, `gap_edges.py`, `make_inputs.py`, `smi_peak.py`. Large WAVs were deleted.

## 1. Code path (read from the code)
`core.run_infer_script` → `VoiceConverter.convert_audio` (rvc/infer/infer.py) → `Pipeline.pipeline` (rvc/infer/pipeline.py).
- **Input handling:** `load_audio_infer` reads the file, downmixes to mono and resamples to 16 kHz (soxr_vhq). Audio is scaled down only if its peak is above 0.95.
- **Output sample rate:** the model's `tgt_sr` = `cpt["config"][-1]`, which is **40000 Hz** for silvio_350e (v2, HiFi-GAN, 400 samples per frame = 10 ms). `resample_sr=0` means no resampling (`self.tgt_sr != resample_sr >= 16000` is False). The file is written as PCM_16 WAV. `export_format="WAV"` means no format conversion.
- **Processing steps:** a 48 Hz high-pass (`filtfilt`, zero phase, so no delay), then a 1 s reflect pad on each side (`x_pad=1`).
  - Output length is quantised to 10 ms frames.
  - With `p_len=min(len//160, 2*hubert_frames)`, each chunk loses 0–1 extra frame.
  - The pads are cropped exactly (`[t_pad_tgt:-t_pad_tgt]`).
- **No silence trimming when `split_audio=False`.**
- **Internal chunking (applies even with `split_audio=False`):** if the input is over about 41 s (`x_max=41`), the pipeline cuts it into about 38 s pieces (`x_center=38`). Each cut is placed at the quietest point within ±6 s (`x_query=6`). Every internal cut can drop one 10 ms frame, and the result is concatenated. This is the drift source.
- **RMVPE:** runs once on the whole file. It chunks internally only above 32000 frames (320 s).
- **`volume_envelope=1.0`:** `change_rms` is skipped (no-op).
- **`clean_audio`:** noisereduce `reduce_noise` at `tgt_sr`. It keeps the length (not run).
- **`post_process`:** a pedalboard effects chain, off by default.
- **Output normalisation:** scaled down only if the peak is above 0.99.
- **`split_audio=True`:** `librosa.effects.split(top_db=60, frame 250 ms, hop 125 ms)`, then per-segment conversion, then `merge_audio`.
  - Leading silence is restored.
  - Gaps between segments are restored.
  - Each segment is padded back to its original length.
  - **Trailing silence after the last segment is dropped.**
  - Bug: if a converted segment came out longer than the original, it would prepend the difference as silence instead of trimming. This does not trigger in practice, because RVC output is never longer than its input.
- **Model reloads:**
  - `import_voice_converter` is `lru_cache`d.
  - `get_vc` reloads `net_g` only when the path changes, and HuBERT/contentvec only when the embedder changes.
  - **Every call** still re-creates RMVPE (loads the 181 MB rmvpe.pt) and re-reads the FAISS index (60 MB, `reconstruct_n`).
- **Randomness:** `net_g.infer` samples noise (`randn_like * 0.66666`). Two runs on the same input give different waveforms but identical length and timing (measured max abs diff 0.70).

## 2–3. Measurements (all run, silvio_350e, GPU idle at about 845 MiB)
Lag sign: negative means the converted audio is early.

| input | in → out duration | length diff | lag 10 ms frames (start / middle / end) | wall time | nvidia-smi peak total (process share) | torch alloc / reserved |
|---|---|---|---|---|---|---|
| a: 23.345 s (1 s silence before, 0.5 s after), 48 k | 23.340 s @ 40 k | −5.3 ms | 0 / 0 / 0–10 (low corr 0.5) | cold 6.66 s, warm 1.24 s | 3717 MiB (+2872) | 1791 / 2686 MiB |
| b: 88.964 s (4 takes, 0.3 s gaps) | 88.940 s | −24 ms | 0 / 0 / **−10** (−12 at 1 ms hop) | **2.80 s** (warm) | **6367 MiB (+5522)** | 3274 / 5336 MiB |
| c: 300 s (extra test) | 299.960 s | −40 ms | 0 / 0 / **−20** | 10.28 s (cold) | 6023 MiB (+5178) | 3450 / 4992 MiB |
| existing app output take_20260923_150423 | 21.845 → 21.840 s | −5.3 ms | 0 / −1 / −7 (1 ms hop, corr 0.5) | – | – | – |

- **Onset and offset in (a):** onset 0.991 → 0.989 s, offset 22.855 → 22.853 s, so within 2 ms. The digital-zero lead-in stays silent in the output (−90 dBFS).
- **Drift in b:** at 10 s intervals the lag is 0 ms up to 60 s, then −10 ms from 70 s. That is a step at an internal chunk boundary, not a gradual slope.
- **Drift in c:** 0 ms up to 140 s, −10 ms from 150 to 220 s, −20 ms from 230 to 300 s.
- **Gap-edge check (b):** voice onsets after the inserted gaps land at −2.3, −5.7 and −12.0 ms at 22, 50 and 70 s, which agrees with the lags above.
- **`split_audio=True`:**
  - a split into only 1 chunk, because real mic noise stays within 60 dB of the peak. It lost the trailing silence: −345 ms.
  - b split into 3 chunks (only thanks to the digital-zero gaps). Length diff −0.075 ms and 0 ms lag, because each segment is re-anchored.
  - Wall time 3.0 s and 4.0 s. Same VRAM as `split_audio=False`.
- **Drift-free "anchored" mode (`anchored.py`):**
  - How it works:
    - I cut the take myself into pieces of at most 30 s, at the quietest 10 ms frame within ±3 s.
    - Each piece gets 0.5 s of context on each side and is converted with the same `run_infer_script` call.
    - Each output is placed at its exact original offset, with a 5 ms crossfade at the joins.
  - Result: length diff 0.0 ms for both b and c. Lag 0 ms in every 10 s window (±5 ms at 1 ms hop).
  - Cost:
    - b: 4 pieces, 6.8–8.4 s conversion, peak **4363 MiB (+3309)**.
    - c: 11 pieces, 18.6–32.2 s.
    - About 1.3 s overhead per piece.
- **VRAM:** the peak depends on the internal chunk length (about 38–41 s), not the total length. The 300 s file did not use more than the 90 s one. There is about 1.6 GB of headroom on 8 GB. Do not run two conversions at once.
- **RAM:** max RSS 2.8–3.0 GB.
- **Load time:** import plus `VoiceConverter()` takes 7.5 s. The first conversion adds about 5.4 s one-time model load.

## 4. Recommendations
- **Length mismatch:** always pad or trim the converted audio to the exact video duration. Verified:
  ```
  ffmpeg -i video.mp4 -i conv.wav -map 0:v:0 -map 1:a:0 -c:v copy -af "aresample=48000:resampler=soxr,apad=whole_dur=$VD" -c:a aac -b:a 192k -t $VD out.mp4
  ```
  This gives AAC at 48000 Hz with duration 6.000 s, equal to the video. Using `apad` + `-shortest` instead gave 5.994 s, because it rounds to AAC frames.
- **Sample rate:** resample 40 kHz → 48 kHz explicitly. With no resample filter, ffmpeg's aac encoder did not fail. It silently resampled 40 kHz to 44100 Hz (verified).
- **`split_audio`:** keep it **False**.
  - On real mic takes it almost never splits, because the noise floor is within 60 dB of the peak.
  - It drops trailing silence.
  - It only re-anchors timing when there is digital silence.
- **Long takes:**
  - Up to about 40 s there is no drift. `split_audio=False` as the app runs it today is fine.
  - Past that, `split_audio=False` drifts early by up to 10 ms per about 38 s. That was 0–20 ms measured over 5 min, and up to about 70 ms in theory over 5 min. The detectability threshold for audio leading video is about 45 ms (from memory, not measured here).
  - For takes over about 40 s, use the anchored chunking in `anchored.py`. It is verified drift-free, needs no changes to Applio, and also lowers peak VRAM to about 3.3 GB.
- **Not verified here:** any audio/video offset introduced at capture time. Applio reads the WAV from t=0 and WAV has no start_time, so if the recorded MKV/MP4 audio stream starts later than the video, that offset must be carried over when re-muxing (e.g. with `adelay`/`-itsoffset`).