"""The design system's carrier palette, checked mechanically.

`specs/ux/design-system.md` § Tokens is what `web/src/tokens.css` copies verbatim, so a
hand-edited hex there ships straight to the page with nobody looking at it. These tests are
the executable half of § Carrier palette: they re-run the `dataviz` skill's colour checks
(`scripts/validate_palette.js`, thresholds and Machado matrices vendored below, since the
skill lives outside the repo) against the tokens as written in the spec.

The maths is duplicated rather than imported for exactly one reason: the skill's directory
is not part of this repo and is not on the box between sessions. Keep the constants in
lockstep with it if the skill's thresholds ever move.
"""

import math
import re
from pathlib import Path

import pytest

SPECS = Path(__file__).resolve().parents[1] / "specs"
DESIGN_SYSTEM = SPECS / "ux" / "design-system.md"
BACKLOG = SPECS / "backlog.md"

# -- thresholds, from the dataviz skill's validate_palette.js -----------------------------
LIGHTNESS_BAND_DARK = (0.48, 0.67)  # OKLCH L
CHROMA_FLOOR = 0.10  # OKLCH C
CVD_TARGET = 8.0  # OKLab ΔE×100, min(protan, deutan)
NORMAL_FLOOR = 15.0  # OKLab ΔE×100, unsimulated vision
CONTRAST_MIN = 3.0  # WCAG vs the surface

# Machado, Oliveira & Fernandes (2009) at severity 1.0, applied in linear RGB. The
# thresholds above are calibrated to this simulation, so the model is part of the standard.
MACHADO = {
    "protan": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deutan": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
}

# The token whose colour a carrier line is drawn on: the fare card's ground.
SURFACE_TOKEN = "--color-card"

# How far a carrier must stay from each role token, per § The rule, by rendering channel.
# Only the thresholds are literals here — the colours themselves are read out of the spec,
# so moving `--sev-high` or `--band-french` is checked against the carriers too.
ROLE_SEPARATION = {
    "--color-accent": 15.0,
    "--sev-high": 10.0,
    "--sev-med": 10.0,
    "--band-wedding": 10.0,
    "--band-religious": 10.0,
    "--band-french": 10.0,
    "--color-ok": 10.0,
}

CARRIER_CODES = ("PK", "QR", "EK", "GF", "TK", "SV")


# -- colour maths --------------------------------------------------------------------------
def _srgb(hex_colour: str) -> tuple[float, float, float]:
    h = hex_colour.strip().lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return r, g, b


def _linear(hex_colour: str) -> tuple[float, float, float]:
    def to_linear(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = _srgb(hex_colour)
    return to_linear(r), to_linear(g), to_linear(b)


def _oklab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = rgb
    long_ = math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    med = math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    short = math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (
        0.2104542553 * long_ + 0.7936177850 * med - 0.0040720468 * short,
        1.9779984951 * long_ - 2.4285922050 * med + 0.4505937099 * short,
        0.0259040371 * long_ + 0.7827717662 * med - 0.8086757660 * short,
    )


def _oklch(hex_colour: str) -> tuple[float, float]:
    """(L, C) — the lightness-band and chroma-floor checks."""
    lightness, a, b = _oklab(_linear(hex_colour))
    return lightness, math.hypot(a, b)


def _simulate(hex_colour: str, kind: str) -> tuple[float, float, float]:
    r, g, b = _linear(hex_colour)
    rows = MACHADO[kind]
    return tuple(  # type: ignore[return-value]
        min(1.0, max(0.0, row[0] * r + row[1] * g + row[2] * b)) for row in rows
    )


def delta_e(first: str, second: str, kind: str | None = None) -> float:
    """Euclidean distance in OKLab ×100. `kind` None means unsimulated vision."""
    a = _oklab(_simulate(first, kind) if kind else _linear(first))
    b = _oklab(_simulate(second, kind) if kind else _linear(second))
    return 100 * math.dist(a, b)


def contrast(first: str, second: str) -> float:
    def luminance(colour: str) -> float:
        r, g, b = _linear(colour)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# -- the spec, as data ---------------------------------------------------------------------
@pytest.fixture(scope="module")
def design_system() -> str:
    return DESIGN_SYSTEM.read_text()


@pytest.fixture(scope="module")
def tokens(design_system: str) -> dict[str, str]:
    """Every `--name: #hex` in § Tokens. `--sev-low` and `--text-muted-*` are `rgba()` and
    so fall out; nothing compares against them."""
    block = design_system.split("## Tokens", 1)[1].split("\n## ", 1)[0]
    return dict(re.findall(r"(--[a-zA-Z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;", block))


@pytest.fixture(scope="module")
def carriers(tokens: dict[str, str]) -> dict[str, str]:
    """The `--carrier-XX` tokens, keyed by IATA code."""
    return {
        name[len("--carrier-") :]: value
        for name, value in tokens.items()
        if name.startswith("--carrier-")
    }


@pytest.fixture(scope="module")
def surface(tokens: dict[str, str]) -> str:
    return tokens[SURFACE_TOKEN]


@pytest.fixture(scope="module")
def roles(tokens: dict[str, str]) -> dict[str, tuple[str, float]]:
    """Role token -> (colour as the spec has it, its separation floor)."""
    return {name: (tokens[name], floor) for name, floor in ROLE_SEPARATION.items()}


def test_six_carrier_tokens_with_distinct_values(carriers: dict[str, str]) -> None:
    assert set(carriers) == set(CARRIER_CODES)
    assert len(set(carriers.values())) == 6, f"duplicate carrier hex: {carriers}"


def test_no_carrier_equals_a_band_or_severity_colour(
    carriers: dict[str, str], roles: dict[str, tuple[str, float]]
) -> None:
    """The literal collisions S13 exists to remove: Emirates *was* --sev-high and Gulf Air
    *was* --band-wedding and --sev-med, byte for byte."""
    for code, colour in carriers.items():
        for role, (role_colour, _) in roles.items():
            assert colour != role_colour, f"--carrier-{code} is the same colour as {role}"


def test_every_carrier_sits_in_the_dark_lightness_band(carriers: dict[str, str]) -> None:
    low, high = LIGHTNESS_BAND_DARK
    for code, colour in carriers.items():
        lightness, _ = _oklch(colour)
        assert low <= lightness <= high, f"--carrier-{code} {colour} L={lightness:.3f}"


def test_every_carrier_clears_the_chroma_floor(carriers: dict[str, str]) -> None:
    """Below the floor a hue reads as grey and stops doing identity work."""
    for code, colour in carriers.items():
        _, chroma = _oklch(colour)
        assert chroma >= CHROMA_FLOOR, f"--carrier-{code} {colour} C={chroma:.3f}"


def test_every_carrier_clears_contrast_against_the_card(
    carriers: dict[str, str], surface: str
) -> None:
    for code, colour in carriers.items():
        ratio = contrast(colour, surface)
        assert ratio >= CONTRAST_MIN, f"--carrier-{code} {colour} is {ratio:.2f}:1 on {surface}"


def test_carriers_are_colour_blind_safe_on_the_all_pairs_list(carriers: dict[str, str]) -> None:
    """All pairs, not adjacent: six overlapping paths in one plot and six dots in the
    efficiency scatter both put arbitrary pairs side by side."""
    codes = sorted(carriers)
    worst = min(
        (min(delta_e(carriers[a], carriers[b], k) for k in ("protan", "deutan")), a, b)
        for i, a in enumerate(codes)
        for b in codes[i + 1 :]
    )
    assert worst[0] >= CVD_TARGET, f"{worst[1]}↔{worst[2]} ΔE {worst[0]:.1f} under protan/deutan"


def test_carriers_clear_the_normal_vision_floor(carriers: dict[str, str]) -> None:
    """A hard gate in the skill: secondary encoding does not excuse it."""
    codes = sorted(carriers)
    worst = min(
        (delta_e(carriers[a], carriers[b]), a, b)
        for i, a in enumerate(codes)
        for b in codes[i + 1 :]
    )
    assert worst[0] >= NORMAL_FLOOR, f"{worst[1]}↔{worst[2]} ΔE {worst[0]:.1f} under normal vision"


@pytest.mark.parametrize("role", list(ROLE_SEPARATION))
def test_carriers_stay_clear_of_each_role_colour(
    carriers: dict[str, str], roles: dict[str, tuple[str, float]], role: str
) -> None:
    role_colour, floor = roles[role]
    for code, colour in carriers.items():
        gap = delta_e(colour, role_colour)
        assert gap >= floor, f"--carrier-{code} {colour} is ΔE {gap:.1f} from {role} {role_colour}"


def test_every_carrier_has_a_one_line_rationale(
    design_system: str, carriers: dict[str, str]
) -> None:
    """§ Per-carrier rationale — a value with no reason is one nobody dares change."""
    table = design_system.split("### Per-carrier rationale", 1)[1].split("###", 1)[0]
    for code, colour in carriers.items():
        row = next((r for r in table.splitlines() if f"`--carrier-{code}`" in r), None)
        assert row is not None, f"--carrier-{code} has no rationale row"
        assert colour in row, f"--carrier-{code}'s rationale row does not name {colour}"
        # Cell three is the reason; a bare "moved" is not one.
        assert len(row.split("|")[3].strip()) > 40, f"--carrier-{code}'s reason is too thin"


def test_the_validator_invocation_is_recorded(
    design_system: str, carriers: dict[str, str], surface: str
) -> None:
    """The spec quotes the command a reader re-runs; it has to name the shipped values."""
    section = design_system.split("## Carrier palette", 1)[1]
    invocation = section.split("```", 2)[1]
    assert "validate_palette.js" in invocation
    assert "--pairs all" in invocation
    assert surface in invocation
    for colour in carriers.values():
        assert colour in invocation, f"{colour} is not in the recorded validator run"


def test_phone_behaviour_is_specified(design_system: str) -> None:
    """The design wires only mouse events; S13 owes S15 the touch story for all three."""
    assert "### Phone behaviour" in design_system
    section = design_system.split("### Phone behaviour", 1)[1].split("\n## ", 1)[0]
    for subject in ("tap-to-stick", "Event drawer", "Filter bar overflow"):
        assert subject in section, f"phone behaviour says nothing about {subject}"
    assert "pointerType" in section, "the mouse/touch branch is the whole mechanism"
    assert "Escape" in section, "a stuck hairline and a modal drawer both need a way out"


def test_s15_carries_the_states_the_design_does_not_draw() -> None:
    backlog = BACKLOG.read_text()
    story = backlog.split("## S15 ", 1)[1].split("\n## ", 1)[0]
    for criterion in (
        "Stale-data banner",
        '"No data yet" empty state',
        '"No data for this combination" empty state',
        "Per-region empty states",
    ):
        assert criterion in story, f"S15 has no acceptance criterion for {criterion}"
    assert "36 h" in story, "the stale threshold has to be a number, not a feeling"
