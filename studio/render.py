"""Render final: MKV da tomada + WAV convertido + marca d'agua -> MP4 verificado em videos_finais/."""

from studio.config import Modelo, metadata_tags

FPS = 30
OUT_SR = 48000
SAMPLES_PER_FRAME = OUT_SR // FPS           # 1600
TIMEOUT_FACTOR = 5                          # 5x a duracao da tomada + 60 s
TIMEOUT_BASE_S = 60.0
ENCODERS = ("nvenc", "x264")                # ordem de tentativa
ENCODER_FLAGS = {
    "nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "21", "-b:v", "0",
              "-maxrate", "6M", "-bufsize", "12M", "-profile:v", "high"],
    "x264": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-maxrate", "6M", "-bufsize", "12M",
             "-profile:v", "high"],
}


class RenderError(Exception):
    pass


def offset_filter(av_offset_ms: int, sr: int = OUT_SR) -> str:
    # positivo = audio mais tarde; conta amostras (vale depois do aresample)
    n = round(abs(av_offset_ms) * sr / 1000)
    if n == 0:
        return ""
    if av_offset_ms > 0:
        return f",adelay={n}S:all=1"
    return f",atrim=start_sample={n}"


def build_filter(n_frames: int, av_offset_ms: int = 0) -> str:
    # video comeca no 2o frame (ancora da timeline); audio com exatamente n_frames*1600 amostras
    # (apad whole_len + atrim end_sample; NUNCA apad + -shortest: trava no ffmpeg 6.1.1)
    if n_frames < 1:
        raise ValueError("O vídeo precisa de pelo menos 1 frame")
    s = n_frames * SAMPLES_PER_FRAME
    return (f"[0:v]trim=start_frame=1,setpts=PTS-STARTPTS,fps={FPS},tpad=stop_mode=clone:stop=2,"
            f"trim=end_frame={n_frames},setpts=PTS-STARTPTS,format=yuv420p[v0];"
            "[2:v]format=yuva420p[wm];[v0][wm]overlay=0:0:format=yuv420,format=yuv420p[v];"
            f"[1:a]aresample={OUT_SR}:resampler=soxr,asetpts=N/SR/TB{offset_filter(av_offset_ms)},"
            f"apad=whole_len={s},atrim=end_sample={s},asetpts=N/SR/TB[a]")


def build_render_cmd(raw_mkv: str, conv_wav: str, wm_png: str, out_part: str, n_frames: int, modelo: Modelo,
                     av_offset_ms: int = 0, encoder: str = "nvenc") -> list[str]:
    # sem o prefixo setpriv (o procs.spawn poe); ValueError se o modelo nao tem aviso
    if encoder not in ENCODER_FLAGS:
        raise ValueError(f"Encoder desconhecido: {encoder}")
    meta = []
    for key, value in metadata_tags(modelo).items():
        meta += ["-metadata", f"{key}={value}"]
    return ["nice", "-n", "10",
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", raw_mkv, "-i", conv_wav, "-i", wm_png,
            "-filter_complex", build_filter(n_frames, av_offset_ms),
            "-map", "[v]", "-map", "[a]", *ENCODER_FLAGS[encoder], "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-colorspace", "smpte170m", "-color_primaries", "smpte170m", "-color_trc", "smpte170m",
            "-color_range", "tv",
            "-c:a", "aac", "-b:a", "128k", "-ar", str(OUT_SR), "-ac", "1", "-movflags", "+faststart",
            *meta, "-f", "mp4", out_part]


def render_timeout(n_frames: int) -> float:
    return TIMEOUT_FACTOR * n_frames / FPS + TIMEOUT_BASE_S
