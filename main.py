import argparse
from pathlib import Path

from transcriber.file_utils import validate_directory, discover_media_files, get_media_file
from transcriber.whisper_runner import WhisperTranscriber
from transcriber.ffmpeg_extract import extract_wav, require_ffmpeg


def parse_args():
    parser = argparse.ArgumentParser(
        description="Batch transcribe media files using Whisper"
    )
    parser.add_argument(
        "--root-dir",
        required=True,
        help="Directory containing video/audio files"
    )
    parser.add_argument(
        "--ffmpeg_dir",
        required=True,
        help="The location of the ffmpeg binary"
    )
    parser.add_argument(
        "--model",
        default="medium",
        help="Whisper model size (base, small, medium, large)"
    )
    parser.add_argument(
        "--extract-audio",
        default="True",
        action="store_true",
        help="Whether the extract audio first before whisper parsing"
    )
    return parser.parse_args()

r"""
    python main.py --root-dir "C:\Users\Timothy Simmons\Documents\BD\project" --ffmpeg_dir "C:\Users\Timothy Simmons\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0.1-full_build\bin\ffmpeg.exe" --extract-audio
"""

def main():
    args = parse_args()

    root_dir = validate_directory(args.root_dir, True)
    input_dir = validate_directory(f"{root_dir}\\interviews", True)
    output_dir = validate_directory(f"{root_dir}\\transcripts", True)
    interview_audio = validate_directory(f"{root_dir}\\audio", True)
    ffmpeg_dir = args.ffmpeg_dir or ""

    require_ffmpeg(ffmpeg_dir)

    extract_audio = args.extract_audio or False
    print(f"Input directory: {input_dir}")
    print(f"Output directory: {output_dir}")

    files = discover_media_files(input_dir)
    if not files:
        print("No supported media files found.")
        return

    transcriber = WhisperTranscriber(model_name=args.model)

    for media_file in files:
        if extract_audio:
            filename = f"{media_file.stem}.wav"
            wav_path = interview_audio / filename
            extract_wav(media_file, wav_path)
            result = get_media_file(interview_audio, filename)
            print(f"The results: {result}")
            media_file = result[0]
        print(f"Transcribing: {media_file.name}")
        transcriber.transcribe(
            media_path=media_file,
            output_dir=output_dir,
            use_verbose=True
        )

    print("Transcription complete.")


if __name__ == "__main__":
    main()
