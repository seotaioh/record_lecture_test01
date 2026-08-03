#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
강의 녹음기 GUI — 시작/정지 버튼 버전
- 버튼으로 녹음 시작/정지 (터미널·Ctrl+C 불필요)
- 시작하면 소리 감지로 자동 녹음, 조용해지면 자동 저장
- 개인 학습용으로만 사용하세요.
"""

import os
import ctypes
import queue
import shutil
import subprocess
import threading
import datetime as dt
import unicodedata

import numpy as np
import sounddevice as sd
import soundfile as sf

import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

import recorder as r  # 녹음 로직 재사용 (save_clip, find_input_device, rms 등)

# 이 Mac에서 화면 1은 내장(3456×2234), 화면 2는 외장 4K(3840×2160)이다.
CAPTURE_DISPLAY = 2


class RecorderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("강의 녹음기")
        self.root.geometry("480x750")
        self.root.resizable(False, False)
        self.root.configure(bg="#f3f5f7")
        self.root.attributes("-topmost", True)  # 다른 앱 위에서 버튼이 항상 보이도록 유지

        self.session_on = False
        self.stop_event = threading.Event()
        self.worker = None
        self.ui_q: "queue.Queue" = queue.Queue()
        self.save_dir = r.SAVE_DIR  # 녹음 상위 폴더
        self.transcribe_dir = os.path.join(r.OUTPUT_ROOT, "2) 전사")
        self.capture_dir = os.path.join(r.OUTPUT_ROOT, "3) 캡처")  # 캡처 상위 폴더
        self.combined_dir = os.path.join(r.OUTPUT_ROOT, "4) 종합폴더")
        self.transcribe_var = None  # _build_ui에서 생성

        self._build_ui()
        self._check_output()
        self._poll_ui()
        self.root.after(500, self._request_screen_capture_permission)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- UI ----------------
    def _build_ui(self):
        title_font = tkfont.Font(family="Apple SD Gothic Neo", size=20, weight="bold")
        subtitle_font = tkfont.Font(family="Apple SD Gothic Neo", size=10)
        status_font = tkfont.Font(family="Apple SD Gothic Neo", size=23, weight="bold")
        button_font = tkfont.Font(family="Apple SD Gothic Neo", size=13, weight="bold")
        small = tkfont.Font(family="Apple SD Gothic Neo", size=11)
        tiny = tkfont.Font(family="Apple SD Gothic Neo", size=10)

        # 상단 제목
        header = tk.Frame(self.root, bg="#f3f5f7")
        header.pack(fill="x", padx=22, pady=(18, 10))
        tk.Label(header, text="🎧  강의 녹음기", font=title_font,
                 bg="#f3f5f7", fg="#172033").pack(anchor="w")
        tk.Label(header, text="녹음부터 전사·캡처·정리까지 한 곳에서",
                 font=subtitle_font, bg="#f3f5f7", fg="#6b7280").pack(anchor="w", pady=(2, 0))

        # 녹음 카드
        record_card = tk.Frame(self.root, bg="#ffffff", highlightbackground="#dfe3e8",
                               highlightthickness=1)
        record_card.pack(fill="x", padx=20, pady=(0, 10))
        self.status = tk.Label(record_card, text="●  대기 중", font=status_font,
                               bg="#eef2f5", fg="#344054", height=2)
        self.status.pack(fill="x", padx=10, pady=(10, 8))
        self.btn = tk.Button(
            record_card, text="●  녹음 시작", font=button_font, height=2,
            command=self._toggle, fg="#12372a", bg="#e7f6ed",
            activebackground="#d1efdc", activeforeground="#12372a",
            relief="flat", bd=0, highlightbackground="#16794a")
        self.btn.pack(fill="x", padx=10, pady=(0, 10))

        # 빠른 작업 카드
        action_card = tk.Frame(self.root, bg="#ffffff", highlightbackground="#dfe3e8",
                               highlightthickness=1)
        action_card.pack(fill="x", padx=20, pady=(0, 10))
        tk.Label(action_card, text="빠른 작업", font=button_font,
                 bg="#ffffff", fg="#344054").grid(
                     row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 7))
        action_card.grid_columnconfigure(0, weight=1, uniform="actions")
        action_card.grid_columnconfigure(1, weight=1, uniform="actions")

        def action_button(text, command, row, column):
            button = tk.Button(
                action_card, text=text, font=small, command=command,
                height=2, bg="#f8fafc", fg="#263244", activebackground="#e8eef5",
                relief="flat", bd=0, highlightbackground="#d8dee6")
            button.grid(row=row, column=column, sticky="nsew", padx=(10 if column == 0 else 5,
                        5 if column == 0 else 10), pady=4)
            return button

        self.capture_btn = action_button("▣  외장 화면 캡처", self._capture_screen_region, 1, 0)
        self.pdf_btn = action_button("▤  PDF 만들기", self._create_pdf, 1, 1)
        self.merge_btn = action_button("♫  음성 합치기", self._confirm_audio_merge, 2, 0)
        self.text_merge_btn = action_button("TXT  전사 합치기", self._merge_transcript_files, 2, 1)

        self.collect_btn = tk.Button(
            action_card, text="4)  오늘 자료를 종합폴더로 모으기", font=button_font,
            command=self._confirm_daily_collection, height=2,
            bg="#edf4ff", fg="#175cd3", activebackground="#dbeafe",
            relief="flat", bd=0, highlightbackground="#84adff")
        self.collect_btn.grid(row=3, column=0, columnspan=2, sticky="ew",
                              padx=10, pady=(6, 10))

        # 설정과 장치 안내
        option_card = tk.Frame(self.root, bg="#ffffff", highlightbackground="#dfe3e8",
                               highlightthickness=1)
        option_card.pack(fill="x", padx=20, pady=(0, 10))
        self.notice = tk.Label(option_card, text="", font=tiny, fg="#b26a00",
                               bg="#ffffff", wraplength=420, justify="left")
        self.notice.pack(fill="x", padx=12, pady=(9, 3))
        self.transcribe_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            option_card, text="녹음 후 자동 전사  ·  Whisper 로컬 처리",
            variable=self.transcribe_var, font=small, bg="#ffffff",
            activebackground="#ffffff", fg="#344054",
            selectcolor="#ffffff").pack(anchor="w", padx=10, pady=(2, 9))

        # 활동 로그 카드
        log_card = tk.Frame(self.root, bg="#ffffff", highlightbackground="#dfe3e8",
                            highlightthickness=1)
        log_card.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        log_header = tk.Frame(log_card, bg="#ffffff")
        log_header.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(log_header, text="최근 활동", font=button_font,
                 bg="#ffffff", fg="#344054").pack(side="left")
        tk.Button(log_header, text="📁  폴더 열기", font=tiny,
                  command=self._open_folder, relief="flat", bd=0,
                  bg="#ffffff", fg="#175cd3", activebackground="#ffffff").pack(side="right")
        self.log = tk.Text(
            log_card, height=7, font=tiny, state="disabled", bg="#f8fafc",
            fg="#344054", relief="flat", bd=0, padx=8, pady=6,
            highlightbackground="#e5e7eb", highlightthickness=1)
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

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
            audio_dir = self._dated_dir(self.save_dir)
            res = r.save_clip(frames, audio_dir, upload_copy=True)
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
            txt, srt = tr.transcribe_file(
                wav_path, out_dir=self._dated_dir(self.transcribe_dir))
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
                elif kind == "capture_started":
                    self.capture_btn.configure(state="disabled")
                    self._log("외장 화면 전체를 캡처하는 중…")
                elif kind == "captured":
                    self.capture_btn.configure(state="normal")
                    self._log(f"📷 화면 캡처 저장: {payload}")
                elif kind == "capture_cancelled":
                    self.capture_btn.configure(state="normal")
                    self._log("화면 캡처 취소")
                elif kind == "capture_error":
                    self.capture_btn.configure(state="normal")
                    self._log(f"⚠️ 화면 캡처 실패: {payload}")
                elif kind == "pdf_started":
                    self.pdf_btn.configure(state="disabled")
                    self._log("남아 있는 PNG를 시간순으로 PDF 변환 중…")
                elif kind == "pdf_created":
                    self.pdf_btn.configure(state="normal")
                    pdf_name, page_count = payload
                    self._log(f"📄 PDF 생성 완료: {pdf_name} ({page_count}페이지)")
                elif kind == "pdf_error":
                    self.pdf_btn.configure(state="normal")
                    self._log(f"⚠️ PDF 변환 실패: {payload}")
                elif kind == "audio_merge_started":
                    self.merge_btn.configure(state="disabled")
                    self._log("분할 음성 파일을 시간순으로 합치는 중…")
                elif kind == "audio_merged":
                    self.merge_btn.configure(state="normal")
                    name, count, duration = payload
                    self._log(f"♫ 합본 완료: {name} ({count}개, {duration:.1f}초)")
                    self._log("합본 검증 후 기존 분할 파일 삭제 완료")
                elif kind == "audio_merge_error":
                    self.merge_btn.configure(state="normal")
                    self._log(f"⚠️ 음성 합치기 실패: {payload}")
                elif kind == "text_merge_started":
                    self.text_merge_btn.configure(state="disabled")
                    self._log("전사 TXT를 시간순으로 합치는 중…")
                elif kind == "text_merged":
                    self.text_merge_btn.configure(state="normal")
                    name, count = payload
                    self._log(f"TXT 합본 완료: {name} ({count}개)")
                    self._log("합본 검증 후 기존 개별 TXT 삭제 완료")
                elif kind == "text_merge_error":
                    self.text_merge_btn.configure(state="normal")
                    self._log(f"⚠️ TXT 합치기 실패: {payload}")
                elif kind == "collection_started":
                    self.collect_btn.configure(state="disabled")
                    self._log("오늘의 녹음·전사·캡처 파일을 종합폴더로 이동 중…")
                elif kind == "collection_done":
                    self.collect_btn.configure(state="normal")
                    folder_name, count = payload
                    self._log(f"4) 종합 완료: {folder_name} ({count}개 이동)")
                    self._log("기존 날짜 폴더는 빈 폴더로 유지")
                elif kind == "collection_error":
                    self.collect_btn.configure(state="normal")
                    self._log(f"⚠️ 종합폴더 이동 실패: {payload}")
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

    # ---------------- 화면 영역 캡처 ----------------
    @staticmethod
    def _dated_dir(base_dir):
        """용도와 번호가 붙은 날짜 폴더 경로를 만든다."""
        base_name = unicodedata.normalize("NFC", os.path.basename(base_dir))
        prefixes = {
            "1)": "녹음1)",
            "2)": "전사2)",
            "3)": "캡처3)",
            "4)": "종합4)",
        }
        prefix = next(
            (label for number, label in prefixes.items() if base_name.startswith(number)),
            "")
        day = dt.datetime.now().strftime("%Y-%m-%d")
        folder_name = f"{prefix} {day}" if prefix else day
        return os.path.join(base_dir, folder_name)

    def _capture_screen_region(self):
        """버튼 클릭 시 지정한 외장 화면 전체를 원본 해상도의 PNG로 저장."""
        self.ui_q.put(("capture_started", None))
        threading.Thread(target=self._run_screen_capture, daemon=True).start()

    @staticmethod
    def _screen_capture_api():
        framework = ctypes.CDLL(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        framework.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        framework.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
        return framework

    def _request_screen_capture_permission(self):
        """macOS 화면 기록 권한을 확인하고 최초 실행 시 시스템 요청을 표시."""
        try:
            framework = self._screen_capture_api()
            if framework.CGPreflightScreenCaptureAccess():
                self._log("✅ 화면 기록 권한 확인 완료")
                return
            granted = framework.CGRequestScreenCaptureAccess()
            if not granted:
                self._log("⚠️ 화면 기록 권한 필요: 시스템 설정에서 '강의 녹음기'를 허용하세요.")
                subprocess.run([
                    "open",
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"])
        except Exception as e:
            self._log(f"화면 기록 권한 확인 실패: {e}")

    def _run_screen_capture(self):
        capture_dir = self._dated_dir(self.capture_dir)
        os.makedirs(capture_dir, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
        path = os.path.join(capture_dir, f"화면_{stamp}.png")
        try:
            # -D 2: 외장 4K 화면 전체, -x: 캡처 효과음 끄기
            result = subprocess.run(
                ["screencapture", "-D", str(CAPTURE_DISPLAY), "-x", path],
                capture_output=True, text=True)
            if result.returncode == 0 and os.path.isfile(path):
                self.ui_q.put(("captured", os.path.basename(path)))
            else:
                detail = result.stderr.strip() or "화면 기록 권한 또는 외장 화면 연결을 확인하세요."
                self.ui_q.put(("capture_error", detail))
        except Exception as e:
            self.ui_q.put(("capture_cancelled", None))
            self.ui_q.put(("error", f"화면 캡처 실패: {e}"))

    def _create_pdf(self):
        """사용자가 잘못된 PNG를 삭제한 뒤 남은 파일로 PDF를 다시 만든다."""
        self.ui_q.put(("pdf_started", None))
        threading.Thread(target=self._run_pdf_creation, daemon=True).start()

    def _run_pdf_creation(self):
        try:
            pdf_path, page_count = self._create_pdf_from_captures()
            self.ui_q.put(("pdf_created", (os.path.basename(pdf_path), page_count)))
        except Exception as e:
            self.ui_q.put(("pdf_error", str(e)))

    def _create_pdf_from_captures(self):
        """오늘 폴더에 남은 화면 PNG 전체로 날짜별 PDF를 새로 생성한다."""
        try:
            from pypdf import PdfReader, PdfWriter
        except ImportError as e:
            raise RuntimeError("pypdf가 없습니다. pip install -r requirements.txt를 실행하세요.") from e

        capture_dir = self._dated_dir(self.capture_dir)
        os.makedirs(capture_dir, exist_ok=True)
        image_paths = sorted(
            os.path.join(capture_dir, name)
            for name in os.listdir(capture_dir)
            if (unicodedata.normalize("NFC", name).startswith("화면_")
                and name.lower().endswith(".png")))
        if not image_paths:
            raise RuntimeError("오늘 폴더에 변환할 화면 PNG가 없습니다.")

        day = dt.datetime.now().strftime("%Y-%m-%d")
        pdf_path = os.path.join(capture_dir, f"강의캡처_{day}.pdf")
        output_tmp = pdf_path + ".tmp"
        temporary_pages = []

        try:
            writer = PdfWriter()
            for index, image_path in enumerate(image_paths):
                page_pdf = os.path.join(
                    capture_dir,
                    f".page_{os.getpid()}_{threading.get_ident()}_{index}.pdf")
                temporary_pages.append(page_pdf)
                converted = subprocess.run(
                    ["sips", "-s", "format", "pdf", image_path, "--out", page_pdf],
                    capture_output=True, text=True)
                if converted.returncode != 0 or not os.path.isfile(page_pdf):
                    detail = converted.stderr.strip() or "sips 변환 결과 파일이 없습니다."
                    raise RuntimeError(f"{os.path.basename(image_path)}: {detail}")
                for page in PdfReader(page_pdf).pages:
                    writer.add_page(page)

            with open(output_tmp, "wb") as f:
                writer.write(f)
            # 남은 PNG로 완성한 임시 PDF만 기존 PDF와 교체한다.
            os.replace(output_tmp, pdf_path)
            return pdf_path, len(writer.pages)
        finally:
            for temporary in temporary_pages + [output_tmp]:
                try:
                    os.remove(temporary)
                except FileNotFoundError:
                    pass

    # ---------------- 분할 음성 합치기 ----------------
    def _audio_parts(self):
        audio_dir = self._dated_dir(self.save_dir)
        if not os.path.isdir(audio_dir):
            return []
        return sorted(
            os.path.join(audio_dir, name)
            for name in os.listdir(audio_dir)
            if (unicodedata.normalize("NFC", name).startswith("강의_")
                and not unicodedata.normalize("NFC", name).startswith("강의_합본_")
                and name.lower().endswith(".wav")))

    def _confirm_audio_merge(self):
        if self.session_on:
            self._log("⚠️ 먼저 녹음을 정지한 뒤 음성 파일을 합쳐주세요.")
            return
        parts = self._audio_parts()
        if len(parts) < 2:
            self._log("합칠 분할 음성 파일이 2개 이상 필요합니다.")
            return
        confirmed = messagebox.askyesno(
            "분할 음성 합치기",
            f"오늘의 음성 파일 {len(parts)}개를 하나로 합친 뒤\n"
            "합본이 정상인지 검증하고 기존 조각을 삭제합니다. 계속할까요?")
        if not confirmed:
            return
        self.ui_q.put(("audio_merge_started", None))
        threading.Thread(target=self._run_audio_merge, args=(parts,), daemon=True).start()

    def _run_audio_merge(self, parts):
        try:
            path, count, duration = self._merge_audio_files(parts)
            self.ui_q.put(("audio_merged", (os.path.basename(path), count, duration)))
        except Exception as e:
            self.ui_q.put(("audio_merge_error", str(e)))

    @staticmethod
    def _merge_audio_files(parts):
        """같은 형식의 WAV를 합치고 검증한 후 원본 조각만 삭제한다."""
        if len(parts) < 2:
            raise RuntimeError("합칠 음성 파일이 2개 이상 필요합니다.")

        first = sf.info(parts[0])
        if first.frames <= 0:
            raise RuntimeError(f"빈 음성 파일입니다: {os.path.basename(parts[0])}")
        for part in parts[1:]:
            info = sf.info(part)
            if info.samplerate != first.samplerate or info.channels != first.channels:
                raise RuntimeError(f"음성 형식이 다릅니다: {os.path.basename(part)}")
            if info.frames <= 0:
                raise RuntimeError(f"빈 음성 파일입니다: {os.path.basename(part)}")

        audio_dir = os.path.dirname(parts[0])
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_path = os.path.join(audio_dir, f"강의_합본_{stamp}.wav")
        temporary = output_path + ".tmp"
        expected_frames = 0

        try:
            with sf.SoundFile(
                    temporary, mode="w", samplerate=first.samplerate,
                    channels=first.channels, subtype=first.subtype, format="WAV") as output:
                for part in parts:
                    with sf.SoundFile(part, mode="r") as source:
                        while True:
                            block = source.read(65536, dtype="float32", always_2d=True)
                            if len(block) == 0:
                                break
                            output.write(block)
                            expected_frames += len(block)

            merged = sf.info(temporary)
            if (merged.frames != expected_frames
                    or merged.samplerate != first.samplerate
                    or merged.channels != first.channels):
                raise RuntimeError("합본 검증에 실패해 기존 파일을 삭제하지 않았습니다.")

            os.replace(temporary, output_path)
            # 완성된 합본을 다시 확인한 뒤에만 원본 조각을 삭제한다.
            verified = sf.info(output_path)
            if verified.frames != expected_frames:
                raise RuntimeError("최종 합본 검증에 실패해 기존 파일을 삭제하지 않았습니다.")
            for part in parts:
                os.remove(part)
            return output_path, len(parts), expected_frames / first.samplerate
        finally:
            try:
                os.remove(temporary)
            except FileNotFoundError:
                pass

    # ---------------- 전사 TXT 합치기 ----------------
    def _merge_transcript_files(self):
        parts = self._transcript_parts()
        if not parts:
            self._log("오늘 폴더에 합칠 개별 전사 TXT가 없습니다.")
            return
        confirmed = messagebox.askyesno(
            "전사 TXT 합치기",
            f"오늘의 개별 TXT {len(parts)}개를 하나로 합친 뒤\n"
            "합본을 검증하고 기존 개별 TXT를 삭제합니다. 계속할까요?")
        if not confirmed:
            return
        self.ui_q.put(("text_merge_started", None))
        threading.Thread(target=self._run_text_merge, args=(parts,), daemon=True).start()

    def _run_text_merge(self, parts):
        try:
            path, count = self._create_merged_transcript(parts)
            self.ui_q.put(("text_merged", (os.path.basename(path), count)))
        except Exception as e:
            self.ui_q.put(("text_merge_error", str(e)))

    def _transcript_parts(self):
        text_dir = self._dated_dir(self.transcribe_dir)
        if not os.path.isdir(text_dir):
            return []
        return sorted(
            os.path.join(text_dir, name)
            for name in os.listdir(text_dir)
            if (unicodedata.normalize("NFC", name).startswith("강의_")
                and not unicodedata.normalize("NFC", name).startswith("강의_전사_합본_")
                and name.lower().endswith(".txt")))

    def _create_merged_transcript(self, parts=None):
        """개별 전사 TXT를 합치고 검증한 후 개별 원본을 삭제한다."""
        text_dir = self._dated_dir(self.transcribe_dir)
        parts = list(parts) if parts is not None else self._transcript_parts()
        if not parts:
            raise RuntimeError("오늘 폴더에 합칠 전사 TXT가 없습니다.")

        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_path = os.path.join(text_dir, f"강의_전사_합본_{stamp}.txt")
        temporary = output_path + ".tmp"
        try:
            has_content = False
            with open(temporary, "w", encoding="utf-8") as output:
                for index, part in enumerate(parts):
                    with open(part, "r", encoding="utf-8") as source:
                        content = source.read().strip()
                    has_content = has_content or bool(content)
                    if index:
                        output.write("\n\n")
                    output.write(content)
                output.write("\n")
            if not has_content:
                raise RuntimeError("전사 내용이 비어 있어 합본을 만들지 않았습니다.")
            os.replace(temporary, output_path)
            with open(output_path, "r", encoding="utf-8") as verified:
                if not verified.read().strip():
                    raise RuntimeError("합본 검증에 실패해 기존 TXT를 삭제하지 않았습니다.")
            for part in parts:
                os.remove(part)
            return output_path, len(parts)
        finally:
            try:
                os.remove(temporary)
            except FileNotFoundError:
                pass

    # ---------------- 날짜별 파일 종합 ----------------
    def _confirm_daily_collection(self):
        if self.session_on:
            self._log("⚠️ 먼저 녹음을 정지한 뒤 종합폴더로 이동하세요.")
            return
        sources = self._daily_collection_sources()
        file_count = sum(len(files) for _, files in sources)
        if file_count == 0:
            self._log("오늘 날짜 폴더에 이동할 파일이 없습니다.")
            return
        confirmed = messagebox.askyesno(
            "4) 종합폴더로 모으기",
            f"오늘의 녹음·전사·캡처 파일 {file_count}개를\n"
            "종합 날짜 폴더로 이동합니다. 기존 날짜 폴더는 비워집니다. 계속할까요?")
        if not confirmed:
            return
        self.ui_q.put(("collection_started", None))
        threading.Thread(target=self._run_daily_collection, daemon=True).start()

    def _daily_collection_sources(self):
        sources = []
        for base_dir in (self.save_dir, self.transcribe_dir, self.capture_dir):
            dated_dir = self._dated_dir(base_dir)
            files = []
            if os.path.isdir(dated_dir):
                files = sorted(
                    os.path.join(dated_dir, name)
                    for name in os.listdir(dated_dir)
                    if os.path.isfile(os.path.join(dated_dir, name))
                    and name != ".DS_Store")
            sources.append((dated_dir, files))
        return sources

    def _run_daily_collection(self):
        try:
            folder, count = self._collect_daily_files()
            self.ui_q.put(("collection_done", (os.path.basename(folder), count)))
        except Exception as e:
            self.ui_q.put(("collection_error", str(e)))

    def _collect_daily_files(self):
        """세 날짜 폴더의 파일을 종합 날짜 폴더로 이동하고 원래 폴더는 유지."""
        sources = self._daily_collection_sources()
        files = [(source_dir, path) for source_dir, paths in sources for path in paths]
        if not files:
            raise RuntimeError("오늘 날짜 폴더에 이동할 파일이 없습니다.")

        destination_dir = self._dated_dir(self.combined_dir)
        os.makedirs(destination_dir, exist_ok=True)
        destinations = []
        names = set()
        for source_dir, source_path in files:
            name = os.path.basename(source_path)
            destination = os.path.join(destination_dir, name)
            if name in names or os.path.exists(destination):
                raise RuntimeError(f"같은 이름의 파일이 있어 이동하지 않았습니다: {name}")
            names.add(name)
            destinations.append((source_dir, source_path, destination))

        moved = []
        try:
            for source_dir, source_path, destination in destinations:
                shutil.move(source_path, destination)
                moved.append((source_path, destination))
        except Exception:
            for source_path, destination in reversed(moved):
                if os.path.exists(destination) and not os.path.exists(source_path):
                    shutil.move(destination, source_path)
            raise

        # Finder가 만든 숨김 파일을 제거해 원래 날짜 폴더에는 내용이 남지 않게 한다.
        for source_dir, _ in sources:
            metadata = os.path.join(source_dir, ".DS_Store")
            try:
                os.remove(metadata)
            except FileNotFoundError:
                pass
        return destination_dir, len(moved)

    def _open_folder(self):
        folders = (self.save_dir, self.transcribe_dir, self.capture_dir, self.combined_dir)
        for folder in folders:
            os.makedirs(folder, exist_ok=True)
        subprocess.run(["open", *folders])

    def _on_close(self):
        self.stop_event.set()
        self.root.after(200, self.root.destroy)


def main():
    root = tk.Tk()
    RecorderGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
