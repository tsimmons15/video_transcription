from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FfmpegAudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    codec: str = "pcm_s16le"  # 16-bit PCM WAV
    overwrite: bool = True
    normalize: str = "highpass=f=80,lowpass=f=8000,loudnorm=I=-16:LRA=11:TP=-1.5" # Preset parameters


class FfmpegNotFoundError(RuntimeError):
    pass


class FfmpegDecodeError(RuntimeError):
    pass


def require_ffmpeg(ffmpeg_path: str | None = None) -> str:
    exe = ffmpeg_path or shutil.which("ffmpeg")
    if not exe:
        raise FfmpegNotFoundError(
            "ffmpeg not found. Install it and ensure it's on PATH. "
            "Windows: `winget install Gyan.FFmpeg` then restart your terminal."
        )
    return exe


def extract_wav(
    input_media: Path,
    output_wav: Path,
    *,
    config: FfmpegAudioConfig = FfmpegAudioConfig(),
    ffmpeg_path: str | None = None,
) -> Path:
    if not input_media.exists() or not input_media.is_file():
        raise FileNotFoundError(f"Input media not found: {input_media}")

    output_wav.parent.mkdir(parents=True, exist_ok=True)
    exe = require_ffmpeg(ffmpeg_path)

    cmd = [
        exe,
        "-hide_banner",
        "-loglevel", "error",
    ]

    if config.overwrite:
        cmd.append("-y")

    cmd += [
        "-i", str(input_media),
        "-vn",  # no video
        "-ac", str(config.channels),
        "-ar", str(config.sample_rate),
        "-af", str(config.normalize),
        "-c:a", config.codec,
        str(output_wav),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        stdout = (e.stdout or "").strip()
        msg = "ffmpeg failed to decode media."
        if stderr:
            msg += f"\nffmpeg stderr:\n{stderr}"
        if stdout:
            msg += f"\nffmpeg stdout:\n{stdout}"
        raise FfmpegDecodeError(msg) from e

    if not output_wav.exists():
        raise FfmpegDecodeError(f"ffmpeg reported success but output missing: {output_wav}")

    return output_wav