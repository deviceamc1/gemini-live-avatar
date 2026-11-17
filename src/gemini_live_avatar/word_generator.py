import logging
import os
import tempfile
import wave
from functools import lru_cache
from typing import Optional

import numpy as np
import torch
import whisperx
from scipy.signal import resample_poly

from gemini_live_avatar.singleton import Singleton

logger = logging.getLogger(__name__)


class WordGenerator(metaclass=Singleton):
    def __init__(self, model_size: str = "tiny", compute_type: str = "int8", default_language: str = "en"):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model_size = model_size
        self.compute_type = compute_type
        self.default_language = default_language
        self._model = None

        # Preload the default alignment model so the first turn has zero model-load delay.
        logger.info(f"📦 Preloading alignment model ({default_language}) on {self.device}")
        self.get_alignment_model(default_language, self.device)

    @property
    def model(self):
        """Lazy-load Whisper ASR only when no transcript is provided."""
        if self._model is None:
            logger.info(f"📥 Lazy-loading fallback Whisper model ({self.model_size}, {self.compute_type}) on {self.device}")
            self._model = whisperx.load_model(self.model_size, device=self.device, compute_type=self.compute_type)
        return self._model

    def generate_from_bytes(
        self,
        audio_bytes: bytes,
        transcript: Optional[str] = None,
        language: Optional[str] = None,
    ) -> dict:
        """
        Accepts raw PCM audio bytes (mono, 16-bit, 24000Hz) and produces word-level
        timestamps for TalkingHead lip-sync. When `transcript` is provided (from
        Gemini's output_audio_transcription), skips Whisper ASR completely and runs
        in-memory phoneme alignment directly.
        """
        clean_transcript = (transcript or "").strip()
        lang_code = (language or self.default_language).split("-")[0].lower() or self.default_language

        if clean_transcript:
            return self._align_pcm_bytes(audio_bytes, clean_transcript, lang_code)

        # Fallback to Whisper ASR if Gemini did not provide output transcription
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp_path = tmp.name

        try:
            with wave.open(tmp_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(audio_bytes)
            return self.generate(tmp_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def _align_pcm_bytes(self, audio_bytes: bytes, transcript: str, language: str) -> dict:
        pcm_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
        if pcm_int16.size == 0:
            return {"words": [], "wtimes": [], "wdurations": []}

        duration_s = pcm_int16.size / 24000.0
        audio_24k = pcm_int16.astype(np.float32) / 32768.0
        # Resample 24kHz -> 16kHz in memory (exact 2:3 ratio) for WhisperX wav2vec2 alignment
        audio_16k = resample_poly(audio_24k, 2, 3).astype(np.float32)

        try:
            align_model, metadata = self.get_alignment_model(language, self.device)
            segments = [{"text": transcript, "start": 0.0, "end": duration_s}]
            aligned = whisperx.align(
                segments,
                align_model,
                metadata,
                audio_16k,
                self.device,
                return_char_alignments=False,
            )
            words_buffer = self._parse_alignment(aligned, duration_s)
            if words_buffer["words"]:
                return words_buffer
        except Exception as e:
            logger.warning(f"⚠️ Phoneme alignment failed, falling back to uniform word timing: {e}")

        return self._uniform_word_timings(transcript, duration_s)

    def generate(self, audio_path: str) -> dict:
        logger.info("🧠 Transcribing audio (fallback ASR)...")
        result = self.model.transcribe(audio_path, language=self.default_language)
        segments = result.get("segments", [])
        if not segments:
            raise ValueError("❌ No segments found in transcription. Check the audio quality.")

        language = result.get("language", self.default_language)
        align_model, metadata = self.get_alignment_model(language, self.device)

        aligned = whisperx.align(
            segments,
            align_model,
            metadata,
            audio_path,
            self.device,
            return_char_alignments=False,
        )
        total_end = max((float(s.get("end", 0.0)) for s in segments), default=0.0)
        return self._parse_alignment(aligned, total_end)

    @staticmethod
    @lru_cache(maxsize=4)
    def get_alignment_model(language: str, device: str):
        logger.info(f"📦 Loading alignment model for language: {language}")
        return whisperx.load_align_model(language_code=language, device=device)

    @staticmethod
    def _uniform_word_timings(transcript: str, duration_s: float) -> dict:
        tokens = transcript.split()
        if not tokens:
            return {"words": [], "wtimes": [], "wdurations": []}
        total_ms = max(int(duration_s * 1000), len(tokens) * 100)
        step_ms = max(total_ms // len(tokens), 50)
        return {
            "words": tokens,
            "wtimes": [i * step_ms for i in range(len(tokens))],
            "wdurations": [step_ms for _ in tokens],
        }

    def _parse_alignment(self, aligned_data: dict, total_duration_s: float = 0.0) -> dict:
        words_buffer = {
            "words": [],
            "wtimes": [],
            "wdurations": [],
        }

        last_end_ms = 0
        total_ms = max(int(total_duration_s * 1000), 0)

        for segment in aligned_data.get("segments", []):
            words = segment.get("words", [])
            for idx, word in enumerate(words):
                word_text = word.get("word", "").strip()
                if not word_text:
                    continue

                if "start" in word and "end" in word:
                    start_ms = int(float(word["start"]) * 1000)
                    end_ms = int(float(word["end"]) * 1000)
                else:
                    # Interpolate if alignment omitted timestamps for symbols/digits
                    remaining = max(len(words) - idx, 1)
                    fallback_dur = max((total_ms - last_end_ms) // remaining, 120)
                    start_ms = last_end_ms
                    end_ms = start_ms + fallback_dur

                duration_ms = max(end_ms - start_ms, 50)
                words_buffer["words"].append(word_text)
                words_buffer["wtimes"].append(start_ms)
                words_buffer["wdurations"].append(duration_ms)
                last_end_ms = max(last_end_ms, start_ms + duration_ms)

        return words_buffer
