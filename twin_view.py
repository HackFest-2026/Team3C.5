"""Schematic, latest-value digital-twin view for the MineGuard prototype.

This intentionally visualizes assigned zones and reported nodes only. It is not
surveyed geometry, live worker tracking, a physics model, or a safety assessment.
"""
from html import escape


COLORS = {
    "worker": "#60a5fa",
    "mine": "#fbbf24",
    "recent": "#34d399",
    "stale": "#fb7185",
    "unknown": "#94a3b8",
}


def render_twin_svg(nodes):
    nodes = [node for node in (nodes or []) if isinstance(node, dict)]
    zones = sorted({str(node.get("zone_id") or "unassigned") for node in nodes}) or ["unassigned"]
    zone_index = {zone: i for i, zone in enumerate(zones)}
    zone_x = {zone: 260 + i * 220 for zone, i in zone_index.items()}
    width = max(780, 260 + max(0, len(zones) - 1) * 220 + 194 + 40)
    height = 420

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="420" viewBox="0 0 {width} {height}" role="img" aria-labelledby="twin-title twin-desc">',
        '<title id="twin-title">MineGuard schematic telemetry twin</title>',
        '<desc id="twin-desc">A non-surveyed schematic of the collector, assigned telemetry zones, and latest reporting nodes.</desc>',
        '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L6,3 z" fill="#64748b"/></marker></defs>',
        '<rect width="100%" height="100%" rx="20" fill="#0b1220"/>',
        '<g opacity="0.18" stroke="#334155" stroke-width="1">',
    ]
    for x in range(30, width, 40):
        parts.append(f'<line x1="{x}" y1="40" x2="{x}" y2="385"/>')
    for y in range(50, 390, 40):
        parts.append(f'<line x1="20" y1="{y}" x2="{width - 20}" y2="{y}"/>')
    parts.extend([
        '</g>',
        '<text x="28" y="34" fill="#e2e8f0" font-size="17" font-weight="700" font-family="system-ui,sans-serif">SCHEMATIC · latest telemetry snapshot</text>',
        '<text x="28" y="57" fill="#94a3b8" font-size="12" font-family="system-ui,sans-serif">Assigned zones only · not surveyed geometry or live worker location</text>',
        f'<rect x="30" y="82" width="205" height="54" rx="12" fill="#101b2d" stroke="#334155"/>',
        '<text x="46" y="105" fill="#cbd5e1" font-size="12" font-family="system-ui,sans-serif">HOST UPLINK</text>',
        '<text x="46" y="124" fill="#e2e8f0" font-size="14" font-weight="600" font-family="system-ui,sans-serif">USB serial → Python core</text>',
        '<path d="M130 165 V139" stroke="#64748b" stroke-width="2" stroke-dasharray="4 5"/>',
        '<path d="M190 230 H' + str(width - 40) + '" stroke="#1e293b" stroke-width="34" stroke-linecap="round"/>',
        '<path d="M190 230 H' + str(width - 40) + '" stroke="#334155" stroke-width="2" stroke-dasharray="8 12"/>',
    ])

    # Render schematic zone blocks; their positions are illustrative only.
    for zone in zones:
        x = zone_x[zone]
        parts.extend([
            f'<rect x="{x}" y="128" width="194" height="204" rx="16" fill="#101b2d" fill-opacity="0.94" stroke="#334155" stroke-width="1.5"/>',
            f'<text x="{x + 14}" y="153" fill="#cbd5e1" font-size="12" font-weight="700" font-family="system-ui,sans-serif">ZONE · {escape(zone)}</text>',
        ])

    # Collector gateway icon.
    parts.extend([
        '<rect x="38" y="176" width="150" height="108" rx="16" fill="#111c2e" stroke="#64748b" stroke-width="1.5"/>',
        '<circle cx="63" cy="201" r="7" fill="#34d399"/>',
        '<text x="79" y="205" fill="#e2e8f0" font-size="14" font-weight="700" font-family="system-ui,sans-serif">COLLECTOR</text>',
        '<text x="54" y="231" fill="#94a3b8" font-size="12" font-family="system-ui,sans-serif">ESP-NOW / LoRa RX</text>',
        '<text x="54" y="252" fill="#94a3b8" font-size="12" font-family="system-ui,sans-serif">USB → host</text>',
    ])

    offsets = {}
    for node in nodes:
        zone = str(node.get("zone_id") or "unassigned")
        x_center = zone_x.get(zone, zone_x[zones[0]]) + 97
        node_type = "worker" if node.get("node_type") == "worker" else "mine"
        status = str(node.get("freshness") or "unknown").lower()
        status = status if status in COLORS else "unknown"
        color = COLORS[node_type]
        status_color = COLORS[status]
        stack_key = (zone, node_type)
        stack_n = offsets.get(stack_key, 0)
        offsets[stack_key] = stack_n + 1
        x = x_center + (stack_n % 3 - 1) * 45
        y = (190 if node_type == "worker" else 268) + (stack_n // 3) * 34
        link = "ESP-NOW" if node_type == "worker" else "LoRa · 433.92 MHz"
        raw_node_id = str(node.get("node_id") or "unknown")
        if len(raw_node_id) > 18:
            raw_node_id = raw_node_id[:15] + "…"
        node_id = escape(raw_node_id)

        # Direction indicates telemetry moving toward the collector, not radio coverage.
        parts.append(
            f'<line x1="{x - 14}" y1="{y}" x2="194" y2="230" stroke="{color}" stroke-width="1.8" stroke-dasharray="5 6" opacity="0.8" marker-end="url(#arrow)"/>'
        )
        if node_type == "worker":
            parts.extend([
                f'<circle cx="{x}" cy="{y - 4}" r="10" fill="{color}"/>',
                f'<path d="M{x - 12} {y + 18} Q{x} {y + 1} {x + 12} {y + 18} V{y + 22} H{x - 12} Z" fill="{color}"/>',
            ])
        else:
            parts.append(f'<rect x="{x - 11}" y="{y - 11}" width="22" height="22" rx="5" fill="{color}"/>')
        parts.extend([
            f'<circle cx="{x + 20}" cy="{y - 12}" r="5" fill="{status_color}"/>',
            f'<text x="{x}" y="{y + 40}" text-anchor="middle" fill="#e2e8f0" font-size="11" font-weight="600" font-family="system-ui,sans-serif">{node_id}</text>',
            f'<text x="{x}" y="{y + 55}" text-anchor="middle" fill="#94a3b8" font-size="10" font-family="system-ui,sans-serif">{link} · {escape(status)}</text>',
        ])

    if not nodes:
        parts.append('<text x="580" y="235" text-anchor="middle" fill="#94a3b8" font-size="14" font-family="system-ui,sans-serif">Awaiting telemetry from the collector</text>')
    parts.extend([
        '<g font-family="system-ui,sans-serif" font-size="11" fill="#cbd5e1">',
        '<circle cx="48" cy="373" r="5" fill="#34d399"/><text x="60" y="377">recent</text>',
        '<circle cx="126" cy="373" r="5" fill="#fb7185"/><text x="138" y="377">stale</text>',
        '<circle cx="200" cy="373" r="5" fill="#94a3b8"/><text x="212" y="377">unknown</text>',
        '<circle cx="300" cy="373" r="5" fill="#60a5fa"/><text x="312" y="377">worker</text>',
        '<rect x="389" y="368" width="10" height="10" rx="2" fill="#fbbf24"/><text x="406" y="377">mine node</text>',
        '</g>',
        '</svg>',
    ])
    return "".join(parts)
