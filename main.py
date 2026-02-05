import argparse
import sys
from pathlib import Path

from transcriber.whisper_runner import WhisperTranscriber
from transcriber.ffmpeg_extract import extract_wav, require_ffmpeg

from util.file_utils import validate_directory, discover_media_files
from util.config import TEXT_TYPE
from util.logging.logger import Logging

from util.google.drive import DriveSync  # <-- use the class now

from util.openai.openai import extract_questions as extract_questions_fn  # avoid name collision

from datetime import datetime

MASTER_QUESTION_FILENAME = "master_list.txt"
VERSION = "2.0.1"

def parse_args():
    parser = argparse.ArgumentParser(description="Batch transcribe media files using Whisper")

    parser.add_argument(
        "--version",
        action="version",
        version=f"transcription - %(prog)s {VERSION}",
        help="Displays the version",
    )
    parser.add_argument("--root-dir", required=True, help="Directory containing video/audio files")
    parser.add_argument("--ffmpeg_bin", required=True, help="The location of the ffmpeg binary")

    parser.add_argument(
        "--drive-creds",
        help=(
            "Directory containing Drive creds.json (and where drive_tokens.db will be stored). "
            "If omitted, Drive integration is disabled."
        ),
    )

    parser.add_argument("--log-dir", help="The directory to write the logs to.")
    parser.add_argument("--model", default="medium.en", help="Whisper model size (base, small.en, medium.en, large)")

    # store_true flags should default to False and become True if provided
    parser.add_argument("--use-drive", action="store_true", help="Use the drive integration (else local-only)")
    parser.add_argument("--extract-audio", action="store_true", help="Extract audio from files in interviews/")
    parser.add_argument("--build-transcript", action="store_true", help="Build transcripts from audio/video inputs")
    parser.add_argument("--extract-questions", action="store_true", help="Extract questions from transcripts")

    return parser


def extract_audio_func(logger, drive, input_dir, output_dir):
    files = discover_media_files(input_dir)
    if not files:
        logger.warning("No supported media files found.")
        return

    for media_file in files:
        filename = f"{media_file.stem}.wav"
        wav_path = Path(output_dir) / filename

        if wav_path.exists():
            logger.info("Output (%s) already exists. Skipping...", wav_path)
            continue

        logger.info("Extracting audio from %s...", media_file)
        extract_wav(media_file, wav_path)

        # Upload audio to Drive if enabled
        if drive:
            drive.create_drive_file(
                local_path=wav_path,
                parent_folder_name="Extracted Audio",
                mime_type="audio/wav",
            )
def upload_transcript_artifacts(logger, drive, output_dir, stem):
    if not drive:
        return

    output_dir = Path(output_dir)

    artifacts = [
        # (suffix, mime_type)
        (".srt", "application/x-subrip"),
        (".vtt", "text/vtt"),
        (".txt", "text/plain"),
    ]

    for suffix, mime in artifacts:
        p = output_dir / f"{stem}{suffix}"
        if not p.exists():
            logger.debug("Artifact missing, skipping upload: %s", p)
            continue

        logger.info("Uploading transcript artifact: %s", p.name, console=True)
        drive.create_drive_file(
            local_path=p,
            parent_folder_name="Transcripts",
            mime_type=mime,
        )

def transcribe_audio(logger, drive, transcriber, input_dir, output_dir):
    files = discover_media_files(input_dir)
    if not files:
        logger.warning("No supported media files found.")
        return

    output_dir = Path(output_dir)

    for media_file in files:
        required = [".srt", ".vtt", ".txt"]
        if all((output_dir / f"{media_file.stem}{ext}").exists() for ext in required):
            logger.info("Transcript output already exists at '%s'. Skipping...", output_dir)
            continue

        logger.info("Transcribing: %s", media_file.name, console=True)

        transcriber.transcribe(
            media_path=media_file,
            output_dir=output_dir,
            use_verbose=True,
        )

        # Upload all artifacts Whisper produced
        upload_transcript_artifacts(logger, drive, output_dir, media_file.stem)

def openai_extract_questions(logger, drive, input_dir, output_dir):
    files = discover_media_files(input_dir, type=TEXT_TYPE)
    if not files:
        logger.warning("No transcript files found.")
        return

    master_path = Path(output_dir) / MASTER_QUESTION_FILENAME

    for media_file in files:
        filename = f"{media_file.stem}.txt"
        question_path = Path(output_dir) / filename
        transcript_path = Path(input_dir) / media_file

        if question_path.exists():
            logger.info("Output (%s) already exists. Skipping...", question_path)
            continue
        logger.info("Extracting questions from path: %s", transcript_path, console=True)
        logger.info("Extracting questions from: %s", media_file.name, console=True)

        questions = extract_questions_fn(transcript_path, question_path)

        logger.info("Extracted questions.")
        logger.info(f"size of questions: {questions if questions else 'None'}")

        if drive and question_path.exists():
            logger.info("Uploading to drive...", console=True)
            drive.create_drive_file(
                local_path=question_path,
                parent_folder_name="Extracted Questions",
                mime_type="text/plain",
            )

        logger.info("Appending to the master list...")
        appended = append_to_master_list(
            logger, questions, master_path, media_file.stem
        )
        if appended:
            logger.info("Master list appended.")
        else:
            logger.info("Unable to append.")

        logger.info("Uploading/updating the drive's master list...")
        if drive and (appended or not master_path.exists() is False):
            drive.create_or_update_by_local_path(
                local_path=master_path,
                folder_name="Extracted Questions",
                mime_type="text/plain",
            )
        logger.info("Drive's master list uploaded/updated.")

def append_to_master_list(
    logger,
    questions,
    master_path,
    source_label = None,
):

    logger.info("Starting master list append...", console=True)

    logger.info(f"Arguments:\nappend_to_master_list(logger, {len(questions) if questions else 'None'}, {master_path}, {source_label})")

    if not questions:
        logger.warning("Extracted questions missing, cannot append...", console=True)
        return False

    new_lines = [question for question in questions if float(question["confidence"]) > 0.40]

    if not new_lines:
        logger.warning("No suitable questions found; skipping master append.", console=True)
        return False


    existing = set()
    if master_path.exists():
        existing = set()
        for ln in master_path.read_text(encoding="utf-8").splitlines():
            formatted = extract_question(ln)
            if formatted:
                existing.add(formatted)

    # Keep only truly new lines (exact match)
    to_add = [
        f"\t{idx+1:4}.) {ln['canonical_question']} ({ln['time']})" 
        for idx,ln in enumerate(new_lines) 
        if ln["canonical_question"].strip() not in existing
    ]

    if not to_add:
        logger.info("No new unique questions to append for %s.", per_file_path.name)
        return False

    header = []
    ts = datetime.now().isoformat(timespec="seconds")
    if source_label:
        header = [f"\n--- {source_label} @ {ts} ---"]

    with master_path.open("a", encoding="utf-8", newline="\n") as f:
        for h in header:
            f.write(h + "\n")
        for ln in to_add:
            f.write(ln + "\n")

    logger.info("Appended %d new lines to %s", len(to_add), master_path.name)
    return True

def extract_question(line):
    line = line.strip()

    # Skip source headers
    if line.startswith("--- ") and line.endswith(" ---"):
        return None
    try:
        _, rest = line.split(" ", 1)
        question, _ = rest.rsplit(" (", 1)
        return question.strip()
    except ValueError:
        # If format is unexpected, treat as non-question
        return None

def main():
    parser = parse_args()
    args = parser.parse_args()

    root_dir = validate_directory(args.root_dir, True)
    interview_video_dir = validate_directory(f"{root_dir}\\interviews", True)
    transcript_dir = validate_directory(f"{root_dir}\\transcripts", True)
    interview_audio_dir = validate_directory(f"{root_dir}\\audio", True)
    questions_dir = validate_directory(f"{root_dir}\\questions", True)

    log_dir = validate_directory(f"{args.log_dir}", True) if args.log_dir else None
    ffmpeg_bin = args.ffmpeg_bin or ""

    require_ffmpeg(ffmpeg_bin)

    # Drive is only enabled if both flag AND creds dir are provided
    use_drive = bool(args.use_drive and args.drive_creds)
    drive_creds_dir = validate_directory(args.drive_creds) if use_drive else None

    extract_audio = bool(args.extract_audio)
    build_transcripts = bool(args.build_transcript)
    extract_questions_flag = bool(args.extract_questions)

    if log_dir:
        Logging.set_log_dir(log_dir, create=True)

    logger = Logging.get("main driver")

    do_anything = use_drive or extract_audio or build_transcripts or extract_questions_flag
    if not do_anything:
        logger.warning("No actions selected. Provide flags like --extract-audio, --build-transcript, etc.", console=True)
        parser.print_help()
        sys.exit(1)

    logger.info("-------- Directories ---------------------------")
    logger.info("Root directory: %s", root_dir)
    logger.info("Interview video directory: %s", interview_video_dir)
    logger.info("Interview audio directory: %s", interview_audio_dir)
    logger.info("Transcript directory: %s", transcript_dir)
    logger.info("Questions directory: %s", questions_dir)
    logger.info("Using ffmpeg binary location: %s", ffmpeg_bin)

    logger.info("-------- Options -------------------------------")
    logger.info("Use Drive: %s", use_drive)
    logger.info("Extract audio: %s", extract_audio)
    logger.info("Build transcripts: %s", build_transcripts)
    logger.info("Extract questions: %s", extract_questions_flag)

    # Initialize DriveSync once (stateful: service + db + caches)
    drive = None
    if use_drive:
        drive = DriveSync(credential_location=drive_creds_dir, output_root=root_dir)

        logger.info("Syncing from Drive to local folders (bootstrap vs incremental)...")
        drive.sync()
        logger.info("Drive sync complete.")

    transcriber = WhisperTranscriber(model_name=args.model)

    if extract_audio:
        logger.info("Extracting audio...")
        extract_audio_func(logger, drive, interview_video_dir, interview_audio_dir)
        logger.info("Extracted audio.")

    if build_transcripts:
        # If audio was extracted, you probably want to transcribe audio files; otherwise use video dir
        input_dir = interview_audio_dir if extract_audio else interview_video_dir
        logger.info("Transcribing...")
        transcribe_audio(logger, drive, transcriber, input_dir, transcript_dir)
        logger.info("Transcription complete.")

    if extract_questions_flag:
        logger.info("Extracting questions...")
        openai_extract_questions(logger, drive, transcript_dir, questions_dir)
        logger.info("Questions extracted.")

    logger.info("Processing complete.")


if __name__ == "__main__":
    main()
