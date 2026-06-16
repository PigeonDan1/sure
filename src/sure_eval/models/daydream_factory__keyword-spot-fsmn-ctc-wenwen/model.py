"""WekWS streaming KWS wrapper for SURE local validation."""

from __future__ import annotations

import contextlib
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import librosa
import numpy as np
import torch
import torchaudio.compliance.kaldi as kaldi
import yaml


TARGET_SAMPLE_RATE = 16000
DEFAULT_KEYWORDS = "你好问问,嗨小问"


@dataclass
class KWSResult:
    """Normalized keyword spotting result."""

    detected: bool
    keyword: str | None
    score: float | None
    start: float | None
    end: float | None
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@contextlib.contextmanager
def _pushd(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class WeKWSModel:
    """Thin adapter around WekWS ``KeyWordSpotter``.

    The ModelScope checkpoint keeps ``config.yaml`` references relative to the
    ModelScope repo directory, so model initialization temporarily runs from the
    cache parent directory.
    """

    def __init__(
        self,
        model_dir: str | Path | None = None,
        keywords: str = DEFAULT_KEYWORDS,
        threshold: float = 0.0,
        gpu: int = -1,
        chunk_seconds: float = 0.3,
    ) -> None:
        self.wrapper_dir = Path(__file__).resolve().parent
        self.model_dir = Path(model_dir) if model_dir else self._default_model_dir()
        self.keywords = keywords
        self.threshold = threshold
        self.gpu = gpu
        self.chunk_seconds = chunk_seconds
        self._spotter = None
        self._offline_model = None
        self._offline_config: dict[str, Any] | None = None
        self._offline_device = None
        self._keyword_tokens: dict[str, list[int]] | None = None

    def _default_model_dir(self) -> Path:
        return (
            self.wrapper_dir
            / ".runtime"
            / "modelscope_cache"
            / "daydream-factory"
            / "keyword-spot-fsmn-ctc-wenwen"
        )

    def _ensure_paths(self) -> None:
        required = [
            self.model_dir / "avg_30.pt",
            self.model_dir / "config.yaml",
            self.model_dir / "tokens.txt",
            self.model_dir / "lexicon.txt",
        ]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise FileNotFoundError(
                "Missing WekWS ModelScope files: " + ", ".join(missing)
            )

        wekws_root = self.wrapper_dir / ".runtime" / "source" / "wekws"
        if not wekws_root.exists():
            raise FileNotFoundError(f"Missing WekWS source tree: {wekws_root}")
        for path in (wekws_root, wekws_root / "wekws", wekws_root / "tools"):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))

    def load(self) -> None:
        if self._spotter is not None:
            return

        self._ensure_paths()
        from wekws.bin.stream_kws_ctc import KeyWordSpotter

        with _pushd(self.model_dir.parent):
            self._spotter = KeyWordSpotter(
                ckpt_path=str(self.model_dir / "avg_30.pt"),
                config_path=str(self.model_dir / "config.yaml"),
                token_path=str(self.model_dir / "tokens.txt"),
                lexicon_path=str(self.model_dir / "lexicon.txt"),
                threshold=self.threshold,
                min_frames=5,
                max_frames=250,
                interval_frames=50,
                score_beam=3,
                path_beam=20,
                gpu=self.gpu,
                is_jit_model=False,
            )
        self._spotter.set_keywords(self.keywords)

    def _load_offline(self) -> None:
        if self._offline_model is not None:
            return

        self._ensure_paths()
        from tools.make_list import read_lexicon, read_token, query_token_set
        from wekws.model.kws_model import init_model
        from wekws.utils.checkpoint import load_checkpoint

        with (self.model_dir / "config.yaml").open("r", encoding="utf-8") as fin:
            configs = yaml.load(fin, Loader=yaml.FullLoader)

        with _pushd(self.model_dir.parent):
            model = init_model(configs["model"])
            load_checkpoint(model, str(self.model_dir / "avg_30.pt"))

        use_cuda = self.gpu >= 0 and torch.cuda.is_available()
        device = torch.device("cuda" if use_cuda else "cpu")
        model = model.to(device)
        model.eval()

        token_table = read_token(self.model_dir / "tokens.txt")
        lexicon_table = read_lexicon(self.model_dir / "lexicon.txt")
        keyword_tokens: dict[str, list[int]] = {}
        for keyword in self.keywords.strip().replace(" ", "").split(","):
            if not keyword:
                continue
            _, indexes = query_token_set(keyword, token_table, lexicon_table)
            keyword_tokens[keyword] = list(indexes)

        self._offline_model = model
        self._offline_config = configs
        self._offline_device = device
        self._keyword_tokens = keyword_tokens

    def _extract_offline_features(self, audio_path: str | Path) -> torch.Tensor:
        assert self._offline_config is not None

        wav, _ = librosa.load(str(audio_path), sr=TARGET_SAMPLE_RATE, mono=True)
        waveform = torch.from_numpy(wav.astype(np.float32)).unsqueeze(0)
        waveform = waveform * (1 << 15)

        dataset_conf = self._offline_config["dataset_conf"]
        fbank_conf = dataset_conf["feature_extraction_conf"]
        feats = kaldi.fbank(
            waveform,
            num_mel_bins=fbank_conf["num_mel_bins"],
            frame_length=fbank_conf["frame_length"],
            frame_shift=fbank_conf["frame_shift"],
            dither=0.0,
            energy_floor=0.0,
            sample_frequency=TARGET_SAMPLE_RATE,
        )

        if dataset_conf.get("context_expansion", False):
            left = dataset_conf["context_expansion_conf"]["left"]
            right = dataset_conf["context_expansion_conf"]["right"]
            index = 0
            ctx_dim = feats.shape[0]
            ctx_frm = feats.shape[1] * (left + right + 1)
            feats_ctx = torch.zeros(ctx_dim, ctx_frm, dtype=torch.float32)
            for lag in range(-left, right + 1):
                feats_ctx[:, index : index + feats.shape[1]] = torch.roll(
                    feats, -lag, 0
                )
                index += feats.shape[1]

            for idx in range(left):
                for cpx in range(left - idx):
                    start = cpx * feats.shape[1]
                    end = (cpx + 1) * feats.shape[1]
                    feats_ctx[idx, start:end] = feats_ctx[left, : feats.shape[1]]
            feats = feats_ctx[: feats_ctx.shape[0] - right]

        frame_skip = dataset_conf.get("frame_skip", 1)
        if frame_skip > 1:
            feats = feats[::frame_skip, :]
        return feats.unsqueeze(0)

    @staticmethod
    def _is_sublist(main_list: tuple[int, ...], check_list: list[int]) -> int:
        if len(main_list) < len(check_list):
            return -1
        for idx in range(len(main_list) - len(check_list) + 1):
            if list(main_list[idx : idx + len(check_list)]) == check_list:
                return idx
        return -1

    def predict(self, audio_path: str | Path) -> KWSResult:
        """Run offline CTC prefix beam search on the whole utterance."""
        self._load_offline()
        assert self._offline_model is not None
        assert self._offline_device is not None
        assert self._keyword_tokens is not None

        from wekws.model.loss import ctc_prefix_beam_search

        feats = self._extract_offline_features(audio_path).to(self._offline_device)
        with torch.no_grad():
            logits, _ = self._offline_model(feats)
            probs = logits.softmax(2)[0].cpu()

        keyword_idxset = {0}
        for indexes in self._keyword_tokens.values():
            keyword_idxset.update(indexes)

        hyps = ctc_prefix_beam_search(
            probs,
            probs.shape[0],
            keyword_idxset,
            score_beam_size=3,
            path_beam_size=20,
        )

        for prefix_ids, _, prefix_nodes in hyps:
            for keyword, indexes in self._keyword_tokens.items():
                offset = self._is_sublist(prefix_ids, indexes)
                if offset == -1:
                    continue
                start_frame = prefix_nodes[offset]["frame"]
                end_frame = prefix_nodes[offset + len(indexes) - 1]["frame"]
                score = 1.0
                for idx in range(offset, offset + len(indexes)):
                    score *= prefix_nodes[idx]["prob"]
                score = float(np.sqrt(score))
                detected = score >= self.threshold
                return KWSResult(
                    detected=detected,
                    keyword=keyword if detected else None,
                    score=score,
                    start=float(start_frame * 0.03),
                    end=float(end_frame * 0.03),
                    raw={
                        "state": 1 if detected else 0,
                        "keyword": keyword,
                        "start": float(start_frame * 0.03),
                        "end": float(end_frame * 0.03),
                        "score": score,
                        "decoder": "offline_ctc_prefix_beam_search",
                        "prefix_ids": list(prefix_ids),
                    },
                )

        return KWSResult(
            detected=False,
            keyword=None,
            score=None,
            start=None,
            end=None,
            raw={"state": 0, "decoder": "offline_ctc_prefix_beam_search"},
        )

    def predict_streaming(self, audio_path: str | Path) -> KWSResult:
        self.load()
        assert self._spotter is not None

        wav, _ = librosa.load(str(audio_path), sr=TARGET_SAMPLE_RATE, mono=True)
        wav = np.clip(wav, -1.0, 1.0)
        pcm = (wav * (1 << 15)).astype("<i2").tobytes()
        chunk_size = max(1, int(self.chunk_seconds * TARGET_SAMPLE_RATE) * 2)

        self._spotter.reset_all()
        final: dict[str, Any] = {}
        for offset in range(0, len(pcm), chunk_size):
            chunk = pcm[offset : offset + chunk_size]
            if not chunk:
                continue
            result = self._spotter.forward(chunk)
            if result:
                final = dict(result)
            if result.get("state") == 1:
                break

        detected = final.get("state") == 1
        return KWSResult(
            detected=detected,
            keyword=final.get("keyword") if detected else None,
            score=final.get("score") if detected else None,
            start=final.get("start") if detected else None,
            end=final.get("end") if detected else None,
            raw=final,
        )
