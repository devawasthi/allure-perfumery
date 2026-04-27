from __future__ import annotations

from html import escape


def build_fragrance_artwork(fragrance: dict) -> bytes:
    top_note = fragrance["top_notes"][0] if fragrance["top_notes"] else fragrance["family"].title()
    base_note = fragrance["base_notes"][0] if fragrance["base_notes"] else fragrance["origin"]
    sale_line = " / ".join(item.title() for item in fragrance.get("sale_types", [])) or "Retail"
    starting_price = fragrance.get("starting_price", 0)
    price_line = f"From INR {starting_price:,}"

    brand = escape(fragrance["brand"])
    name = escape(fragrance["name"])
    family = escape(fragrance["family"].title())
    concentration = escape(fragrance["concentration"])
    top = escape(top_note)
    base = escape(base_note)
    sale_text = escape(sale_line)
    price_text = escape(price_line)
    accent_from = escape(fragrance["accent_from"])
    accent_to = escape(fragrance["accent_to"])

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 880" role="img" aria-label="{brand} {name}">
  <defs>
    <linearGradient id="bg" x1="0%" x2="100%" y1="0%" y2="100%">
      <stop offset="0%" stop-color="{accent_from}" />
      <stop offset="100%" stop-color="{accent_to}" />
    </linearGradient>
    <linearGradient id="glass" x1="0%" x2="0%" y1="0%" y2="100%">
      <stop offset="0%" stop-color="rgba(255,255,255,0.34)" />
      <stop offset="100%" stop-color="rgba(255,255,255,0.06)" />
    </linearGradient>
  </defs>
  <rect width="720" height="880" rx="48" fill="url(#bg)" />
  <rect x="30" y="30" width="660" height="820" rx="36" fill="rgba(12,10,10,0.18)" stroke="rgba(255,245,233,0.22)" />
  <circle cx="578" cy="128" r="108" fill="rgba(255,255,255,0.09)" />
  <circle cx="166" cy="754" r="138" fill="rgba(255,255,255,0.08)" />
  <rect x="270" y="116" width="180" height="84" rx="24" fill="rgba(17,13,12,0.42)" stroke="rgba(255,241,224,0.18)" />
  <rect x="214" y="196" width="292" height="456" rx="68" fill="rgba(249,244,236,0.15)" stroke="rgba(255,250,244,0.34)" />
  <rect x="268" y="224" width="184" height="52" rx="20" fill="rgba(23,18,17,0.7)" />
  <rect x="256" y="280" width="208" height="224" rx="28" fill="rgba(252,248,243,0.18)" stroke="rgba(255,255,255,0.25)" />
  <rect x="234" y="536" width="252" height="78" rx="24" fill="rgba(23,18,17,0.62)" stroke="rgba(255,248,239,0.18)" />
  <text x="80" y="126" fill="#F9EBDD" font-family="Manrope, Arial, sans-serif" font-size="50" font-weight="800">{name}</text>
  <text x="82" y="180" fill="rgba(255,245,233,0.9)" font-family="Manrope, Arial, sans-serif" font-size="26" letter-spacing="6">{brand.upper()}</text>
  <text x="324" y="166" fill="rgba(255,241,231,0.82)" font-family="Manrope, Arial, sans-serif" font-size="18" letter-spacing="5">SIGNATURE</text>
  <text x="360" y="428" fill="rgba(255,255,255,0.92)" text-anchor="middle" font-family="Manrope, Arial, sans-serif" font-size="36" font-weight="800">{brand}</text>
  <text x="360" y="580" fill="rgba(255,245,234,0.94)" text-anchor="middle" font-family="Manrope, Arial, sans-serif" font-size="18" letter-spacing="4">{family.upper()}</text>
  <text x="80" y="706" fill="rgba(255,246,236,0.9)" font-family="Manrope, Arial, sans-serif" font-size="18" letter-spacing="4">TOP NOTE</text>
  <text x="80" y="744" fill="#FFF6EC" font-family="Manrope, Arial, sans-serif" font-size="30" font-weight="700">{top}</text>
  <text x="80" y="802" fill="rgba(255,246,236,0.9)" font-family="Manrope, Arial, sans-serif" font-size="18" letter-spacing="4">BASE NOTE</text>
  <text x="80" y="840" fill="#FFF6EC" font-family="Manrope, Arial, sans-serif" font-size="28" font-weight="700">{base}</text>
  <text x="462" y="706" fill="rgba(255,246,236,0.9)" font-family="Manrope, Arial, sans-serif" font-size="18" letter-spacing="4">FORMAT</text>
  <text x="462" y="744" fill="#FFF6EC" font-family="Manrope, Arial, sans-serif" font-size="24" font-weight="700">{sale_text}</text>
  <text x="462" y="802" fill="rgba(255,246,236,0.9)" font-family="Manrope, Arial, sans-serif" font-size="18" letter-spacing="4">STARTING AT</text>
  <text x="462" y="840" fill="#FFF6EC" font-family="Manrope, Arial, sans-serif" font-size="28" font-weight="700">{price_text}</text>
  <text x="80" y="96" fill="rgba(255,255,255,0.12)" font-family="Manrope, Arial, sans-serif" font-size="20" letter-spacing="8">{concentration.upper()}</text>
</svg>"""
    return svg.encode("utf-8")
