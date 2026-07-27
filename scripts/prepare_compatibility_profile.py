"""Create an ignored, input-bound compatibility profile for a private pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


def _fingerprint(
    sources: list[tuple[str, Path]],
    analysis_mode: str,
) -> str:
    canonical_sources: list[dict[str, str]] = []
    for role, path in sources:
        if not path.is_file():
            raise FileNotFoundError(f"Arquivo obrigatório ausente para o papel {role}.")
        canonical_sources.append(
            {
                "role": role,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    canonical = json.dumps(
        {
            "analysis_mode": analysis_mode,
            "sources": canonical_sources,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def _ordinals(values: list[int], label: str) -> list[int]:
    if not values or any(value < 1 for value in values):
        raise ValueError(f"{label} deve conter ordinais positivos.")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} contém ordinais repetidos.")
    return values


def prepare_profile(
    *,
    ghe: Path,
    psychosocial: Path,
    ergo: Path,
    registration_card: Path,
    integrated: Path | None,
    psychosocial_analysis: Path | None,
    ergonomic_analysis: Path | None,
    included: list[int],
    omitted: list[int],
    output: Path,
    private_root: Path | None = None,
) -> Path:
    if integrated is not None:
        if psychosocial_analysis is not None or ergonomic_analysis is not None:
            raise ValueError("Escolha relatório integrado ou análises separadas.")
        analysis_mode = "integrated"
        technical_sources = [("technical_integrated", integrated)]
    else:
        if psychosocial_analysis is None or ergonomic_analysis is None:
            raise ValueError("As duas análises separadas são obrigatórias.")
        analysis_mode = "separate"
        technical_sources = [
            ("psychosocial_analysis", psychosocial_analysis),
            ("ergonomic_analysis", ergonomic_analysis),
        ]

    included = _ordinals(included, "included")
    omitted = _ordinals(omitted, "omitted")
    if set(included) & set(omitted):
        raise ValueError("Um bloco Ergo não pode ser incluído e omitido.")
    complete = sorted([*included, *omitted])
    if complete != list(range(1, len(complete) + 1)):
        raise ValueError("Os ordinais devem cobrir todos os blocos Ergo sem lacunas.")

    project_root = Path(__file__).resolve().parents[1]
    allowed_private_root = (
        private_root.resolve()
        if private_root is not None
        else (project_root / "private_templates").resolve()
    )
    destination = output.resolve()
    if allowed_private_root not in destination.parents:
        raise ValueError("O perfil deve permanecer dentro de private_templates/.")
    destination.parent.mkdir(parents=True, exist_ok=True)

    sources = [
        ("ghe", ghe),
        ("psychosocial_raw", psychosocial),
        ("ergo_raw", ergo),
        ("cnpj_card", registration_card),
        *technical_sources,
    ]
    payload = {
        "schema_version": 1,
        "mode": "pilot_reference",
        "analysis_mode": analysis_mode,
        "input_fingerprint": _fingerprint(sources, analysis_mode),
        "included_ergo_ordinals": included,
        "omitted_ergo_ordinals": omitted,
        "created_at": datetime.now(UTC).isoformat(),
    }
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ghe", required=True, type=Path)
    parser.add_argument("--psychosocial", required=True, type=Path)
    parser.add_argument("--ergo", required=True, type=Path)
    parser.add_argument("--registration-card", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--integrated", type=Path)
    mode.add_argument("--psychosocial-analysis", type=Path)
    parser.add_argument("--ergonomic-analysis", type=Path)
    parser.add_argument("--include-ergo", required=True, nargs="+", type=int)
    parser.add_argument("--omit-ergo", required=True, nargs="+", type=int)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("private_templates/aep_compatibility_profile.json"),
    )
    args = parser.parse_args()
    output = prepare_profile(
        ghe=args.ghe,
        psychosocial=args.psychosocial,
        ergo=args.ergo,
        registration_card=args.registration_card,
        integrated=args.integrated,
        psychosocial_analysis=args.psychosocial_analysis,
        ergonomic_analysis=args.ergonomic_analysis,
        included=args.include_ergo,
        omitted=args.omit_ergo,
        output=args.output,
    )
    print(f"Perfil privado criado: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
