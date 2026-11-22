#!/usr/bin/env python3
"""Audio and video intake with fallbacks that never break the agent.

Order for audio:
1. a transcript next to the file (same name, .srt/.vtt/.txt/.md) or --transcript:
   origin "human" (made or checked by a person);
2. local speech recognition, only if faster_whisper or whisper is already
   installed: origin "auto-transcript", confidence low until a person checks it;
3. otherwise the file is recorded as "pending transcript" with an instruction.

Video: if ffmpeg is on PATH, keyframes and the audio track are extracted and
the audio goes through the same order. Without ffmpeg: sidecar transcript or
"pending transcript". Nothing is installed or downloaded by this module, and
no text is invented: a missing tool means a gap, not a guess.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".avi"}
SIDECAR_SUFFIXES = (".srt", ".vtt", ".txt", ".md")
FRAME_LIMIT = 12
TOOL_TIMEOUT = 900

TIME_RE = re.compile(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?")
CUE_RE = re.compile(
    r"((?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?)\s*-->\s*((?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?)"
)
INLINE_RE = re.compile(r"^\[?((?:\d{1,2}:)?\d{1,2}:\d{2})\]?\s+(.+)$")


class MediaError(ValueError):
    pass


def seconds(stamp: str) -> float:
    match = TIME_RE.fullmatch(stamp.strip())
    if not match:
        raise MediaError(f"Не время: {stamp}")
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    secs = int(match.group(3))
    frac = match.group(4) or "0"
    return hours * 3600 + minutes * 60 + secs + int(frac.ljust(3, "0")[:3]) / 1000.0


def timecode(value: float) -> str:
    total = int(round(value))
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def parse_segments(raw: str) -> list[dict]:
    """Segments with start/end seconds from SRT/VTT, or "[mm:ss] text" lines, or plain paragraphs."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    segments: list[dict] = []
    lines = text.split("\n")
    index = 0
    while index < len(lines):
        cue = CUE_RE.search(lines[index])
        if cue:
            body: list[str] = []
            index += 1
            while index < len(lines) and lines[index].strip() and not CUE_RE.search(lines[index]):
                body.append(re.sub(r"<[^>]+>", "", lines[index]).strip())
                index += 1
            joined = " ".join(item for item in body if item)
            if joined:
                segments.append({"start": seconds(cue.group(1)), "end": seconds(cue.group(2)), "text": joined})
            continue
        index += 1
    if segments:
        return segments
    for line in lines:
        inline = INLINE_RE.match(line.strip())
        if inline:
            segments.append({"start": seconds(inline.group(1)), "end": None, "text": inline.group(2).strip()})
    if segments:
        return segments
    paragraphs = [re.sub(r"\s+", " ", part).strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    return [{"start": None, "end": None, "text": part} for part in paragraphs if not part.startswith("#")]


def to_vtt(segments: list[dict]) -> str:
    out = ["WEBVTT", ""]
    for seg in segments:
        start = float(seg.get("start") or 0.0)
        end = float(seg.get("end") or start)
        out.append(f"{_vtt_stamp(start)} --> {_vtt_stamp(end)}")
        out.append(str(seg.get("text") or "").strip())
        out.append("")
    return "\n".join(out)


def _vtt_stamp(value: float) -> str:
    millis = int(round(value * 1000))
    return f"{millis // 3600000:02d}:{millis % 3600000 // 60000:02d}:{millis % 60000 // 1000:02d}.{millis % 1000:03d}"


def find_sidecar(path: Path) -> Path | None:
    for suffix in SIDECAR_SUFFIXES:
        candidate = path.with_suffix(suffix)
        if candidate.is_file() and candidate != path:
            return candidate
    return None


def recognizer_available(find_spec=None) -> str | None:
    find_spec = find_spec or importlib.util.find_spec
    for name in ("faster_whisper", "whisper"):
        try:
            if find_spec(name) is not None:
                return name
        except (ImportError, ValueError):
            continue
    return None


def transcribe(audio: Path, backend: str, model_size: str = "base") -> list[dict]:
    """Local recognition with an already installed package. Errors become MediaError."""
    try:
        if backend == "faster_whisper":
            from faster_whisper import WhisperModel  # type: ignore

            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            parts, _info = model.transcribe(str(audio))
            return [{"start": float(p.start), "end": float(p.end), "text": str(p.text).strip()} for p in parts if str(p.text).strip()]
        if backend == "whisper":
            import whisper  # type: ignore

            model = whisper.load_model(model_size)
            result = model.transcribe(str(audio))
            return [
                {"start": float(p["start"]), "end": float(p["end"]), "text": str(p["text"]).strip()}
                for p in result.get("segments") or []
                if str(p.get("text") or "").strip()
            ]
    except Exception as exc:  # noqa: BLE001 — model download, codec, memory: all degrade to a gap
        raise MediaError(f"Распознавание {backend} не удалось: {type(exc).__name__}: {str(exc)[:200]}") from exc
    raise MediaError(f"Неизвестный распознаватель: {backend}")


def extract_video(video: Path, out_dir: Path, *, which=None, run=None) -> dict:
    """Keyframes and audio through ffmpeg if it is on PATH. Never raises for a missing tool."""
    which = which or shutil.which
    run = run or subprocess.run
    ffmpeg = which("ffmpeg")
    if not ffmpeg:
        return {"ffmpeg": False, "frames": [], "audio": None, "message": "ffmpeg не найден в PATH: кадры и звук не извлечены."}
    out_dir.mkdir(parents=True, exist_ok=True)
    audio = out_dir / "audio.wav"
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(exist_ok=True)
    messages: list[str] = []
    try:
        run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(audio)],
            check=True, capture_output=True, timeout=TOOL_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        messages.append(f"звук не извлечён: {type(exc).__name__}")
    try:
        run(
            [
                ffmpeg, "-y", "-loglevel", "error", "-i", str(video),
                "-vf", "select='eq(pict_type\\,I)',scale=640:-2", "-vsync", "vfr",
                "-frames:v", str(FRAME_LIMIT), str(frames_dir / "frame-%03d.png"),
            ],
            check=True, capture_output=True, timeout=TOOL_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        messages.append(f"кадры не извлечены: {type(exc).__name__}")
    frames = sorted(frames_dir.glob("frame-*.png"))
    return {
        "ffmpeg": True,
        "frames": frames,
        "audio": audio if audio.is_file() and audio.stat().st_size > 0 else None,
        "message": "; ".join(messages),
    }


def prepare(
    project: Path,
    path: Path,
    *,
    transcript: Path | None = None,
    allow_auto: bool = True,
    which=None,
    run=None,
    find_spec=None,
    recognize=None,
) -> dict:
    """Decide where the text comes from. Returns a dict; never raises for missing tools."""
    which = which or shutil.which
    run = run or subprocess.run
    find_spec = find_spec or importlib.util.find_spec
    suffix = path.suffix.lower()
    if suffix not in AUDIO_SUFFIXES | VIDEO_SUFFIXES:
        raise MediaError("Аудио: mp3, wav, m4a, aac, ogg, flac. Видео: mp4, webm, mov, mkv, avi.")
    kind = "video" if suffix in VIDEO_SUFFIXES else "audio"
    work = project / "media" / path.stem
    notes: list[str] = []
    info: dict = {"kind": kind, "frames": [], "transcript": None, "origin": None, "status": "pending", "notes": notes}
    audio = path
    if kind == "video":
        extracted = extract_video(path, work, which=which, run=run)
        info["frames"] = extracted["frames"]
        if extracted["message"]:
            notes.append(extracted["message"])
        audio = extracted["audio"]
    chosen = transcript or find_sidecar(path)
    if chosen is not None:
        info.update(transcript=chosen, origin="human", status="ready")
        notes.append(f"Расшифровка взята из файла {chosen.name}: происхождение human.")
        return info
    backend = recognizer_available(find_spec) if allow_auto else None
    if audio is not None and backend is not None:
        try:
            segments = (recognize or transcribe)(audio, backend)
        except MediaError as exc:
            notes.append(str(exc))
            segments = []
        if segments:
            work.mkdir(parents=True, exist_ok=True)
            out = work / "transcript.auto.vtt"
            out.write_text(to_vtt(segments), encoding="utf-8", newline="\n")
            info.update(transcript=out, origin="auto-transcript", status="ready", backend=backend)
            notes.append(f"Автоматическая расшифровка ({backend}). Ошибки распознавания возможны: проверьте человеком.")
            return info
    elif backend is None and allow_auto:
        notes.append("Распознаватель речи не установлен (faster-whisper или whisper): автоматической расшифровки нет.")
    notes.append(pending_instruction(path))
    return info


def pending_instruction(path: Path) -> str:
    return (
        f"Запись ждёт расшифровки. Положите рядом файл {path.stem}.srt, .vtt или .txt "
        "(субтитры платформы, расшифровка созвона или ручной конспект с метками [мм:сс]) "
        f"и повторите: python scripts/strat.py media add {path.name}. "
        "Пока расшифровки нет, из записи нельзя делать fact."
    )


def write_segments(folder: Path, segments: list[dict], origin: str) -> Path:
    path = folder / "segments.jsonl"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for number, seg in enumerate(segments, 1):
            row = {
                "n": number,
                "start": seg.get("start"),
                "end": seg.get("end"),
                "timecode": timecode(seg["start"]) if seg.get("start") is not None else None,
                "text": seg.get("text"),
                "origin": origin,
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path
