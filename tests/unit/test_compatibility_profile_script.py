from __future__ import annotations

import json
from pathlib import Path

from app.services.pipeline import _compatibility_input_fingerprint
from scripts.prepare_compatibility_profile import prepare_profile


def test_private_profile_fingerprint_matches_pipeline_contract(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "private_templates"
    paths: dict[str, Path] = {}
    for role in (
        "ghe",
        "psychosocial_raw",
        "ergo_raw",
        "cnpj_card",
        "technical_integrated",
    ):
        path = tmp_path / f"{role}.synthetic"
        path.write_bytes(f"SYNTHETIC-{role}".encode("ascii"))
        paths[role] = path

    output = private_root / "perfil.json"
    prepare_profile(
        ghe=paths["ghe"],
        psychosocial=paths["psychosocial_raw"],
        ergo=paths["ergo_raw"],
        registration_card=paths["cnpj_card"],
        integrated=paths["technical_integrated"],
        psychosocial_analysis=None,
        ergonomic_analysis=None,
        included=[1, 2, 3],
        omitted=[4],
        output=output,
        private_root=private_root,
    )

    profile = json.loads(output.read_text(encoding="utf-8"))
    assert profile["input_fingerprint"] == _compatibility_input_fingerprint(
        paths,
        "integrated",
    )
    assert profile["included_ergo_ordinals"] == [1, 2, 3]
    assert profile["omitted_ergo_ordinals"] == [4]
    serialized = output.read_text(encoding="utf-8")
    assert "SYNTHETIC-" not in serialized
    assert str(tmp_path) not in serialized
