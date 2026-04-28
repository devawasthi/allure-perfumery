from __future__ import annotations

import hashlib
import random
from html import escape


def _rng_for(fragrance: dict) -> random.Random:
    seed = hashlib.sha256(fragrance["slug"].encode("utf-8")).hexdigest()
    return random.Random(int(seed[:12], 16))


def _particle_marks(rng: random.Random, count: int = 28) -> str:
    marks = []
    for _ in range(count):
        x = rng.randint(42, 678)
        y = rng.randint(52, 560)
        angle = rng.randint(-34, 34)
        length = rng.randint(8, 22)
        opacity = rng.uniform(0.28, 0.72)
        marks.append(
            f'<line x1="{x}" y1="{y}" x2="{x + length}" y2="{y - length // 2}" '
            f'stroke="#f4b86d" stroke-width="{rng.uniform(2.0, 4.2):.1f}" '
            f'stroke-linecap="round" opacity="{opacity:.2f}" '
            f'transform="rotate({angle} {x} {y})" />'
        )
    return "\n    ".join(marks)


def _smoke_paths(rng: random.Random, count: int = 7) -> str:
    paths = []
    for index in range(count):
        y = 110 + index * 56 + rng.randint(-20, 20)
        start_x = rng.randint(-80, 80)
        control_1 = rng.randint(130, 260)
        control_2 = rng.randint(420, 560)
        end_x = rng.randint(650, 810)
        opacity = 0.08 + index * 0.012
        width = rng.randint(18, 36)
        paths.append(
            f'<path d="M {start_x} {y} C {control_1} {y - 120}, {control_2} {y + 120}, {end_x} {y - 18}" '
            f'fill="none" stroke="url(#smoke)" stroke-width="{width}" '
            f'stroke-linecap="round" opacity="{opacity:.2f}" filter="url(#softBlur)" />'
        )
    return "\n    ".join(paths)


def build_fragrance_artwork(fragrance: dict) -> bytes:
    rng = _rng_for(fragrance)
    brand = escape(fragrance["brand"])
    name = escape(fragrance["name"])
    bottle_width = rng.randint(206, 252)
    bottle_x = 360 - bottle_width // 2
    bottle_y = rng.randint(214, 246)
    bottle_height = rng.randint(286, 334)
    cap_width = rng.randint(102, 132)
    cap_x = 360 - cap_width // 2

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 880" role="img" aria-label="{brand} {name}">
  <defs>
    <linearGradient id="bg" x1="0%" x2="100%" y1="0%" y2="100%">
      <stop offset="0%" stop-color="#f1ede6" />
      <stop offset="58%" stop-color="#d9d2c8" />
      <stop offset="100%" stop-color="#a79f94" />
    </linearGradient>
    <radialGradient id="halo" cx="50%" cy="28%" r="58%">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0.62" />
      <stop offset="62%" stop-color="#f1ede6" stop-opacity="0.18" />
      <stop offset="100%" stop-color="#5a534b" stop-opacity="0" />
    </radialGradient>
    <linearGradient id="smoke" x1="0%" x2="100%">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0" />
      <stop offset="48%" stop-color="#ffffff" stop-opacity="0.48" />
      <stop offset="100%" stop-color="#ffffff" stop-opacity="0" />
    </linearGradient>
    <linearGradient id="bottle" x1="18%" x2="92%" y1="0%" y2="100%">
      <stop offset="0%" stop-color="#eff8ff" stop-opacity="0.26" />
      <stop offset="36%" stop-color="#1a2830" stop-opacity="0.72" />
      <stop offset="100%" stop-color="#03060b" stop-opacity="0.96" />
    </linearGradient>
    <linearGradient id="platform" x1="0%" x2="100%">
      <stop offset="0%" stop-color="#c8bfb4" />
      <stop offset="54%" stop-color="#ede7df" />
      <stop offset="100%" stop-color="#9b9287" />
    </linearGradient>
    <filter id="softBlur">
      <feGaussianBlur stdDeviation="9" />
    </filter>
    <filter id="deepShadow">
      <feDropShadow dx="0" dy="24" stdDeviation="22" flood-color="#000000" flood-opacity="0.45" />
    </filter>
  </defs>
  <rect width="720" height="880" rx="48" fill="url(#bg)" />
  <rect width="720" height="880" rx="48" fill="url(#halo)" />
  <rect x="34" y="34" width="652" height="812" rx="42" fill="rgba(255,255,255,0.1)" stroke="rgba(70,62,54,0.12)" />
  <g opacity="0.95">
    {_smoke_paths(rng)}
  </g>
  <g>
    {_particle_marks(rng)}
  </g>
  <ellipse cx="360" cy="687" rx="242" ry="46" fill="#3e3933" opacity="0.22" filter="url(#softBlur)" />
  <rect x="150" y="658" width="420" height="118" rx="16" fill="url(#platform)" opacity="0.9" />
  <rect x="{cap_x}" y="{bottle_y - 96}" width="{cap_width}" height="90" rx="18" fill="#07101a" stroke="rgba(255,255,255,0.22)" filter="url(#deepShadow)" />
  <rect x="{cap_x + 22}" y="{bottle_y - 26}" width="{cap_width - 44}" height="34" rx="10" fill="#05080d" stroke="rgba(255,255,255,0.16)" />
  <rect x="{bottle_x}" y="{bottle_y}" width="{bottle_width}" height="{bottle_height}" rx="34" fill="url(#bottle)" stroke="rgba(255,255,255,0.3)" filter="url(#deepShadow)" />
  <rect x="{bottle_x + 22}" y="{bottle_y + 24}" width="{bottle_width - 44}" height="{bottle_height - 58}" rx="26" fill="rgba(255,255,255,0.05)" stroke="rgba(255,255,255,0.08)" />
  <path d="M {bottle_x + 42} {bottle_y + 26} C {bottle_x + 18} {bottle_y + 156}, {bottle_x + 76} {bottle_y + bottle_height - 44}, {bottle_x + 46} {bottle_y + bottle_height - 24}" fill="none" stroke="#ffffff" stroke-opacity="0.22" stroke-width="5" />
  <path d="M {bottle_x + bottle_width - 54} {bottle_y + 18} C {bottle_x + bottle_width - 20} {bottle_y + 130}, {bottle_x + bottle_width - 92} {bottle_y + bottle_height - 84}, {bottle_x + bottle_width - 42} {bottle_y + bottle_height - 30}" fill="none" stroke="#ffffff" stroke-opacity="0.12" stroke-width="4" />
  <circle cx="{rng.randint(468, 552)}" cy="{rng.randint(560, 640)}" r="{rng.randint(26, 44)}" fill="#7a4424" opacity="0.76" />
  <path d="M 492 606 C 548 578, 602 594, 650 550" fill="none" stroke="#d0a47a" stroke-width="8" stroke-linecap="round" opacity="0.62" />
  <path d="M 518 632 C 584 612, 620 640, 676 608" fill="none" stroke="#b78864" stroke-width="6" stroke-linecap="round" opacity="0.5" />
</svg>"""
    return svg.encode("utf-8")
