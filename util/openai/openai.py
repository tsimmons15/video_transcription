import json, re
from pathlib import Path

from openai import OpenAI

from util.logging.logger import Logging

def call_openai(logger, client, model, system, user, temperature = 0):
    logger.info("Sending request...")
    response = client.responses.create(
        model=model,
        service_tier="flex",
        input=[
            {
                "role": "system",
                "content": system
            },
            {
                "role":"user",
                "content": user
            }
        ],
    )
    
    logger.info(f"The full response for this pass: \n{response}")

    return response.output_text or ""

PASS1_SYSTEM = """You are a transcript normalizer for interview recordings.

Input is SRT (number, time range, text). Output must be a cleaned transcript that:

NON-INTERACTIVE MODE
- You MUST NOT ask questions, request confirmation, propose batching, or mention limitations.
- If the snippet is incomplete or ambiguous, still produce best-effort normalized output for the provided SRT only.
- Output ONLY JSONL lines. Never output suggestions, plans, or meta text.

FORMAT
- One line per utterance
- Format exactly as JSON (no markdown): {"time":"HH:MM:SS","speaker":"SPEAKER","line":"text"}
- HH:MM:SS must be the START time from the SRT time range

SPEAKER LABELING (CRITICAL)
- Prefer consistent speaker labels. Reuse labels you have already used in this snippet.
- If you cannot infer speaker, use "UNKNOWN" (do not ask to clarify).

NAME INTRODUCTION HEURISTICS (IMPORTANT)
- If a line includes self-introduction patterns like:
  - "my name is X"
  - "this is X"
  - "I’m X"
  then treat X as a speaker name label (uppercase the label, e.g., "AKIT").
- If the self-introduction occurs with interview framing (e.g., "thank you for your time", "today", "we'll start", role/company info),
  treat that named speaker as the interviewer for subsequent turns in the snippet (still label as the name, e.g., "AKIT").
- Once a name label is introduced, apply it to subsequent turns by that same speaker within this snippet when reasonable.

CLEANUP RULES
- Remove SRT line numbers and formatting artifacts
- Merge broken lines belonging to the same utterance
- Clean grammar, capitalization, and punctuation without changing meaning
- Remove filler words ONLY when they do not affect meaning
- Do not summarize or add new information
- Preserve consent/recording/procedural statements

OUTPUT CONSTRAINTS
- Return ONLY the JSONL lines
- Do not include any additional keys besides time/speaker/line
"""

PASS2_SYSTEM = """You refine interviewer questions from a timestamped interview transcript.

Input format:
{"time": "HH:MM:SS", "speaker": "SPEAKER", "line":"line"}

SPEAKER HANDLING
- Treat any named speaker who is clearly acting as the interviewer as INTERVIEWER
- Treat the literal label "INTERVIEWER" as interviewer
- Do NOT treat CANDIDATE or named candidate speakers as interviewer
- If a named speaker is ambiguous, prefer role labels over guessing

QUESTION EXTRACTION RULES
- Extract only questions asked by the interviewer or unknown
- Include direct questions and polite request-style questions
  (e.g., "Can you explain...", "Could you walk me through...")
- Split multiple questions in a single utterance into separate items
- Exclude rhetorical questions and candidate-asked questions
- A question needs to include the context from before and after it.
- Do NOT invent questions or reword beyond cleanup and adding in context missing from the line

OUTPUT FORMAT
Return a JSON array. Each item must include:
- time: timestamp of the utterance
- speaker: speaker name or role as it appears in the input
- type: one of ["consent","intro","behavioral","technical","logistics","clarification","other"]
- confidence: number between 0.0 and 1.0
- question: cleaned question text, keep close to original
- canonical_question: add minimal missing nouns and descriptions based on context only. MUST NOT add facts not present in the provided turns.

OUTPUT CONSTRAINTS
- Return JSON only
- No commentary or explanations
"""

def pass1_normalize_srt(
    logger,
    client,
    srt_text,
    system = None,
    user = None,
    model = "gpt-5-nano",
    *,
    max_blocks = 100,
    overlap_lines = 15,
):
    """
    Pass 1: Normalize SRT into JSONL turns with *overlapping context*.

    Overlap behavior:
      - Each chunk call includes up to `overlap_lines` *raw SRT lines* from the previous chunk as context.
      - Those overlap lines are NOT included in the preserved output (deduped out).
      - Deduping is done by the parsed output object's "time" field (start timestamp),
        with a fallback to (time,speaker,line) if needed.

    Assumptions:
      - chunk_srt_by_blocks(srt_text, max_blocks=...) returns a list[str] chunks of raw SRT blocks.
      - is_valid_pass1 validates JSONL output schema.
      - normalize_srt_with_retry(...) calls the model with retry+validator.
    """

    turns = []
    turn_idx = 1
    max_tries = 3

    system = PASS1_SYSTEM if not system else system
    block_chunks = chunk_srt_by_blocks(srt_text, max_blocks=max_blocks)

    pass1_msg = """
CORRECTION: Output ONLY JSONL lines. Each line must be a single JSON object like
{"time":"HH:MM:SS","speaker":"SPEAKER","line":"text"}.
No commentary. No questions to the user. If uncertain, speaker must be UNKNOWN.
"""

    speaker_set = set()

    # Overlap context holder: last N raw SRT lines from the previous chunk (as context only)
    prev_context_lines = []

    # Track what we have already preserved so we can exclude overlap output.
    # Primary key: time (SRT start time is unique-ish for utterances)
    seen_times = set()
    # Secondary key: full tuple in case time repeats (rare but possible)
    seen_triplets = set()

    for chunk_idx, chunk in enumerate(block_chunks):
        # Build speaker hint (optional, helps consistency across chunks)
        speaker_hint = ""
        if speaker_set:
            speaker_hint = "Known speakers so far:\n- " + "\n- ".join(sorted(speaker_set)) + "\n\n"

        # Build overlap context (raw SRT lines) to prepend to this chunk
        overlap_context = ""
        if prev_context_lines:
            overlap_context = (
                "OVERLAP CONTEXT (do NOT repeat in output if already seen):\n"
                + "\n".join(prev_context_lines)
                + "\n\n"
            )

        # Construct user prompt for this chunk
        if not user:
            tmp_user = f"""Normalize this SRT snippet:
Remember: output ONLY lines like {{"time":"HH:MM:SS","speaker":"SPEAKER","line":"line"}}. No other text.

{speaker_hint}{overlap_context}SRT:
{chunk}"""
        else:
            # If caller provided a custom user prompt, still append the chunk + overlap context
            tmp_user = f"""{user}

{speaker_hint}{overlap_context}SRT:
{chunk}"""

        out = normalize_srt_with_retry(
            logger,
            client,
            is_valid_pass1,
            pass1_msg,
            model,
            system,
            tmp_user,
            max_tries,
        )

        # Parse + preserve output, excluding anything that likely came from overlap
        for ln in out.splitlines():
            ln = ln.strip()
            if not ln:
                continue

            try:
                obj = json.loads(ln)
            except Exception:
                # Validator should prevent this; keep safe-guard anyway
                continue

            t = (obj.get("time") or "").strip()
            spk = (obj.get("speaker") or "").strip()
            line_txt = (obj.get("line") or "").strip()

            # Dedup gate: drop anything we've already kept (overlap replay)
            triplet = (t, spk, line_txt)
            if t and t in seen_times:
                continue
            if triplet in seen_triplets:
                continue

            # Accept + record
            if t:
                seen_times.add(t)
            seen_triplets.add(triplet)

            if spk:
                speaker_set.add(spk)

            obj["turn"] = turn_idx
            turn_idx += 1
            turns.append(obj)

        # Update overlap context for next chunk:
        # take the last `overlap_lines` *non-empty* raw lines from THIS chunk
        raw_lines = [l.rstrip("\r\n") for l in chunk.splitlines()]
        raw_lines = [l for l in raw_lines if l.strip()]  # drop empty lines
        prev_context_lines = raw_lines[-overlap_lines:] if overlap_lines > 0 else []

    return turns


def pass2_extract_questions(logger, client, turns, system, user = None, model = "gpt-5-nano"):
    jsonl = "\n".join(json.dumps(t, ensure_ascii=False) for t in turns)

    max_tries = 3

    system = PASS2_SYSTEM if not system else system

    tmp_user = f"""Extract interviewer questions from these transcript turns (JSONL):
Remember: output ONLY lines like {{"turns": 1, "time": "HH:MM:SS", "speaker": "SPEAKER", "line":"line"}}. No other text.

{jsonl}""" if not user else f"{user}\n\n{jsonl}"

    pass2_msg = """
CORRECTION: Return a JSON array only (no markdown, no commentary). "
Each element must contain: time, speaker, type, confidence, question.
"""

    out = normalize_srt_with_retry(
        logger, 
        client, 
        is_valid_pass2, 
        pass2_msg, 
        model, 
        system, 
        tmp_user, 
        max_tries
    )
    #out = call_openai(model=model, system=PASS2_SYSTEM, user=user)
    return json.loads(out)

BAD_META_RE = re.compile(r"\b(would you like|please confirm|i can start|process this in batches)\b", re.I)

def is_valid_pass1(text):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    if any(BAD_META_RE.search(ln) for ln in lines):
        return False

    # Each line must be valid JSON with required keys
    for ln in lines:
        try:
            obj = json.loads(ln)
        except Exception:
            return False
        if not isinstance(obj, dict):
            return False
        if "time" not in obj or "speaker" not in obj or "line" not in obj:
            return False
        if not isinstance(obj["time"], str) or not isinstance(obj["speaker"], str) or not isinstance(obj["line"], str):
            return False
    return True

def is_valid_pass2(text):
    try:
        data = json.loads(text)
    except Exception:
        return False
    if not isinstance(data, list):
        return False
    # light schema check
    for item in data:
        if not isinstance(item, dict):
            return False
        for k in ("time", "speaker", "type", "confidence", "question"):
            if k not in item:
                return False
    return True

def normalize_srt_with_retry(logger, client, validator, validator_msg, model, system, user = None, max_tries=3):
    msg = None

    for attempt in range(1, max_tries + 1):
        text = call_openai(logger, client, model, system if msg is None else f"{system}\n\n{msg}", user)
        if validator(text):
            return text

        msg = validator_msg

    # last resort: return empty or raise
    raise ValueError(f"Pass normalization failed validation after {max_tries} retries. Text generated: {text}")

# ---- Just straight chunks, if needed. Need to provide overlapping windows since the timestamps --
# ---- and other aspects of the SRT might be split up between windows -----------------------------
def chunk_lines(text, max_lines = 200, *, drop_empty = True):
    if max_lines <= 0:
        raise ValueError("max_lines must be > 0")

    # Preserve original line endings if requested
    lines = text.splitlines()

    if drop_empty:
        lines = [ln.strip("\n\r") for ln in lines if ln.strip()]

    chunks = []
    for i in range(0, len(lines), max_lines):
        block = lines[i : i + max_lines]
        chunks.append("\n".join(block))
    return chunks


# --- SRT-aware chunking  -----------------------------------------

_SRT_TIME_RE = re.compile(r"^\s*\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}\s*$")

def chunk_srt_by_blocks(srt_text, max_blocks = 120, *, drop_empty = True):
    if max_blocks <= 0:
        raise ValueError("max_blocks must be > 0")

    raw = srt_text.strip()
    if not raw:
        return []

    # Split by blank-line separators between blocks
    parts = re.split(r"\n\s*\n", raw)

    blocks = []
    for part in parts:
        lines = [ln.rstrip() for ln in part.splitlines()]
        # Drop fully empty
        if drop_empty and not any(ln.strip() for ln in lines):
            continue

        # Basic sanity: must contain a time line somewhere
        has_time = any(_SRT_TIME_RE.match(ln) for ln in lines)
        if not has_time:
            # Keep it anyway (some SRTs are messy), but you could also skip
            pass

        blocks.append("\n".join(lines).strip())

    chunks = []
    for i in range(0, len(blocks), max_blocks):
        chunk_blocks = blocks[i : i + max_blocks]
        chunks.append("\n\n".join(chunk_blocks).strip() + "\n")
    return chunks

def extract_questions(input_path, output_path):
    logger = Logging.get("question-extract")

    client = OpenAI()

    logger.info("Extracting questions from %s", input_path)
    try:
        srt_text = input_path.read_text(encoding="utf-8", errors="ignore")

        turns = pass1_normalize_srt(logger, client, srt_text, PASS1_SYSTEM, model="gpt-5.2")

        base_dir = input_path.parent.parent
        if not base_dir.exists():
            raise ValueError(f"Cannot determine processed dir for {input_path}")

        processed_transcripts = base_dir / "processed"
        processed_transcripts.mkdir(parents=True, exist_ok=True)

        logger.info(f"Writing processed to {processed_transcripts}/{input_path.stem}.raw.jsonl")
        processed_transcript = processed_transcripts / f"{input_path.stem}.raw.jsonl"
        processed_transcript.write_text(json.dumps(turns, ensure_ascii=False), encoding="utf-8")

        questions = pass2_extract_questions(logger, client, turns, PASS2_SYSTEM, model="gpt-5.2")

        if questions:
            output_path.write_text(json.dumps(questions, indent=2), encoding="utf-8")
        else:
            # A fail path so that I have a record it failed, etc. and it won't keep trying to extract the empty interview
            logger.warning("Failed to extract useful questions, writing 'empty' output.")
            output_path.write_text(json.dumps("""[{'time': '00:00:14","speaker": "UNKNOWN","type": "logistics","confidence": 0.78,"question": "Did it succeed?","canonical_question": "Did the transcription succeed?"}]""", indent=2), encoding="utf-8")
    except Exception as e:
        logger.error("Failed extracting questions from %s", input_path)
        questions = None
    
    return questions
