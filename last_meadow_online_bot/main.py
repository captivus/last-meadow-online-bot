#!/usr/bin/env python3
"""Game bot: automates adventuring, crafting, battling, and the full game loop.

Flow:
1. Main screen, both on cooldown: rapid-click Adventure button to grind
2. Main screen, craft ready: click Craft, do arrow sequence, click Continue
3. Main screen, battle ready: click Battle, find and click targets
4. Back to step 1

F8 to start, Escape to pause, Enter to resume, Ctrl+C to quit.
"""

import argparse
import sys
import threading
import time

import cv2
import numpy as np
from PIL import ImageGrab
from pynput import keyboard, mouse

from .config import (
    compute_all_buttons,
    compute_all_regions,
    compute_target_thresholds,
    get_template_dir,
    load_config,
)

MATCH_THRESHOLD = 0.7
KEY_DELAY = 0.05
ADVENTURE_CLICK_INTERVAL = 0.02
POLL_INTERVAL = 0.5
HOTKEY = keyboard.Key.f8


def load_templates(config):
    """Load template images from the calibrated template directory."""
    template_dir = get_template_dir(config=config)
    templates = {}

    for name in ["up", "down", "left", "right", "continue", "craft_button", "battle_button"]:
        path = template_dir / f"{name}.png"
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise FileNotFoundError(f"Template not found: {path}")
        templates[name] = img

    return templates


def capture_region(region):
    """Capture a screen region. Region is (y1, y2, x1, x2)."""
    y1, y2, x1, x2 = region
    screenshot = ImageGrab.grab(bbox=(x1, y1, x2, y2))
    return cv2.cvtColor(src=np.array(screenshot), code=cv2.COLOR_RGB2GRAY)


def find_template(screen, template, threshold=MATCH_THRESHOLD):
    """Find best match location for a template. Returns (x, y, confidence) or None."""
    if template.shape[0] > screen.shape[0] or template.shape[1] > screen.shape[1]:
        return None

    result = cv2.matchTemplate(
        image=screen,
        templ=template,
        method=cv2.TM_CCOEFF_NORMED,
    )
    _, max_val, _, max_loc = cv2.minMaxLoc(src=result)
    if max_val >= threshold:
        return max_loc[0], max_loc[1], max_val
    return None


def find_arrows(screen, templates):
    """Find all arrow matches in the screen region, sorted left to right."""
    arrow_templates = {d: templates[d] for d in ["up", "down", "left", "right"]}
    matches = []

    for direction, template in arrow_templates.items():
        if template.shape[0] > screen.shape[0] or template.shape[1] > screen.shape[1]:
            continue

        result = cv2.matchTemplate(
            image=screen,
            templ=template,
            method=cv2.TM_CCOEFF_NORMED,
        )
        locations = np.where(result >= MATCH_THRESHOLD)
        for x, y in zip(locations[1], locations[0]):
            matches.append((x, direction))

    matches.sort(key=lambda m: m[0])
    deduplicated = []
    for x, direction in matches:
        if not deduplicated or abs(x - deduplicated[-1][0]) > 30:
            deduplicated.append((x, direction))

    return [direction for _, direction in deduplicated]


def find_target(screen, target_thresholds):
    """Find a bullseye target in the battle screen using circular blob detection.

    Returns (center_x, center_y) relative to the screen region, or None.
    """
    min_px = target_thresholds["min_px"]
    max_px = target_thresholds["max_px"]
    min_area = target_thresholds["min_area"]

    _, thresh = cv2.threshold(src=screen, thresh=100, maxval=255, type=cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(
        image=thresh,
        mode=cv2.RETR_EXTERNAL,
        method=cv2.CHAIN_APPROX_SIMPLE,
    )

    best = None
    best_circularity = 0

    for c in contours:
        x, y, w, h = cv2.boundingRect(array=c)
        area = cv2.contourArea(contour=c)
        perimeter = cv2.arcLength(curve=c, closed=True)

        if perimeter == 0 or area < min_area:
            continue
        if w < min_px or h < min_px or w > max_px or h > max_px:
            continue

        circularity = 4 * np.pi * area / (perimeter ** 2)
        aspect_ratio = w / h if h > 0 else 0

        if circularity > 0.6 and 0.7 < aspect_ratio < 1.4:
            if circularity > best_circularity:
                best_circularity = circularity
                center_x = x + w // 2
                center_y = y + h // 2
                best = (center_x, center_y)

    return best


def press_arrow_keys(sequence):
    """Press arrow keys in sequence."""
    key_map = {
        "up": keyboard.Key.up,
        "down": keyboard.Key.down,
        "left": keyboard.Key.left,
        "right": keyboard.Key.right,
    }
    ctrl = keyboard.Controller()
    for direction in sequence:
        key = key_map[direction]
        ctrl.press(key=key)
        ctrl.release(key=key)
        time.sleep(KEY_DELAY)


def click_at(region, x_offset, y_offset):
    """Click at a position relative to a screen region."""
    y1, _, x1, _ = region
    abs_x = x1 + x_offset
    abs_y = y1 + y_offset
    ctrl = mouse.Controller()
    ctrl.position = (abs_x, abs_y)
    time.sleep(0.1)
    ctrl.click(button=mouse.Button.left)


def click_absolute(x, y):
    """Click at an absolute screen position."""
    ctrl = mouse.Controller()
    ctrl.position = (x, y)
    ctrl.click(button=mouse.Button.left)


def cooldown_is_done(region):
    """Check if a cooldown timer is absent in the given region.

    Timer present = ~10-12% dark pixels, no timer = ~0%.
    """
    screen = capture_region(region)
    dark_pixels = np.sum(screen < 100)
    total_pixels = screen.size
    dark_ratio = dark_pixels / total_pixels
    return dark_ratio < 0.03


def in_minigame(regions):
    """Detect if we're in a minigame screen by checking for the back button '<'."""
    screen = capture_region(regions["back_button"])
    dark_pixels = np.sum(screen < 50)
    total_pixels = screen.size
    return dark_pixels / total_pixels > 0.1


def detect_state(templates, regions, target_thresholds):
    """Detect which screen the game is on.

    Returns one of: 'main', 'anvil', 'success', 'battle', 'unknown'
    """
    # Check for arrows (anvil screen)
    arrow_screen = capture_region(regions["arrow"])
    arrows = find_arrows(screen=arrow_screen, templates=templates)
    if len(arrows) >= 3:
        return "anvil", arrows

    # Check for Continue button (success screen — shared by craft and battle)
    continue_screen = capture_region(regions["continue"])
    match = find_template(
        screen=continue_screen,
        template=templates["continue"],
    )
    if match:
        return "success", match

    # Check for Craft button (main screen)
    craft_screen = capture_region(regions["craft_button"])
    match = find_template(
        screen=craft_screen,
        template=templates["craft_button"],
    )
    if match:
        return "main", match

    # If no main screen buttons found, check if we're in a minigame (back button present)
    # If we're in a minigame but no arrows and no continue, it must be battle
    if in_minigame(regions=regions):
        battle_screen = capture_region(regions["battle_arena"])
        target = find_target(screen=battle_screen, target_thresholds=target_thresholds)
        return "battle", target

    return "unknown", None


def adventure_click_burst(running_event, buttons, duration=2.0):
    """Rapid-click the Adventure button for a burst, then return to check state."""
    adv_x, adv_y = buttons["adventure"]
    end_time = time.monotonic() + duration
    while time.monotonic() < end_time and running_event.is_set():
        click_absolute(x=adv_x, y=adv_y)
        time.sleep(ADVENTURE_CLICK_INTERVAL)


def run_battle(running_event, templates, regions, target_thresholds):
    """Rapidly scan for and click targets during battle."""
    print("[Battle] Hunting targets...")
    misses = 0
    same_spot_count = 0
    last_click_pos = None
    last_continue_check = time.monotonic()

    while running_event.is_set() and misses < 20:
        # Check for Continue button every ~1 second
        now = time.monotonic()
        if now - last_continue_check > 1.0:
            last_continue_check = now
            continue_screen = capture_region(regions["continue"])
            match = find_template(
                screen=continue_screen,
                template=templates["continue"],
            )
            if match:
                print("[Battle] Battle over, found Continue.")
                return

        screen = capture_region(regions["battle_arena"])
        target = find_target(screen=screen, target_thresholds=target_thresholds)

        if target:
            center_x, center_y = target
            y1, _, x1, _ = regions["battle_arena"]
            abs_x = x1 + center_x
            abs_y = y1 + center_y

            # If we keep clicking the same spot, it's a false positive
            if last_click_pos and abs(abs_x - last_click_pos[0]) < 10 and abs(abs_y - last_click_pos[1]) < 10:
                same_spot_count += 1
                if same_spot_count > 3:
                    # Treat repeated same-spot clicks as a miss
                    misses += 1
                    time.sleep(0.05)
                    continue
            else:
                same_spot_count = 0

            last_click_pos = (abs_x, abs_y)
            click_absolute(x=abs_x, y=abs_y)
            print(f"[Battle] Clicked target at ({abs_x}, {abs_y})")
            misses = 0
            time.sleep(0.05)
        else:
            misses += 1
            time.sleep(0.05)

    print("[Battle] Battle complete or no targets found.")


def run_loop(running_event, config):
    """Main automation loop."""
    regions = compute_all_regions(config=config)
    buttons = compute_all_buttons(config=config)
    target_thresholds = compute_target_thresholds(config=config)
    templates = load_templates(config=config)

    print("Bot started. Detecting game state...")
    adventuring = False

    while running_event.is_set():
        state, data = detect_state(
            templates=templates,
            regions=regions,
            target_thresholds=target_thresholds,
        )

        if state == "anvil":
            adventuring = False
            arrows = data
            print(f"[Anvil] Detected: {' -> '.join(arrows)}")
            press_arrow_keys(sequence=arrows)
            print("[Anvil] Keys pressed, waiting for result...")
            time.sleep(1.0)

        elif state == "battle":
            adventuring = False
            if data:
                center_x, center_y = data
                y1, _, x1, _ = regions["battle_arena"]
                click_absolute(x=x1 + center_x, y=y1 + center_y)
                print("[Battle] Clicked first target")
            run_battle(
                running_event=running_event,
                templates=templates,
                regions=regions,
                target_thresholds=target_thresholds,
            )
            time.sleep(1.0)

        elif state == "success":
            adventuring = False
            x, y, confidence = data
            template_h, template_w = templates["continue"].shape[:2]
            click_x = x + template_w // 2
            click_y = y + template_h // 2
            print(f"[Success] Clicking Continue (confidence: {confidence:.2f})")
            click_at(
                region=regions["continue"],
                x_offset=click_x,
                y_offset=click_y,
            )
            time.sleep(1.0)

        elif state == "main":
            craft_ready = cooldown_is_done(region=regions["craft_cooldown"])
            battle_ready = cooldown_is_done(region=regions["battle_cooldown"])

            if craft_ready:
                adventuring = False
                craft_x, craft_y = buttons["craft"]
                print("[Main] Craft ready, clicking Craft")
                click_absolute(x=craft_x, y=craft_y)
                time.sleep(1.5)
            elif battle_ready:
                adventuring = False
                battle_x, battle_y = buttons["battle"]
                print("[Main] Battle ready, clicking Battle")
                click_absolute(x=battle_x, y=battle_y)
                time.sleep(1.5)
            else:
                if not adventuring:
                    print("[Main] Both on cooldown, adventuring...")
                    adventuring = True
                adventure_click_burst(
                    running_event=running_event,
                    buttons=buttons,
                    duration=2.0,
                )
                continue

        else:
            pass

        time.sleep(POLL_INTERVAL)

    print("Bot stopped.")


def main():
    parser = argparse.ArgumentParser(
        description="Automated game bot for Last Meadow Online",
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="Run the calibration wizard to configure your screen layout",
    )
    args = parser.parse_args()

    if args.calibrate:
        from .calibrate import run_calibration
        success = run_calibration()
        sys.exit(0 if success else 1)

    # Load config
    config = load_config()
    if config is None:
        print("No calibration found. Run with --calibrate first:")
        print()
        print("  last-meadow-online-bot --calibrate")
        print()
        sys.exit(1)

    print("Game bot ready. F8 to start, Escape to pause, Enter to resume, Ctrl+C to quit.")

    running_event = threading.Event()
    bot_thread = None

    def start_bot():
        nonlocal bot_thread
        print("Starting bot...")
        running_event.set()
        bot_thread = threading.Thread(
            target=run_loop,
            args=(running_event, config),
            daemon=True,
        )
        bot_thread.start()

    def on_press(key):
        if key == keyboard.Key.esc:
            if running_event.is_set():
                print("Paused. Press Enter in the terminal to resume, or Ctrl+C to quit.")
                running_event.clear()

        elif key == HOTKEY:
            if not running_event.is_set():
                start_bot()

    def input_loop():
        while True:
            try:
                input()  # wait for Enter
                if not running_event.is_set():
                    start_bot()
            except EOFError:
                break

    input_thread = threading.Thread(target=input_loop, daemon=True)
    input_thread.start()

    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()


if __name__ == "__main__":
    main()
