#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
강의 자동 녹음기 (Auto Lecture Recorder)
- macOS 시스템 소리를 BlackHole(가상 오디오)로 받아 WAV로 저장합니다.
- 개인 학습/복습용으로만 사용하세요. 녹음물의 공유/재배포는 저작권 침해가 될 수 있습니다.

모드:
  auto        소리가 나면 자동으로 녹음 시작, 일정 시간 조용해지면 자동 저장 (기본값)
  continuous  Ctrl+C 로 멈출 때까지 통째로 하나의 파일에 녹음
  --list      오디오 장치 목록만 출력하고 종료
"""

import argparse
import datetime as dt
import os
import queue
import sys
import time

import numpy as np
import sounddevice as sd
import soundfile as sf
from scipy.signal import resample_poly

# ----------------------------- 설정(기본값) -----------------------------
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_ROOT = os.path.dirname(PROJECT_DIR)     # 저장소의 상위 작업 폴더
SAVE_DIR = os.path.join(OUTPUT_ROOT, "1) 녹음")
SAMPLE_RATE = 48000                           # 샘플레이트 (BlackHole 기본 48kHz)
CHANNELS = 2                                  # 스테레오
BLOCK_SECONDS = 0.25                          # 오디오를 읽는 블록 길이(초)
SILENCE_THRESHOLD = 0.008                     # 이 RMS 이하면 '조용함'으로 간주 (0~1)
SILENCE_TIMEOUT = 600.0                       # 10분간 계속 조용하면 녹음 종료·저장
MIN_CLIP_SECONDS = 2.0                        # 이보다 짧은 녹음은 잡음으로 보고 버림
DEVICE_KEYWORD = "BlackHole"                  # 입력 장치 찾을 때 쓰는 이름 키워드

# 업로드용 소용량 사본 (Tiro/Plaud 업로드에 적합, 용량 ~1/10)
MAKE_UPLOAD_COPY = True                        # True면 16kHz 모노 사본을 함께 저장
UPLOAD_SAMPLE_RATE = 16000                     # 말소리 전사에 표준인 16kHz
UPLOAD_SUBDIR = "업로드용"                      # 원본 폴더 아래 이 하위폴더에 저장
SAVE_ORIGINAL = False                          # 원본 없이 16kHz 모노 업로드용 파일만 생성
# ----------------------------------------------------------------------


def find_input_device(keyword: str):
    """이름에 keyword가 들어간 '입력' 오디오 장치의 인덱스를 찾는다."""
    for idx, dev in enumerate(sd.query_devices()):
        if keyword.lower() in dev["name"].lower() and dev["max_input_channels"] > 0:
            return idx, dev
    return None, None


def list_devices():
    print("=== 오디오 장치 목록 ===")
    for idx, dev in enumerate(sd.query_devices()):
        io = []
        if dev["max_input_channels"] > 0:
            io.append(f"입력{dev['max_input_channels']}")
        if dev["max_output_channels"] > 0:
            io.append(f"출력{dev['max_output_channels']}")
        print(f"  [{idx}] {dev['name']}  ({', '.join(io)})")
    print()
    idx, dev = find_input_device(DEVICE_KEYWORD)
    if dev:
        print(f"➡  녹음에 사용할 장치: [{idx}] {dev['name']}")
    else:
        print(f"⚠  '{DEVICE_KEYWORD}' 입력 장치를 찾지 못했습니다. BlackHole 설치/설정을 확인하세요.")


def check_output_routing():
    """현재 시스템 출력이 녹음 가능한 장치(다중 출력 기기/BlackHole)인지 점검하고 경고."""
    try:
        name = sd.query_devices(kind="output")["name"]
    except Exception:
        return
    if ("다중 출력" in name) or ("Multi-Output" in name) or ("BlackHole" in name):
        print(f"✅ 출력 라우팅 OK: 현재 출력 = '{name}' → 녹음됩니다.\n")
    else:
        print("⚠️  주의: 현재 시스템 출력이 '" + name + "' 입니다.")
        print("   이대로 녹음하면 강의 소리가 안 담겨 '무음'이 될 수 있습니다!")
        print("   → 시스템 설정 → 사운드 → 출력 → '다중 출력 기기' 로 바꿔주세요.")
        print("   (바꾼 뒤 이 창은 그대로 두고 강의를 재생하면 됩니다)\n")


def timestamp_name(prefix="강의"):
    now = dt.datetime.now()
    return f"{prefix}_{now:%Y-%m-%d_%H-%M-%S}.wav"


def rms(block: np.ndarray) -> float:
    """오디오 블록의 볼륨(RMS)을 0~1 범위로 반환."""
    if block.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))


def open_stream(device_index):
    q: "queue.Queue[np.ndarray]" = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            print(f"  (오디오 상태: {status})", file=sys.stderr)
        q.put(indata.copy())

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        device=device_index,
        blocksize=int(SAMPLE_RATE * BLOCK_SECONDS),
        dtype="float32",
        callback=callback,
    )
    return stream, q


def to_upload_audio(data: np.ndarray) -> np.ndarray:
    """스테레오/48kHz 데이터를 모노/16kHz로 변환 (전사 업로드용, 용량 절감)."""
    # 1) 스테레오 → 모노 (채널 평균)
    mono = data.mean(axis=1) if data.ndim == 2 else data
    # 2) 리샘플 SAMPLE_RATE → UPLOAD_SAMPLE_RATE (폴리페이즈, 안티에일리어싱 포함)
    if SAMPLE_RATE != UPLOAD_SAMPLE_RATE:
        mono = resample_poly(mono, UPLOAD_SAMPLE_RATE, SAMPLE_RATE)
    return mono.astype(np.float32)


def write_upload_copy(data: np.ndarray, save_dir: str, base_name: str, enabled: bool):
    """업로드용 16kHz 모노 WAV 사본을 하위폴더에 저장하고 경로를 반환."""
    if not enabled:
        return None
    up_dir = os.path.join(save_dir, UPLOAD_SUBDIR)
    os.makedirs(up_dir, exist_ok=True)
    up_path = os.path.join(up_dir, base_name)
    sf.write(up_path, to_upload_audio(data), UPLOAD_SAMPLE_RATE, subtype="PCM_16")
    return up_path


def save_clip(frames_list, save_dir, upload_copy=True):
    if not frames_list:
        return None
    data = np.concatenate(frames_list, axis=0)
    duration = len(data) / SAMPLE_RATE
    if duration < MIN_CLIP_SECONDS:
        return None  # 너무 짧으면 저장 안 함
    base_name = timestamp_name()

    if not SAVE_ORIGINAL:
        # 날짜 폴더에 원본 없이 16kHz 모노 파일 한 개만 생성
        os.makedirs(save_dir, exist_ok=True)
        only_path = os.path.join(save_dir, base_name)
        sf.write(only_path, to_upload_audio(data), UPLOAD_SAMPLE_RATE, subtype="PCM_16")
        return only_path, duration, None

    os.makedirs(save_dir, exist_ok=True)
    path = os.path.join(save_dir, base_name)
    sf.write(path, data, SAMPLE_RATE, subtype="PCM_16")
    up_path = write_upload_copy(data, save_dir, base_name, upload_copy)
    return path, duration, up_path


def run_auto(device_index, save_dir, upload_copy=True):
    stream, q = open_stream(device_index)
    silence_blocks_needed = int(SILENCE_TIMEOUT / BLOCK_SECONDS)

    recording = False
    frames = []
    silent_count = 0

    print("🎧 자동 녹음 대기 중… 소리가 감지되면 자동으로 녹음을 시작합니다.")
    print("   (종료: Ctrl+C)\n")
    with stream:
        try:
            while True:
                block = q.get()
                level = rms(block)

                if not recording:
                    if level >= SILENCE_THRESHOLD:
                        recording = True
                        frames = [block]
                        silent_count = 0
                        print(f"● 녹음 시작  ({dt.datetime.now():%H:%M:%S})")
                else:
                    frames.append(block)
                    if level < SILENCE_THRESHOLD:
                        silent_count += 1
                        if silent_count >= silence_blocks_needed:
                            result = save_clip(frames, save_dir, upload_copy)
                            if result:
                                path, dur, up_path = result
                                print(f"■ 저장 완료  {os.path.basename(path)}  ({dur:.1f}초)")
                                if up_path:
                                    print(f"   ↳ 업로드용 사본  {UPLOAD_SUBDIR}/{os.path.basename(up_path)}")
                                print()
                            else:
                                print("… 너무 짧아 저장하지 않음\n")
                            recording = False
                            frames = []
                            silent_count = 0
                    else:
                        silent_count = 0
        except KeyboardInterrupt:
            print("\n\n종료 요청 받음.")
            if recording:
                result = save_clip(frames, save_dir, upload_copy)
                if result:
                    path, dur, up_path = result
                    print(f"■ 마지막 녹음 저장  {os.path.basename(path)}  ({dur:.1f}초)")
                    if up_path:
                        print(f"   ↳ 업로드용 사본  {UPLOAD_SUBDIR}/{os.path.basename(up_path)}")


def run_continuous(device_index, save_dir, upload_copy=True):
    os.makedirs(save_dir, exist_ok=True)
    base_name = timestamp_name()
    path = os.path.join(save_dir, base_name)
    stream, q = open_stream(device_index)
    print(f"● 연속 녹음 시작 → {os.path.basename(path)}")
    print("   (종료: Ctrl+C)\n")
    frames = 0
    with sf.SoundFile(path, mode="w", samplerate=SAMPLE_RATE,
                      channels=CHANNELS, subtype="PCM_16") as f:
        with stream:
            start = time.time()
            try:
                while True:
                    block = q.get()
                    f.write(block)
                    frames += len(block)
                    elapsed = time.time() - start
                    print(f"\r  녹음 중… {elapsed:6.1f}초", end="", flush=True)
            except KeyboardInterrupt:
                pass
    print(f"\n■ 저장 완료  {path}")

    # 업로드용 사본: 큰 파일을 메모리에 다 올리지 않도록 저장된 원본을 다시 읽어 변환
    if upload_copy:
        data, sr = sf.read(path, dtype="float32", always_2d=True)
        up_path = write_upload_copy(data, save_dir, base_name, True)
        if up_path:
            print(f"   ↳ 업로드용 사본  {UPLOAD_SUBDIR}/{os.path.basename(up_path)}")


def main():
    parser = argparse.ArgumentParser(description="강의 자동 녹음기 (개인 학습용)")
    parser.add_argument("mode", nargs="?", default="auto",
                        choices=["auto", "continuous"],
                        help="auto=소리 감지 자동녹음(기본), continuous=Ctrl+C까지 통째 녹음")
    parser.add_argument("--list", action="store_true", help="오디오 장치 목록만 출력")
    parser.add_argument("--dir", default=SAVE_DIR, help=f"저장 폴더 (기본: {SAVE_DIR})")
    parser.add_argument("--device", type=int, default=None,
                        help="입력 장치 인덱스 직접 지정 (--list 로 확인)")
    parser.add_argument("--no-upload-copy", action="store_true",
                        help="업로드용 16kHz 모노 소용량 사본을 만들지 않음")
    args = parser.parse_args()

    upload_copy = MAKE_UPLOAD_COPY and not args.no_upload_copy

    if args.list:
        list_devices()
        return

    if args.device is not None:
        device_index = args.device
    else:
        device_index, dev = find_input_device(DEVICE_KEYWORD)
        if device_index is None:
            print(f"⚠  '{DEVICE_KEYWORD}' 입력 장치를 찾지 못했습니다.")
            print("   1) BlackHole 이 설치되어 있는지 확인하세요.")
            print("   2) '오디오 MIDI 설정'에서 다중 출력 장치를 만들었는지 확인하세요.")
            print("   3) python recorder.py --list 로 장치 목록을 확인하고 --device 로 지정할 수 있습니다.")
            sys.exit(1)
        print(f"입력 장치: [{device_index}] {dev['name']}")

    print(f"저장 폴더: {args.dir}")
    if upload_copy:
        print(f"업로드용 사본: {os.path.join(args.dir, UPLOAD_SUBDIR)} (16kHz 모노)")
    print()

    check_output_routing()

    if args.mode == "continuous":
        run_continuous(device_index, args.dir, upload_copy)
    else:
        run_auto(device_index, args.dir, upload_copy)


if __name__ == "__main__":
    main()
