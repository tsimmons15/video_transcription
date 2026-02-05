import whisper
from whisper.utils import get_writer
from pathlib import Path
from util.config import DEFAULT_MODEL, DEFAULT_LANGUAGE, DEFAULT_OUTPUT_FORMATS

from util.logging.logger import Logging


class WhisperTranscriber:
    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        language: str = DEFAULT_LANGUAGE
    ):
        self.logger = Logging.get("whisper")
        self.model = whisper.load_model(model_name)
        self.language = language

    def transcribe(
        self,
        media_path: Path,
        output_dir: Path,
        output_formats=None,
        use_verbose=False
    ):
        self.logger.info("Transcription started.")
        output_formats = output_formats or DEFAULT_OUTPUT_FORMATS
        output_dir.mkdir(parents=True, exist_ok=True)

        self.logger.info("Calling transcribe...")
        result = self.model.transcribe(
            str(media_path),
            language=self.language,
            verbose=use_verbose
        )
        self.logger.info("Transcribed")

        base_name = media_path.stem
        self.logger.info(f"File base name: {base_name}")

        if "txt" in output_formats:
            self._write_txt(result, output_dir / f"{base_name}.txt")

        if "srt" in output_formats:
            self._write_srt(result, output_dir / f"{base_name}.srt")

        if "vtt" in output_formats:
            self._write_vtt(result, output_dir / f"{base_name}.vtt")

    def _get_writer(self, writer_type, output_dir):
        return get_writer(writer_type, output_dir)

    def _write_txt(self, result, path: Path):
        writer = self._get_writer("txt", path.parent)
        writer(result, path.stem)


    def _write_srt(self, result, path: Path):
        writer = self._get_writer("srt", path.parent)
        writer(result, path.stem)


    def _write_vtt(self, result, path: Path):
        writer = self._get_writer("vtt", path.parent)
        writer(result, path.stem)
        
