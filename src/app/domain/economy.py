"""Re-export facade: production + text map + map geometry.

Импортёры могут мигрировать на узкие модули отдельным коммитом.
"""
from app.domain.map_geometry import (
    adjacent_claimable,
    pick_max_separated_tiles,
    too_close_to_ruins,
    toroidal_delta,
    toroidal_manhattan,
    wrap_xy,
)
from app.domain.production import (
    Production,
    TileView,
    building_production,
    fief_daily_production,
    tile_passive,
)
from app.domain.text_map import (
    MAP_EMPTY_MARK,
    MAP_OWNER_CAPTION_MAX_OTHERS,
    format_map_cell,
    format_map_owners,
    format_map_you_pin,
    map_owner_marks,
    owner_mark,
    render_map,
    render_map_parts,
)

__all__ = [
    "MAP_EMPTY_MARK",
    "MAP_OWNER_CAPTION_MAX_OTHERS",
    "Production",
    "TileView",
    "adjacent_claimable",
    "building_production",
    "fief_daily_production",
    "format_map_cell",
    "format_map_owners",
    "format_map_you_pin",
    "map_owner_marks",
    "owner_mark",
    "pick_max_separated_tiles",
    "render_map",
    "render_map_parts",
    "tile_passive",
    "too_close_to_ruins",
    "toroidal_delta",
    "toroidal_manhattan",
    "wrap_xy",
]
