#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""녹음 파이프라인 최종 점검: BlackHole에서 6초 캡처하며 테스트 음성 재생 후 RMS 확인."""
import os, sys, time, threading, subprocess
import numpy as np
import sounddevice as sd
import recorder as r

idx, dev = r.find_input_device(r.DEVICE_KEYWORD)
if idx is None:
    print("❌ BlackHole 입력 장치를 못 찾음"); sys.exit(1)
print(f"입력 장치: [{idx}] {dev['name']}")

DUR = 6.0
frames = []

def speak_later():
    time.sleep(0.8)
    # 시스템 기본 출력(=다중 출력 기기 → BlackHole+스피커)으로 TTS 재생
    subprocess.run(["say", "-r", "180",
                    "안녕하세요. 강의 자동 녹음 테스트입니다. 하나 둘 셋 넷 다섯."])

t = threading.Thread(target=speak_later, daemon=True)
t.start()

print("● 6초 녹음 시작 (테스트 음성 재생 중)...")
rec = sd.rec(int(DUR * r.SAMPLE_RATE), samplerate=r.SAMPLE_RATE,
             channels=r.CHANNELS, dtype="float32", device=idx)
sd.wait()
t.join(timeout=1)

# 블록 리스트로 변환해 recorder의 저장 로직 재사용 → 실제 산출물 생성
block = int(r.SAMPLE_RATE * r.BLOCK_SECONDS)
frames = [rec[i:i+block] for i in range(0, len(rec), block)]

overall_rms = float(np.sqrt(np.mean(rec.astype(np.float64) ** 2)))
peak = float(np.max(np.abs(rec)))
print(f"\n측정: 전체 RMS = {overall_rms:.5f},  피크 = {peak:.4f}")

if peak < 0.001:
    print("⚠️ 소리가 거의 안 잡혔습니다(무음). 출력이 '다중 출력 기기'인지, BlackHole 체크됐는지 확인 필요.")
    sys.exit(2)

# 실제 저장 (원본 + 업로드용 사본)
res = r.save_clip(frames, r.SAVE_DIR, upload_copy=True)
if res:
    path, dur, up = res
    print(f"\n✅ 캡처 성공! 소리가 정상적으로 녹음됩니다.")
    print(f"   원본:     {path}  ({os.path.getsize(path)/1024:.0f}KB, {dur:.1f}s)")
    if up:
        print(f"   업로드용: {up}  ({os.path.getsize(up)/1024:.0f}KB)")
else:
    print("저장 로직 반환 없음(길이 부족?)")
