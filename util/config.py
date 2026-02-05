from pathlib import Path

MEDIA_TYPE = 0
TEXT_TYPE = 1

SUPPORTED_TEXT_EXTENSIONS = {
    ".srt"
}
SUPPORTED_MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi",
    ".wav", ".mp3", ".m4a", ".flac"
}

DEFAULT_MODEL = "medium.en"
DEFAULT_LANGUAGE = "en"

DEFAULT_OUTPUT_FORMATS = ["txt", "srt", "vtt"]
