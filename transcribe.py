#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
로컬 Whisper 전사 (Apple Silicon MLX) — 무료·오프라인.
WAV 파일을 받아 .txt(전문)와 .srt(자막)를 생성한다. ffmpeg 불필요(soundfile로 로드).
"""
import os
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

# large-v3-turbo: 빠르고 정확 (한국어 우수). 더 정확하게 원하면 whisper-large-v3-mlx.
MODEL = "mlx-community/whisper-large-v3-turbo"
LANGUAGE = "ko"
MAKE_SRT = False  # True면 자막(.srt)도 생성. 기본은 .txt만.


def _load_16k_mono(path):
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if sr != 16000:
        mono = resample_poly(mono, 16000, sr)
    return np.ascontiguousarray(mono, dtype=np.float32)


def _fmt_ts(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcribe_file(wav_path, out_dir=None, model=MODEL, language=LANGUAGE):
    """wav_path를 전사해 .txt/.srt를 out_dir에 저장하고 (txt_path, srt_path) 반환."""
    import mlx_whisper  # 지연 임포트 (앱 시작 속도 확보)
    audio = _load_16k_mono(wav_path)
    res = mlx_whisper.transcribe(audio, path_or_hf_repo=model, language=language)

    base = os.path.splitext(os.path.basename(wav_path))[0]
    out_dir = out_dir or os.path.dirname(wav_path)
    os.makedirs(out_dir, exist_ok=True)
    txt_path = os.path.join(out_dir, base + ".txt")
    srt_path = os.path.join(out_dir, base + ".srt")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(res["text"].strip() + "\n")

    if MAKE_SRT:
        segs = res.get("segments", [])
        with open(srt_path, "w", encoding="utf-8") as f:
            for i, seg in enumerate(segs, 1):
                f.write(f"{i}\n{_fmt_ts(seg['start'])} --> {_fmt_ts(seg['end'])}\n"
                        f"{seg['text'].strip()}\n\n")
        return txt_path, srt_path
    return txt_path, None


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("사용법: python transcribe.py <오디오파일.wav>")
        sys.exit(1)
    t, s = transcribe_file(sys.argv[1])
    print("저장:", t)
    print("저장:", s)
