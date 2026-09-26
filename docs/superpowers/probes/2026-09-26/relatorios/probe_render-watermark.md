Everything in the probe ran and passed. The final pipeline keeps audio and video in sync to within 0.08 ms in every case I tested, and the limit was 33 ms. Nothing under `/home/peras/orochi-ia-homenagem` was changed, nothing was installed, and `~/.config` was not touched. Scratch dir: `/tmp/claude-1000/-home-peras-gitperaro-thiago-knowledge/ed300675-58f2-4b29-8f05-0e27e4d61468/scratchpad/probes/render-watermark-av/`

## 1. Fonts (checked by running fontTools 4.65.0 from the Applio venv, script `fontcheck.py`)

| Font | Missing from "ãéóçÓÃÉÇõêâí·●◉•" |
|---|---|
| DejaVuSans-Bold.ttf / DejaVuSans.ttf (`/usr/share/fonts/truetype/dejavu/`) | nothing |
| NotoSans-Bold / Regular | ● ◉ |
| LiberationSans-Bold / Regular | ◉ |
| Ubuntu-B / Ubuntu-R | ● ◉ |

`fc-match "sans:bold"` resolves to Noto Sans Bold. So a drawtext `font=Sans` with "●" shows an empty box instead of the dot. Use the DejaVu font files by path. With the PIL approach the dot is drawn as a circle, so no dot character is needed.

## 2. Watermark: pick approach A (PIL-generated PNG plus the ffmpeg overlay filter)

Frames I rendered and looked at (kept in the scratch dir):
- `frameA_1280x720.png`
- `frameA_640x480.png`
- `frameB_1280x720.png` (the drawtext version)
- `final_mp4_frame_t1.png` (a frame taken from the final nvenc MP4)

All accents render correctly and the rounded "● IA" badge sits top-right. At 640x480 the band font sizes are 20 and 16 px and still fit.

Why A:
- **Badge shape:** drawtext/drawbox can't round corners, so B's badge is a plain rectangle.
- **Layout:** in B the dot and text need positions computed in Python from font measurements anyway, which means 6 chained filters with hand-set coordinates.
- **Escaping and fonts:** B has text-escaping risks and depends on fontconfig choosing the right font.
- **Band look:** B's `drawbox black@0.55` washes the band out to grey, while A keeps the colours underneath and just darkens them.
- **Other video sizes:** A's text shrinks automatically for any size.

A stays on screen the whole video. I measured the pixel brightness at frames 0, 150 and 299 of the output:

| Region | Source | Output |
|---|---|---|
| Band background | 76 | 33 |
| Badge area | 179 | 103 |
| Untouched area | 143.7 | 143.2 |

A single-frame PNG input stays on screen to the last frame (overlay's default behaviour). No `-loop 1` is needed.

PIL code (`watermark.py`, runs with the Applio venv, which has PIL 12.3). Usage is `watermark.py W H out.png`:
```python
FONT_BOLD="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"; FONT_REG="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
LINE1="VOZ GERADA POR IA \u00b7 PAR\u00d3DIA/HOMENAGEM"; LINE2="N\u00e3o \u00e9 a voz real de Silvio Santos"
def _fit_font(path,size,text,max_w):
    while size>8:
        f=ImageFont.truetype(path,size); l,t,r,b=f.getbbox(text)
        if r-l<=max_w: return f
        size-=1
    return ImageFont.truetype(path,size)
def make_watermark(w,h,out_path,line1=LINE1,line2=LINE2,badge="IA"):
    img=Image.new("RGBA",(w,h),(0,0,0,0)); d=ImageDraw.Draw(img); margin=max(8,round(0.03*h))
    bfont=ImageFont.truetype(FONT_BOLD,max(12,round(0.05*h))); l,t,r,b=bfont.getbbox(badge)
    text_w,text_h=r-l,b-t; dot_d=round(text_h*.85); pad_x=round(text_h*.55); pad_y=round(text_h*.45); gap=round(text_h*.40)
    box_w=pad_x+dot_d+gap+text_w+pad_x; box_h=pad_y+text_h+pad_y; x1=w-margin; x0=x1-box_w; y0=margin; y1=y0+box_h
    d.rounded_rectangle((x0,y0,x1,y1),radius=box_h//2,fill=(0,0,0,165),outline=(255,255,255,200),width=max(1,round(h/360)))
    cy=(y0+y1)/2; dx0=x0+pad_x
    d.ellipse((dx0,cy-dot_d/2,dx0+dot_d,cy+dot_d/2),fill=(230,30,40,255))
    d.text((dx0+dot_d+gap-l,y0+pad_y-t),badge,font=bfont,fill=(255,255,255,255))
    max_text_w=w-2*margin
    f1=_fit_font(FONT_BOLD,max(10,round(0.042*h)),line1,max_text_w); f2=_fit_font(FONT_REG,max(9,round(0.034*h)),line2,max_text_w)
    b1=f1.getbbox(line1); b2=f2.getbbox(line2); h1,h2=b1[3]-b1[1],b2[3]-b2[1]
    line_gap=round(.35*h1); vpad=round(.022*h); band_h=vpad+h1+line_gap+h2+vpad; band_y0=h-band_h
    d.rectangle((0,band_y0,w,h),fill=(0,0,0,140))   # 55% black
    y=band_y0+vpad
    for text,f,bb,hh,col in ((line1,f1,b1,h1,(255,255,255,255)),(line2,f2,b2,h2,(235,235,235,255))):
        d.text(((w-(bb[2]-bb[0]))/2-bb[0],y-bb[1]),text,font=f,fill=col); y+=hh+line_gap
    img.save(out_path,optimize=True)
```
At 1280x720 this gives a 91 px band, fonts of 30 and 24 px, and a badge box at (1157,22)-(1258,72).

## 3. Alignment (all run and measured)

**Synthetic takes** (`mktake.sh`): MJPEG yuvj422p 720p30 with a white flash on frame 90, plus mono 48 kHz PCM with a 1 kHz, 50 ms beep. They're built by muxing with `-itsoffset` on each input and `-output_ts_offset`. For example: `ffmpeg -itsoffset 0 -i v.mkv -itsoffset 0.4 -i a.wav -map 0:v -map 1:a -c copy -output_ts_offset 0 take.mkv`.

ffprobe start times (video / audio):

| Take | Video start | Audio start |
|---|---|---|
| audio 0.40 s late | 0.000 | 0.400 |
| audio 0.25 s early | 0.250 | 0.000 |
| audio late, whole file shifted +5 s | 5.000 | 5.400 |

In all three the beep and the flash land on the same absolute instant (offset 0.08 ms).

**`extract_aligned_audio`** (`avalign.py`, standard library only): read `ffprobe -show_streams -show_format -of json`, compute `delta = a_start - v_start`, and `n = round(|delta| * sr)`.
- **Audio starts later:** `["ffmpeg","-hide_banner","-loglevel","error","-y","-i",MKV,"-map","0:a:0","-af","asetpts=N/SR/TB,adelay=delays=19200S:all=1,asetpts=N/SR/TB","-ac","1","-c:a","pcm_s16le",WAV]`
- **Audio starts earlier:** the same command with `-af asetpts=N/SR/TB,atrim=start_sample=12000,asetpts=N/SR/TB`

Result: the beep lands at sample 144004 (3.00008 s) in all three cases, and each WAV is exactly 10.000 s.

For comparison, a plain `ffmpeg -i take.mkv -map 0:a out.wav` puts the beep at 2.60008 s (late case) or 3.25008 s (early case).

**`render`**: nvenc first, and libx264 (`-c:v libx264 -preset veryfast -crf 20 -profile:v high` in place of the nvenc flags) if it fails. The fallback works: with `CUDA_VISIBLE_DEVICES=` set, nvenc fails with `cuInit ... CUDA_ERROR_NO_DEVICE` and the render finishes with libx264. Exact argv:
```
["ffmpeg","-hide_banner","-loglevel","error","-y","-i","take.mkv","-i","converted.wav","-i","wm_1280x720.png",
 "-filter_complex","[0:v]setpts=PTS-STARTPTS,fps=30,tpad=stop_mode=clone:stop=2,trim=end_frame=300,setpts=PTS-STARTPTS,format=yuv420p[v0];[2:v]format=yuva420p[wm];[v0][wm]overlay=0:0:format=yuv420,format=yuv420p[v];[1:a]aresample=48000,asetpts=N/SR/TB,apad=whole_len=480000,atrim=end_sample=480000,asetpts=N/SR/TB[a]",
 "-map","[v]","-map","[a]","-c:v","h264_nvenc","-preset","p5","-tune","hq","-rc","vbr","-cq","21","-b:v","0","-profile:v","high",
 "-pix_fmt","yuv420p","-r","30","-colorspace","smpte170m","-color_primaries","smpte170m","-color_trc","smpte170m","-color_range","tv",
 "-c:a","aac","-b:a","192k","-ar","48000","-ac","1","-movflags","+faststart",
 "-metadata","title=Paródia/homenagem - voz gerada por IA",
 "-metadata","comment=Voz sintética gerada por IA (conversão RVC). Não é a voz real de Silvio Santos.",
 "-metadata","description=AI-generated/synthetic voice (RVC voice conversion). Parody/tribute. Not the real voice of Silvio Santos.","out.mp4"]
```
How the numbers in the filter are computed:
- `n_frames = round((last_video_pts + last_pkt_dur - first_pts) * 30)`, taken from ffprobe packet timestamps without decoding.
- `n_samples = n_frames * 1600`.

Checks on the output:
- ffprobe shows the title, comment and description tags with accents intact.
- The file layout is `ftyp, moov, free, mdat`, so `+faststart` worked.

**Measured beep minus flash in the output MP4** (flash is frame 90, t = 3.000):

| Case | Offset |
|---|---|
| Audio 0.40 s late | +0.08 ms |
| Audio 0.25 s early | +0.08 ms |
| Late case with +5 s shift | +0.08 ms |
| libx264 | +0.08 ms |
| BT.709 option | +0.08 ms |
| Variable-frame-rate take (mostly 15 fps with a 30 fps burst, audio 0.4 s late, shifted +2 s; 160 packets became 299 frames) | +0.08 ms |
| Control: no alignment, audio late | −399.92 ms |
| Control: no alignment, audio early | +250.08 ms |

## 4. Converted WAV at a different rate and length

These were run on the late case, and one on the early case:

| Converted WAV | Offset | Video / audio stream duration |
|---|---|---|
| 40 kHz, 60 ms short | 0.08 ms | 10.000000 / 10.000000 |
| 40 kHz, 40 ms long | 0.08 ms | 10.000000 / 10.000000 |
| 32 kHz, 20 ms short | 0.08 ms | 10.000000 / 10.000000 |
| 32 kHz, 60 ms long | 0.08 ms | 10.000000 / 10.000000 |
| Early case, 40 kHz, 60 ms short | 0.06 ms | 10.000000 / 10.000000 |

All outputs have 300 video frames. Decoding the AAC gives 10.00533 s, because the encoder pads the last frame by 256 samples; the reported stream durations still match.

## 5. Encode speed, 60 s of 720p30

The test source was MJPEG q3 with heavy synthetic sensor noise (`noise=alls=12:allf=t+u`), 343 MB. Machine is an idle Ryzen 5 5600 with the RTX 3060 Ti; three runs each, full pipeline including decode, overlay and encode.

| Encoder | Time per run | File size |
|---|---|---|
| h264_nvenc p5, cq 21 | 9.50 / 9.46 / 9.70 s (about 6.3x real time) | 63 MB (8.3 Mbps) |
| libx264 veryfast, crf 20 | 11.44 / 11.81 / 11.47 s (about 5.2x real time) | 127 MB (16.7 Mbps) |

Where the time goes:
- **MJPEG decode alone:** 4.7 s (4.3 s with `mjpeg_cuvid`, not worth adding).
- **Encoding alone:** nvenc 3.4 s, x264 6.5 s.
- **ffmpeg 6.1:** it runs these stages mostly one after another, which is why the encoders end up close.
- **Timing noise:** an earlier batch took 16 to 22 s while other probes were loading the machine.

## Pitfalls
1. **`-shortest` hangs:** infinite `apad` plus `-shortest` inside `-filter_complex` never finishes on ffmpeg 6.1.1. I reproduced it twice: a 10 s clip was killed after 20 s and 25 s (the first run's file reached 41 MB), against 1.4 s normally. Moving `apad` to `-af` does end, but the audio comes out 16 ms short (9.984 s). Use `apad=whole_len` plus `atrim=end_sample` as above, and keep the subprocess timeout that `render()` now has.
2. **MKV `DURATION` tag:** it holds the stream's end timestamp, not its length. The early-audio take's video shows `DURATION=10.25` for 10.0 s of video. Get the length from packet timestamps instead.
3. **Colour tags:** the webcam MJPEG is `yuvj422p` with `color_range=pc` and `bt470bg`. Without tags the MP4 is marked unknown/unknown, and players that assume BT.709 for HD shift the colours. Tagging it as smpte170m/tv costs nothing. Converting to BT.709 (`color="709"` in `avalign.py`) adds about 2 s per minute.
4. **Filter path changes file size:** the direct `format=yuv420p` path keeps the noise faithfully (brightness spread 2.413 against the expected 2.432). Going through `overlay format=auto` or the BT.709 scale path smooths it (1.48 to 1.55) and gives files about 2x smaller. To save space, raise `-cq` to 23 or add `hqdn3d`. I didn't test either.
5. **Overlay format:** convert with `format=yuv420p` before the overlay and set `overlay=format=yuv420`. `format=auto` with `yuvj422p` input was slower: 7.08 s against 5.83 s for the filters alone.
6. **Mono AAC bitrate:** asking for 192k mono produced 138 kbps on the test signal. 128k is enough.
7. **Not tested:**
   - **Metadata:** it will likely be stripped when WhatsApp or Instagram re-encode the video, so the burned-in watermark is the protection that actually holds.
   - **Real RVC offset:** my converted WAVs were simulated, so a real RVC run hasn't been checked for a constant delay. Cross-correlating the aligned input WAV with the converted output would detect one.
8. **Killed processes:** during step 4 I stopped only my own runaway ffmpeg processes (the `-shortest` renders). Another probe's ffmpeg reading `/dev/video0` was left alone.

Files are in the scratch dir above:
- `watermark.py`, `avalign.py`, `measure.py`, `mktake.sh`, `onset.py`, `fontcheck.py`
- `wm_1280x720.png`, `wm_640x480.png`
- the four frame PNGs listed in section 2
- `take_*.mkv` and `out_*.mp4` (the 10 s test clips)

The large 60 s test files were deleted.