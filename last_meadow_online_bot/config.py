"""Configuration management for the game bot.

Handles loading/saving calibration data and computing absolute screen
coordinates from relative positions within the game window.
"""

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "last-meadow-online-bot"
CONFIG_FILE = CONFIG_DIR / "config.json"
CALIBRATED_TEMPLATE_DIR = CONFIG_DIR / "templates"
BUNDLED_TEMPLATE_DIR = Path(__file__).parent / "templates"

# Reference game window dimensions (what the bundled templates were extracted from)
REFERENCE_WIDTH = 1720
REFERENCE_HEIGHT = 1408

# All regions as relative coordinates (fractions of game content area)
# Format: (rel_y1, rel_y2, rel_x1, rel_x2)
RELATIVE_REGIONS = {
    "arrow": (0.4673, 0.5526, 0.2326, 0.7267),
    "continue": (0.5455, 0.6875, 0.3488, 0.6395),
    "craft_button": (0.9290, 1.0000, 0.6395, 0.8256),
    "craft_cooldown": (0.9773, 0.9893, 0.7762, 0.8169),
    "battle_button": (0.9290, 1.0000, 0.8140, 0.9884),
    "battle_cooldown": (0.9751, 0.9893, 0.9419, 0.9884),
    "battle_arena": (0.0483, 0.9006, 0.0349, 0.9070),
    "back_button": (0.0021, 0.0412, 0.0029, 0.0349),
}

# Button click positions as relative coordinates (fractions of game content area)
# Format: (rel_x, rel_y)
RELATIVE_BUTTONS = {
    "adventure": (0.5366, 0.9595),
    "craft": (0.7093, 0.9595),
    "battle": (0.9006, 0.9595),
}

# Target detection thresholds at reference resolution
REFERENCE_MIN_TARGET_PX = 15
REFERENCE_MAX_TARGET_PX = 120
REFERENCE_MIN_TARGET_AREA = 150


def load_config():
    """Load calibration config from disk. Returns None if not calibrated."""
    if not CONFIG_FILE.exists():
        return None
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def save_config(config):
    """Save calibration config to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def compute_region(region_name, config):
    """Compute absolute screen coordinates for a named region.

    Returns (y1, y2, x1, x2) in absolute screen pixels.
    """
    rel_y1, rel_y2, rel_x1, rel_x2 = RELATIVE_REGIONS[region_name]
    game_x = config["game_x"]
    game_y = config["game_y"]
    game_w = config["game_width"]
    game_h = config["game_height"]

    return (
        int(game_y + rel_y1 * game_h),
        int(game_y + rel_y2 * game_h),
        int(game_x + rel_x1 * game_w),
        int(game_x + rel_x2 * game_w),
    )


def compute_button(button_name, config):
    """Compute absolute screen coordinates for a button click target.

    Returns (abs_x, abs_y) in screen pixels.
    """
    rel_x, rel_y = RELATIVE_BUTTONS[button_name]
    game_x = config["game_x"]
    game_y = config["game_y"]
    game_w = config["game_width"]
    game_h = config["game_height"]

    return (
        int(game_x + rel_x * game_w),
        int(game_y + rel_y * game_h),
    )


def compute_all_regions(config):
    """Compute all regions as a dict of name -> (y1, y2, x1, x2)."""
    return {
        name: compute_region(region_name=name, config=config)
        for name in RELATIVE_REGIONS
    }


def compute_all_buttons(config):
    """Compute all button positions as a dict of name -> (abs_x, abs_y)."""
    return {
        name: compute_button(button_name=name, config=config)
        for name in RELATIVE_BUTTONS
    }


def compute_scale(config):
    """Compute scale factors relative to the reference resolution.

    Returns (scale_x, scale_y) tuple. These may differ if the game window
    has a different aspect ratio than the reference.
    """
    scale_x = config["game_width"] / REFERENCE_WIDTH
    scale_y = config["game_height"] / REFERENCE_HEIGHT
    return scale_x, scale_y


def compute_target_thresholds(config):
    """Compute target detection thresholds scaled to the game window size."""
    scale_x, scale_y = compute_scale(config=config)
    # Use the smaller scale to be conservative with minimum sizes
    min_scale = min(scale_x, scale_y)
    return {
        "min_px": max(5, int(REFERENCE_MIN_TARGET_PX * min_scale)),
        "max_px": max(20, int(REFERENCE_MAX_TARGET_PX * max(scale_x, scale_y))),
        "min_area": max(20, int(REFERENCE_MIN_TARGET_AREA * scale_x * scale_y)),
    }


def get_template_dir(config):
    """Return the appropriate template directory.

    Uses calibrated templates if available, otherwise bundled defaults.
    """
    if CALIBRATED_TEMPLATE_DIR.exists() and any(CALIBRATED_TEMPLATE_DIR.glob("*.png")):
        return CALIBRATED_TEMPLATE_DIR
    return BUNDLED_TEMPLATE_DIR
