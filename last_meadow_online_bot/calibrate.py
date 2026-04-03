"""Calibration wizard for the game bot.

Extracts templates directly from the user's live screen to ensure
accurate template matching at any resolution and aspect ratio.
"""

import cv2
import numpy as np
from PIL import ImageGrab
from pynput import mouse

from .config import (
    CALIBRATED_TEMPLATE_DIR,
    compute_region,
    save_config,
)


def get_mouse_position():
    """Return current mouse position as (x, y)."""
    ctrl = mouse.Controller()
    return ctrl.position


def capture_game_region(region_name, config):
    """Capture a game region and return as grayscale numpy array."""
    y1, y2, x1, x2 = compute_region(region_name=region_name, config=config)
    screenshot = ImageGrab.grab(bbox=(x1, y1, x2, y2))
    return cv2.cvtColor(src=np.array(screenshot), code=cv2.COLOR_RGB2GRAY)


def extract_button_templates(config):
    """Extract Craft and Battle button templates from the main screen."""
    CALIBRATED_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

    for button_name in ["craft_button", "battle_button"]:
        screen = capture_game_region(region_name=button_name, config=config)
        # The button fills most of the region, save the whole capture
        path = CALIBRATED_TEMPLATE_DIR / f"{button_name}.png"
        cv2.imwrite(filename=str(path), img=screen)

    return True


def extract_arrow_templates(config):
    """Extract individual arrow templates from the crafting screen.

    Detects dark arrow-shaped contours in the arrow region and classifies
    each by its shape (which direction it points).
    """
    CALIBRATED_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

    screen = capture_game_region(region_name="arrow", config=config)

    # Find dark contours (the arrows)
    _, thresh = cv2.threshold(src=screen, thresh=100, maxval=255, type=cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(
        image=thresh,
        mode=cv2.RETR_EXTERNAL,
        method=cv2.CHAIN_APPROX_SIMPLE,
    )

    # Filter to arrow-sized contours and sort left to right
    min_size = screen.shape[0] * 0.3  # arrows should be at least 30% of region height
    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(array=c)
        if w > min_size and h > min_size:
            boxes.append((x, y, w, h))

    boxes.sort(key=lambda b: b[0])

    if len(boxes) < 4:
        print(f"        Warning: Only found {len(boxes)} arrow candidates (need at least 4).")
        return False

    # Classify each arrow by analyzing which direction it points.
    # Compare the center of mass to the bounding box center:
    # - Right arrow: center of mass is left of bbox center (body is left, point is right)
    # - Left arrow: center of mass is right of bbox center
    # - Up arrow: center of mass is below bbox center
    # - Down arrow: center of mass is above bbox center
    saved = {"up": False, "down": False, "left": False, "right": False}

    for x, y, w, h in boxes:
        # Extract the arrow region from the thresholded image
        arrow_mask = thresh[y:y + h, x:x + w]
        moments = cv2.moments(array=arrow_mask)

        if moments["m00"] == 0:
            continue

        cx = moments["m10"] / moments["m00"]
        cy = moments["m01"] / moments["m00"]

        # Relative position of center of mass within bounding box
        rel_cx = cx / w
        rel_cy = cy / h

        # Classify based on center of mass offset
        dx = rel_cx - 0.5
        dy = rel_cy - 0.5

        if abs(dx) > abs(dy):
            direction = "left" if dx > 0 else "right"
        else:
            direction = "up" if dy > 0 else "down"

        if not saved[direction]:
            # Crop from the original grayscale with small padding
            pad = 2
            crop_y1 = max(0, y - pad)
            crop_y2 = min(screen.shape[0], y + h + pad)
            crop_x1 = max(0, x - pad)
            crop_x2 = min(screen.shape[1], x + w + pad)
            arrow_img = screen[crop_y1:crop_y2, crop_x1:crop_x2]

            path = CALIBRATED_TEMPLATE_DIR / f"{direction}.png"
            cv2.imwrite(filename=str(path), img=arrow_img)
            saved[direction] = True

    found = [d for d, s in saved.items() if s]
    missing = [d for d, s in saved.items() if not s]

    if missing:
        print(f"        Warning: Could not find arrows for: {', '.join(missing)}")
        return False

    print(f"        Extracted arrow templates: {', '.join(found)}")
    return True


def extract_continue_template(config):
    """Extract the Continue button template from a success screen.

    The Continue button is a small bordered rectangle with text,
    located in the lower portion of the continue region. We look
    for a wide-ish contour in the bottom half that's not too large
    (to avoid matching the reward box above it).
    """
    CALIBRATED_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

    screen = capture_game_region(region_name="continue", config=config)
    height, width = screen.shape[:2]

    # Only look in the bottom half of the region (Continue is below the reward box)
    bottom_half = screen[height // 2:, :]

    _, thresh = cv2.threshold(src=bottom_half, thresh=100, maxval=255, type=cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(
        image=thresh,
        mode=cv2.RETR_EXTERNAL,
        method=cv2.CHAIN_APPROX_SIMPLE,
    )

    # Find a contour that looks like a button:
    # - wider than tall
    # - not too large (less than 60% of region width — excludes reward box)
    # - not too small
    best = None
    best_area = 0
    for c in contours:
        x, y, w, h = cv2.boundingRect(array=c)
        area = w * h
        if (w > h
                and w > width * 0.08
                and w < width * 0.6
                and area > best_area):
            best = (x, y + height // 2, w, h)
            best_area = area

    if best is None:
        print("        Warning: Could not find Continue button.")
        return False

    x, y, w, h = best
    pad = 2
    crop_y1 = max(0, y - pad)
    crop_y2 = min(height, y + h + pad)
    crop_x1 = max(0, x - pad)
    crop_x2 = min(width, x + w + pad)
    continue_img = screen[crop_y1:crop_y2, crop_x1:crop_x2]

    path = CALIBRATED_TEMPLATE_DIR / "continue.png"
    cv2.imwrite(filename=str(path), img=continue_img)
    return True


def verify_main_screen(config):
    """Verify we can detect the craft button on the current screen."""
    region = compute_region(region_name="craft_button", config=config)
    y1, y2, x1, x2 = region
    screenshot = ImageGrab.grab(bbox=(x1, y1, x2, y2))
    screen = cv2.cvtColor(src=np.array(screenshot), code=cv2.COLOR_RGB2GRAY)

    template = cv2.imread(
        str(CALIBRATED_TEMPLATE_DIR / "craft_button.png"),
        cv2.IMREAD_GRAYSCALE,
    )

    if template is None:
        return False, 0.0

    if template.shape[0] > screen.shape[0] or template.shape[1] > screen.shape[1]:
        return False, 0.0

    result = cv2.matchTemplate(
        image=screen,
        templ=template,
        method=cv2.TM_CCOEFF_NORMED,
    )
    _, max_val, _, _ = cv2.minMaxLoc(src=result)

    return max_val >= 0.5, max_val


def run_calibration():
    """Run the interactive calibration wizard."""
    print()
    print("=== Calibration Wizard ===")
    print()
    print("Make sure the game is visible on the main screen")
    print("(with Adventure, Craft, Battle buttons visible).")
    print()

    # Step 1: Top-left corner
    print("Step 1: Move your mouse to the TOP-LEFT corner of the game area.")
    print("        This should be just inside the game content, below any")
    print("        window title bar or browser chrome.")
    input("        Press Enter when positioned... ")
    top_left = get_mouse_position()
    print(f"        Recorded: ({top_left[0]}, {top_left[1]})")
    print()

    # Step 2: Bottom-right corner
    print("Step 2: Move your mouse to the BOTTOM-RIGHT corner of the game area.")
    input("        Press Enter when positioned... ")
    bottom_right = get_mouse_position()
    print(f"        Recorded: ({bottom_right[0]}, {bottom_right[1]})")
    print()

    # Compute game window bounds
    game_x = top_left[0]
    game_y = top_left[1]
    game_width = bottom_right[0] - top_left[0]
    game_height = bottom_right[1] - top_left[1]

    if game_width <= 0 or game_height <= 0:
        print("Error: Bottom-right must be below and to the right of top-left.")
        print("       Please run calibration again.")
        return False

    print(f"Game area: {game_width} x {game_height} pixels")
    print(f"Position: ({game_x}, {game_y})")
    print()

    config = {
        "game_x": game_x,
        "game_y": game_y,
        "game_width": game_width,
        "game_height": game_height,
    }

    # Step 3: Extract button templates from the main screen
    print("Step 3: Extracting button templates from the main screen...")
    extract_button_templates(config=config)
    print("        Craft and Battle button templates saved.")
    print()

    # Step 4: Verify detection works
    print("Step 4: Verifying detection on the main screen...")
    detected, confidence = verify_main_screen(config=config)
    if detected:
        print(f"        Craft button detected! (confidence: {confidence:.2f})")
    else:
        print(f"        Warning: Craft button match low (confidence: {confidence:.2f}).")
        print("        Make sure the game is fully visible on the main screen.")
    print()

    # Step 5: Extract arrow templates from the crafting screen
    print("Step 5: Click the Craft button in the game to open the crafting screen.")
    print("        Wait until the arrow sequence is fully visible.")
    input("        Press Enter when you see the arrows... ")
    print("        Extracting arrow templates...")
    arrows_ok = extract_arrow_templates(config=config)
    print()

    # Step 6: Complete the craft and extract Continue template
    print("Step 6: Complete the arrow sequence (press the keys manually).")
    print("        Wait until you see the success screen with the Continue button.")
    input("        Press Enter when you see Continue... ")
    print("        Extracting Continue template...")
    continue_ok = extract_continue_template(config=config)
    if continue_ok:
        print("        Continue template saved.")
    print()

    # Save config
    save_config(config=config)
    print(f"Calibration saved to {CALIBRATED_TEMPLATE_DIR.parent}/")
    print()

    if arrows_ok and continue_ok and detected:
        print("Calibration complete! You can now run: last-meadow-online-bot")
    else:
        print("Calibration saved with warnings. The bot may need re-calibration.")
    print()

    return True
