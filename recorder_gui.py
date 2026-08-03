#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
강의 녹음기 GUI — 시작/정지 버튼 버전
- 버튼으로 녹음 시작/정지 (터미널·Ctrl+C 불필요)
- 시작하면 소리 감지로 자동 녹음, 조용해지면 자동 저장
- 개인 학습용으로만 사용하세요.
"""

import os
import queue
import subprocess
import threading
import datetime as dt

import numpy as np
import sounddevice as sd

import tkinter as tk
from tkinter import font as tkfont

import recorder as r  # 녹음 로직 재사용 (save_clip, find_input_device, rms 등)


class RecorderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("강의 녹음기")
        self.root.geometry("440x420")
        self.root.resizable(False, False)

        self.session_on = False
        self.stop_event = threading.Event()
        self.worker = None
        self.ui_q: "queue.Queue" = queue.Queue()
        self.save_dir = r.SAVE_DIR
        self.transcribe_dir = os.path.join(self.save_dir, "전사")
        self.transcribe_var = None  # _build_ui에서 생성

        self._build_ui()
        self._check_output()
        self._poll_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- UI ----------------
    def _build_ui(self):
        big = tkfont.Font(size=22, weight="bold")
        mid = tkfont.Font(size=15, weight="bold")
        small = tkfont.Font(size=11)

        tk.Label(self.root, text="🎧 강의 자동 녹음기", font=mid).pack(pady=(14, 4))

        # 상태 표시 (배경색으로 상태 구분)
        self.status = tk.Label(self.root, text="⚪  대기 중", font=big,
                               bg="#e8e8e8", fg="#333333", width=18, height=2)
        self.status.pack(pady=6, padx=16, fill="x")

        # 시작/정지 버튼
        self.btn = tk.Button(self.root, text="●  녹음 시작", font=mid,
                             height=2, command=self._toggle,
                             highlightbackground="#2e7d32")
        self.btn.pack(pady=8, padx=16, fill="x")

        # 출력 경고/안내
        self.notice = tk.Label(self.root, text="", font=small, fg="#b26a00",
                               wraplength=400, justify="left")
        self.notice.pack(pady=(0, 4), padx=16, fill="x")

        # 녹음 후 자동 전사 (Whisper) 옵션
        self.transcribe_var = tk.BooleanVar(value=True)
        tk.Checkbutton(self.root, text="🖊  녹음 후 자동 전사 (Whisper · 무료·로컬)",
                       variable=self.transcribe_var, font=small).pack(anchor="w", padx=16)

        # 로그
        tk.Label(self.root, text="저장된 녹음", font=small, fg="#666").pack(anchor="w", padx=18)
        self.log = tk.Text(self.root, height=6, font=small, state="disabled",
                           bg="#fafafa", relief="solid", bd=1)
        self.log.pack(pady=4, padx=16, fill="both", expand=True)

        # 폴더 열기
        tk.Button(self.root, text="📁  저장 폴더 열기", font=small,
                  command=self._open_folder).pack(pady=(0, 12))

    def _log(self, msg):
        ts = dt.datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{ts}] {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_status(self, text, bg, fg="#333333"):
        self.status.configure(text=text, bg=bg, fg=fg)

    # ---------------- 출력 라우팅 점검 ----------------
    def _check_output(self):
        try:
            name = sd.query_devices(kind="output")["name"]
        except Exception:
            self.notice.configure(text="")
            return
        if ("다중 출력" in name) or ("Multi-Output" in name) or ("BlackHole" in name):
            self.notice.configure(
                text=f"✅ 출력: '{name}' → 녹음 준비 완료", fg="#2e7d32")
        else:
            self.notice.configure(
                text=(f"⚠️ 현재 출력이 '{name}' 입니다. 강의 소리가 안 담길 수 있어요.\n"
                      "   시스템 설정 → 사운드 → 출력 → '다중 출력 기기'로 바꿔주세요."),
                fg="#c62828")

    # ---------------- 시작/정지 ----------------
    def _toggle(self):
        if not self.session_on:
            self._start()
        else:
            self._stop()

    def _start(self):
        idx, dev = r.find_input_device(r.DEVICE_KEYWORD)
        if idx is None:
            self._set_status("❌  BlackHole 없음", "#ffcdd2", "#b71c1c")
            self._log("BlackHole 입력 장치를 찾지 못했습니다. 설치/설정을 확인하세요.")
            return
        self._check_output()
        self.stop_event.clear()
        self.session_on = True
        self.btn.configure(text="■  녹음 정지", highlightbackground="#c62828")
        self._set_status("🟢  대기 중 (소리 감지)", "#e8f5e9", "#1b5e20")
        self._log(f"녹음기 켜짐 — 입력장치 [{idx}] {dev['name']}")
        self.worker = threading.Thread(target=self._capture_loop, args=(idx,), daemon=True)
        self.worker.start()

    def _stop(self):
        self.stop_event.set()
        self.session_on = False
        self.btn.configure(text="●  녹음 시작", highlightbackground="#2e7d32")
        self._set_status("⚪  대기 중", "#e8e8e8", "#333333")
        self._log("녹음기 꺼짐")

    # ---------------- 캡처 루프 (백그라운드 스레드) ----------------
    def _capture_loop(self, device_index):
        aq: "queue.Queue" = queue.Queue()

        def cb(indata, frames, t, status):
            aq.put(indata.copy())

        silence_blocks = int(r.SILENCE_TIMEOUT / r.BLOCK_SECONDS)
        recording = False
        frames = []
        silent = 0

        try:
            stream = sd.InputStream(
                samplerate=r.SAMPLE_RATE, channels=r.CHANNELS,
                device=device_index, blocksize=int(r.SAMPLE_RATE * r.BLOCK_SECONDS),
                dtype="float32", callback=cb)
        except Exception as e:
            self.ui_q.put(("error", f"오디오 열기 실패: {e}"))
            return

        with stream:
            while not self.stop_event.is_set():
                try:
                    block = aq.get(timeout=0.2)
                except queue.Empty:
                    continue
                level = r.rms(block)
                if not recording:
                    if level >= r.SILENCE_THRESHOLD:
                        recording = True
                        frames = [block]
                        silent = 0
                        self.ui_q.put(("rec_start", None))
                else:
                    frames.append(block)
                    if level < r.SILENCE_THRESHOLD:
                        silent += 1
                        if silent >= silence_blocks:
                            self._save(frames)
                            recording = False
                            frames = []
                            silent = 0
                            self.ui_q.put(("rec_wait", None))
                    else:
                        silent = 0
            # 정지 시 진행 중이던 녹음 저장
            if recording:
                self._save(frames)

    def _save(self, frames):
        try:
            res = r.save_clip(frames, self.save_dir, upload_copy=True)
        except Exception as e:
            self.ui_q.put(("error", f"저장 실패: {e}"))
            return
        if res:
            path, dur, up = res
            self.ui_q.put(("saved", (os.path.basename(path), dur)))
            if self.transcribe_var and self.transcribe_var.get():
                threading.Thread(target=self._transcribe, args=(path,), daemon=True).start()
        else:
            self.ui_q.put(("short", None))

    def _transcribe(self, wav_path):
        name = os.path.basename(wav_path)
        self.ui_q.put(("transcribing", name))
        try:
            import transcribe as tr
            txt, srt = tr.transcribe_file(wav_path, out_dir=self.transcribe_dir)
            self.ui_q.put(("transcribed", os.path.basename(txt)))
        except Exception as e:
            self.ui_q.put(("error", f"전사 실패({name}): {e}"))

    # ---------------- UI 큐 폴링 (메인 스레드) ----------------
    def _poll_ui(self):
        try:
            while True:
                kind, payload = self.ui_q.get_nowait()
                if kind == "rec_start":
                    self._set_status("🔴  녹음 중…", "#ffebee", "#b71c1c")
                elif kind == "rec_wait":
                    self._set_status("🟢  대기 중 (소리 감지)", "#e8f5e9", "#1b5e20")
                elif kind == "saved":
                    name, dur = payload
                    self._log(f"■ 저장 완료: {name} ({dur:.1f}초)  → 업로드용")
                elif kind == "transcribing":
                    self._log(f"🖊 전사 중… ({payload})")
                elif kind == "transcribed":
                    self._log(f"✅ 전사 완료: {payload}  (전사 폴더)")
                elif kind == "short":
                    self._log("… 너무 짧아 저장 안 함")
                elif kind == "error":
                    self._set_status("❌  오류", "#ffcdd2", "#b71c1c")
                    self._log(payload)
                    self.session_on = False
                    self.btn.configure(text="●  녹음 시작", highlightbackground="#2e7d32")
        except queue.Empty:
            pass
        self.root.after(120, self._poll_ui)

    def _open_folder(self):
        os.makedirs(self.save_dir, exist_ok=True)
        subprocess.run(["open", self.save_dir])

    def _on_close(self):
        self.stop_event.set()
        self.root.after(200, self.root.destroy)


def main():
    root = tk.Tk()
    RecorderGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
