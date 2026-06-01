from __future__ import annotations

import argparse
import json
from pathlib import Path


WHISPER_LANG_MAP = {
    "eng_Latn": "english",
    "zho_Hans": "chinese",
    "zho_Hant": "chinese",
    "fra_Latn": "french",
    "spa_Latn": "spanish",
    "deu_Latn": "german",
    "jpn_Jpan": "japanese",
    "kor_Hang": "korean",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minimal repro for s2tt_nllb")
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--source-lang", required=True)
    parser.add_argument("--target-lang", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--weights-root",
        default=str(Path(__file__).resolve().parent / "weights"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    fixture = Path(args.fixture).resolve()
    if not fixture.exists():
        raise FileNotFoundError(f"Fixture not found: {fixture}")

    weights_root = Path(args.weights_root).resolve()
    asr_dir = weights_root / "asr_frontend"
    mt_dir = weights_root / "mt_backend"
    if not asr_dir.exists() or not mt_dir.exists():
        raise FileNotFoundError(
            f"Expected bundled weights at {asr_dir} and {mt_dir}"
        )

    import librosa
    import torch
    from transformers import (
        AutoModelForSeq2SeqLM,
        AutoModelForSpeechSeq2Seq,
        AutoProcessor,
        AutoTokenizer,
    )

    device = args.device
    asr_dtype = torch.float16 if device == "cuda" and torch.cuda.is_available() else torch.float32

    processor = AutoProcessor.from_pretrained(str(asr_dir))
    asr_model = AutoModelForSpeechSeq2Seq.from_pretrained(
        str(asr_dir),
        dtype=asr_dtype,
        low_cpu_mem_usage=True,
    )
    tokenizer = AutoTokenizer.from_pretrained(str(mt_dir))
    mt_model = AutoModelForSeq2SeqLM.from_pretrained(str(mt_dir))

    if device == "cuda" and torch.cuda.is_available():
        asr_model = asr_model.to("cuda")
        mt_model = mt_model.to("cuda")
    else:
        asr_model = asr_model.to("cpu")
        mt_model = mt_model.to("cpu")

    whisper_language = WHISPER_LANG_MAP.get(args.source_lang)
    if whisper_language is None:
        raise ValueError(f"Unsupported source_lang for repro: {args.source_lang}")

    audio, _ = librosa.load(str(fixture), sr=16000, mono=True)
    prompt_ids = processor.get_decoder_prompt_ids(language=whisper_language, task="transcribe")
    inputs = processor(audio=audio, sampling_rate=16000, return_tensors="pt")
    input_features = inputs.input_features.to(asr_model.device)
    predicted_ids = asr_model.generate(
        input_features,
        forced_decoder_ids=prompt_ids,
        max_new_tokens=225,
    )
    asr_text = processor.batch_decode(predicted_ids, skip_special_tokens=True)[0].strip()
    if not asr_text:
        raise RuntimeError("ASR transcript is empty")

    tokenizer.src_lang = args.source_lang
    mt_inputs = tokenizer(asr_text, return_tensors="pt")
    mt_inputs = {key: value.to(mt_model.device) for key, value in mt_inputs.items()}
    forced_bos_token_id = tokenizer.convert_tokens_to_ids(args.target_lang)
    generated_tokens = mt_model.generate(
        **mt_inputs,
        forced_bos_token_id=forced_bos_token_id,
        max_new_tokens=256,
    )
    translation_text = tokenizer.batch_decode(
        generated_tokens,
        skip_special_tokens=True,
    )[0].strip()
    if not translation_text:
        raise RuntimeError("Translation text is empty")

    payload = {
        "text": translation_text,
        "translation_text": translation_text,
        "asr_text": asr_text,
        "source_lang": args.source_lang,
        "target_lang": args.target_lang,
        "segments": [],
        "error_code": None,
    }
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
