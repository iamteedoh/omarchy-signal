"""Emoji shortcodes (``:smile:`` → 😄), GitHub/Slack style.

A curated table rather than the full Unicode list: the names people actually
type. Lookups are prefix and substring based so ``:thumb`` finds
``:thumbsup:``. Skin-tone and flag sequences are deliberately left out.
"""

from __future__ import annotations

import re

SHORTCODES: dict[str, str] = {
    # smileys
    "smile": "😄", "grinning": "😀", "grin": "😁", "laughing": "😆", "joy": "😂", "rofl": "🤣", "sweat_smile": "😅",
    "blush": "😊", "innocent": "😇", "wink": "😉", "relieved": "😌", "heart_eyes": "😍", "smiling_face_with_hearts": "🥰",
    "kissing_heart": "😘", "kissing": "😗", "yum": "😋", "stuck_out_tongue": "😛", "stuck_out_tongue_winking_eye": "😜",
    "zany_face": "🤪", "thinking": "🤔", "shushing_face": "🤫", "raised_eyebrow": "🤨", "neutral_face": "😐",
    "expressionless": "😑", "no_mouth": "😶", "smirk": "😏", "unamused": "😒", "roll_eyes": "🙄", "grimacing": "😬",
    "lying_face": "🤥", "pensive": "😔", "sleepy": "😪", "drooling_face": "🤤", "sleeping": "😴", "mask": "😷",
    "face_with_thermometer": "🤒", "face_with_head_bandage": "🤕", "nauseated_face": "🤢", "vomiting_face": "🤮",
    "sneezing_face": "🤧", "hot_face": "🥵", "cold_face": "🥶", "woozy_face": "🥴", "dizzy_face": "😵",
    "exploding_head": "🤯", "cowboy_hat_face": "🤠", "partying_face": "🥳", "sunglasses": "😎", "nerd_face": "🤓",
    "monocle_face": "🧐", "confused": "😕", "worried": "😟", "slightly_frowning_face": "🙁", "frowning_face": "☹️",
    "open_mouth": "😮", "hushed": "😯", "astonished": "😲", "flushed": "😳", "pleading_face": "🥺", "frowning": "😦",
    "anguished": "😧", "fearful": "😨", "cold_sweat": "😰", "disappointed_relieved": "😥", "cry": "😢", "sob": "😭",
    "scream": "😱", "confounded": "😖", "persevere": "😣", "disappointed": "😞", "sweat": "😓", "weary": "😩",
    "tired_face": "😫", "yawning_face": "🥱", "triumph": "😤", "rage": "😡", "angry": "😠", "cursing_face": "🤬",
    "smiling_imp": "😈", "imp": "👿", "skull": "💀", "skull_and_crossbones": "☠️", "poop": "💩", "clown_face": "🤡",
    "ghost": "👻", "alien": "👽", "robot": "🤖", "smiley_cat": "😺", "smile_cat": "😸", "joy_cat": "😹",
    "heart_eyes_cat": "😻", "crying_cat_face": "😿", "see_no_evil": "🙈", "hear_no_evil": "🙉", "speak_no_evil": "🙊",
    "slightly_smiling_face": "🙂", "upside_down_face": "🙃", "smiley": "😃", "melting_face": "🫠", "saluting_face": "🫡",
    # hearts & symbols
    "heart": "❤️", "orange_heart": "🧡", "yellow_heart": "💛", "green_heart": "💚", "blue_heart": "💙", "purple_heart": "💜",
    "black_heart": "🖤", "white_heart": "🤍", "brown_heart": "🤎", "broken_heart": "💔", "heartbeat": "💓", "heartpulse": "💗",
    "two_hearts": "💕", "sparkling_heart": "💖", "revolving_hearts": "💞", "cupid": "💘", "gift_heart": "💝", "heart_decoration": "💟",
    "kiss": "💋", "100": "💯", "anger": "💢", "boom": "💥", "dizzy": "💫", "sweat_drops": "💦", "dash": "💨", "zzz": "💤",
    "speech_balloon": "💬", "thought_balloon": "💭", "fire": "🔥", "sparkles": "✨", "star": "⭐", "star2": "🌟",
    "zap": "⚡", "tada": "🎉", "confetti_ball": "🎊", "balloon": "🎈", "gift": "🎁", "trophy": "🏆", "medal": "🏅",
    "check": "✅", "white_check_mark": "✅", "heavy_check_mark": "✔️", "x": "❌", "negative_squared_cross_mark": "❎",
    "question": "❓", "exclamation": "❗", "warning": "⚠️", "no_entry": "⛔", "no_entry_sign": "🚫", "recycle": "♻️",
    "infinity": "♾️", "heavy_plus_sign": "➕", "heavy_minus_sign": "➖", "arrow_right": "➡️", "arrow_left": "⬅️",
    "arrow_up": "⬆️", "arrow_down": "⬇️", "ok": "🆗", "new": "🆕", "cool": "🆒", "free": "🆓", "sos": "🆘",
    # gestures & people
    "thumbsup": "👍", "+1": "👍", "thumbsdown": "👎", "-1": "👎", "ok_hand": "👌", "pinched_fingers": "🤌", "v": "✌️",
    "crossed_fingers": "🤞", "love_you_gesture": "🤟", "metal": "🤘", "call_me_hand": "🤙", "point_left": "👈",
    "point_right": "👉", "point_up": "☝️", "point_up_2": "👆", "point_down": "👇", "middle_finger": "🖕", "raised_hand": "✋",
    "wave": "👋", "raised_back_of_hand": "🤚", "vulcan_salute": "🖖", "clap": "👏", "raised_hands": "🙌", "open_hands": "👐",
    "palms_up_together": "🤲", "handshake": "🤝", "pray": "🙏", "writing_hand": "✍️", "nail_care": "💅", "selfie": "🤳",
    "muscle": "💪", "eyes": "👀", "eye": "👁️", "brain": "🧠", "tongue": "👅", "lips": "👄", "ear": "👂", "nose": "👃",
    "baby": "👶", "child": "🧒", "boy": "👦", "girl": "👧", "adult": "🧑", "man": "👨", "woman": "👩", "older_adult": "🧓",
    "facepalm": "🤦", "shrug": "🤷", "person_raising_hand": "🙋", "bow": "🙇", "dancer": "💃", "man_dancing": "🕺",
    "running": "🏃", "walking": "🚶", "couple": "👫", "family": "👪", "santa": "🎅", "detective": "🕵️", "cook": "🧑‍🍳",
    "technologist": "🧑‍💻", "superhero": "🦸", "zombie": "🧟", "ninja": "🥷",
    # animals & nature
    "dog": "🐶", "cat": "🐱", "mouse": "🐭", "hamster": "🐹", "rabbit": "🐰", "fox_face": "🦊", "bear": "🐻", "panda_face": "🐼",
    "koala": "🐨", "tiger": "🐯", "lion": "🦁", "cow": "🐮", "pig": "🐷", "frog": "🐸", "monkey_face": "🐵", "chicken": "🐔",
    "penguin": "🐧", "bird": "🐦", "eagle": "🦅", "owl": "🦉", "bat": "🦇", "wolf": "🐺", "boar": "🐗", "horse": "🐴",
    "unicorn": "🦄", "bee": "🐝", "bug": "🐛", "butterfly": "🦋", "snail": "🐌", "turtle": "🐢", "snake": "🐍", "dragon": "🐉",
    "whale": "🐳", "dolphin": "🐬", "fish": "🐟", "shark": "🦈", "octopus": "🐙", "crab": "🦀", "elephant": "🐘", "goat": "🐐",
    "sheep": "🐑", "duck": "🦆", "rooster": "🐓", "peacock": "🦚", "parrot": "🦜", "rat": "🐀", "raccoon": "🦝", "sloth": "🦥",
    "bouquet": "💐", "cherry_blossom": "🌸", "rose": "🌹", "sunflower": "🌻", "blossom": "🌼", "tulip": "🌷", "seedling": "🌱",
    "evergreen_tree": "🌲", "deciduous_tree": "🌳", "palm_tree": "🌴", "cactus": "🌵", "four_leaf_clover": "🍀", "maple_leaf": "🍁",
    "fallen_leaf": "🍂", "leaves": "🍃", "mushroom": "🍄", "earth_americas": "🌎", "earth_africa": "🌍", "moon": "🌙",
    "full_moon": "🌕", "sun": "☀️", "sunny": "☀️", "cloud": "☁️", "rain_cloud": "🌧️", "snowflake": "❄️", "rainbow": "🌈",
    "umbrella": "☔", "ocean": "🌊", "volcano": "🌋", "mountain": "⛰️", "comet": "☄️", "milky_way": "🌌",
    # food & drink
    "apple": "🍎", "banana": "🍌", "grapes": "🍇", "watermelon": "🍉", "strawberry": "🍓", "cherries": "🍒", "peach": "🍑",
    "pineapple": "🍍", "avocado": "🥑", "lemon": "🍋", "mango": "🥭", "corn": "🌽", "hot_pepper": "🌶️", "broccoli": "🥦",
    "bread": "🍞", "croissant": "🥐", "cheese": "🧀", "egg": "🥚", "bacon": "🥓", "hamburger": "🍔", "fries": "🍟",
    "pizza": "🍕", "hotdog": "🌭", "taco": "🌮", "burrito": "🌯", "sandwich": "🥪", "salad": "🥗", "ramen": "🍜",
    "spaghetti": "🍝", "sushi": "🍣", "bento": "🍱", "rice": "🍚", "curry": "🍛", "dumpling": "🥟", "cookie": "🍪",
    "cake": "🍰", "birthday": "🎂", "cupcake": "🧁", "doughnut": "🍩", "ice_cream": "🍨", "icecream": "🍦", "chocolate_bar": "🍫",
    "candy": "🍬", "lollipop": "🍭", "popcorn": "🍿", "coffee": "☕", "tea": "🍵", "beer": "🍺", "beers": "🍻", "wine_glass": "🍷",
    "cocktail": "🍸", "tropical_drink": "🍹", "champagne": "🍾", "clinking_glasses": "🥂", "tumbler_glass": "🥃",
    "milk_glass": "🥛", "cup_with_straw": "🥤", "bubble_tea": "🧋",
    # activities & objects
    "soccer": "⚽", "basketball": "🏀", "football": "🏈", "baseball": "⚾", "tennis": "🎾", "volleyball": "🏐", "golf": "⛳",
    "ski": "🎿", "snowboarder": "🏂", "surfer": "🏄", "swimmer": "🏊", "bike": "🚲", "video_game": "🎮", "joystick": "🕹️",
    "game_die": "🎲", "chess_pawn": "♟️", "dart": "🎯", "bowling": "🎳", "guitar": "🎸", "musical_note": "🎵", "notes": "🎶",
    "microphone": "🎤", "headphones": "🎧", "art": "🎨", "clapper": "🎬", "movie_camera": "🎥", "camera": "📷",
    "camera_flash": "📸", "video_camera": "📹", "tv": "📺", "radio": "📻", "phone": "📱", "iphone": "📱", "telephone": "☎️",
    "computer": "💻", "desktop_computer": "🖥️", "keyboard": "⌨️", "mouse_computer": "🖱️", "printer": "🖨️", "floppy_disk": "💾",
    "cd": "💿", "battery": "🔋", "electric_plug": "🔌", "bulb": "💡", "flashlight": "🔦", "candle": "🕯️", "wrench": "🔧",
    "hammer": "🔨", "hammer_and_wrench": "🛠️", "gear": "⚙️", "nut_and_bolt": "🔩", "toolbox": "🧰", "magnet": "🧲",
    "microscope": "🔬", "telescope": "🔭", "satellite": "📡", "syringe": "💉", "pill": "💊", "lock": "🔒", "unlock": "🔓",
    "key": "🔑", "old_key": "🗝️", "shield": "🛡️", "package": "📦", "mailbox": "📫", "envelope": "✉️", "email": "📧",
    "inbox_tray": "📥", "outbox_tray": "📤", "memo": "📝", "pencil2": "✏️", "book": "📖", "books": "📚", "bookmark": "🔖",
    "newspaper": "📰", "calendar": "📅", "date": "📆", "clock": "🕐", "alarm_clock": "⏰", "hourglass": "⌛", "stopwatch": "⏱️",
    "money_with_wings": "💸", "dollar": "💵", "credit_card": "💳", "moneybag": "💰", "gem": "💎", "crown": "👑",
    "ring": "💍", "tophat": "🎩", "glasses": "👓", "shirt": "👕", "jeans": "👖", "dress": "👗", "shoe": "👟", "boot": "👢",
    "backpack": "🎒", "briefcase": "💼", "handbag": "👜", "umbrella2": "🌂", "house": "🏠", "office": "🏢", "hospital": "🏥",
    "school": "🏫", "church": "⛪", "castle": "🏰", "tent": "⛺", "city_sunset": "🌇", "bridge_at_night": "🌉", "statue_of_liberty": "🗽",
    "car": "🚗", "taxi": "🚕", "bus": "🚌", "truck": "🚚", "police_car": "🚓", "ambulance": "🚑", "fire_engine": "🚒",
    "motorcycle": "🏍️", "train": "🚋", "metro": "🚇", "airplane": "✈️", "rocket": "🚀", "helicopter": "🚁", "ship": "🚢",
    "sailboat": "⛵", "anchor": "⚓", "fuelpump": "⛽", "traffic_light": "🚥", "construction": "🚧", "world_map": "🗺️",
    "compass": "🧭", "bell": "🔔", "no_bell": "🔕", "loudspeaker": "📢", "mega": "📣", "mute": "🔇", "sound": "🔉",
    "loud_sound": "🔊", "hourglass_flowing_sand": "⏳", "link": "🔗", "paperclip": "📎", "scissors": "✂️", "pushpin": "📌",
    "round_pushpin": "📍", "triangular_flag_on_post": "🚩", "checkered_flag": "🏁", "white_flag": "🏳️", "rainbow_flag": "🏳️‍🌈",
    "pirate_flag": "🏴‍☠️", "signal_strength": "📶", "copyright": "©️", "registered": "®️", "tm": "™️",
    "eggplant": "🍆", "hot_beverage": "☕", "wavy_dash": "〰️", "bomb": "💣", "hole": "🕳️", "black_cat": "🐈‍⬛",
}

# Classic emoticons, converted only when they stand alone (space or line
# boundaries on both sides), so "http://x" and "10:30" are never touched.
EMOTICONS: dict[str, str] = {
    ":)": "🙂", ":-)": "🙂", "(:": "🙂", ":D": "😃", ":-D": "😃", ":(": "🙁", ":-(": "🙁", ";)": "😉", ";-)": "😉",
    ":P": "😛", ":p": "😛", ":-P": "😛", ":O": "😮", ":o": "😮", ":-O": "😮", ":'(": "😢", ":'-(": "😢",
    "<3": "❤️", "</3": "💔", "xD": "😆", "XD": "😆", ":|": "😐", ":-|": "😐", ":/": "😕", ":-/": "😕",
    ":*": "😘", ":-*": "😘", "B)": "😎", "B-)": "😎", ">:(": "😠", ":3": "😊", "^^": "😊", "^_^": "😊",
    "-_-": "😑", "o_O": "😳", "O_o": "😳", ":$": "😳", ":x": "🤐", ":X": "🤐", "8)": "😎", "D:": "😱",
    ":')": "😂", ":,(": "😢", "<(\"\")": "🐧", "\\o/": "🙌",
}

_TOKEN = re.compile(r"(?<![A-Za-z0-9_]):([A-Za-z0-9_+\-]{1,40}):")
_EMOTICON_TOKEN = re.compile(r"(?:(?<=\s)|^)(" + "|".join(re.escape(k) for k in sorted(EMOTICONS, key=len, reverse=True)) + r")(?=\s|$)")


def lookup(code: str) -> str | None:
    return SHORTCODES.get(code.lower())


def search(prefix: str, *, limit: int = 8) -> list[tuple[str, str]]:
    """Codes starting with ``prefix`` first, then codes containing it."""
    q = prefix.lower()
    if not q:
        return []
    starts = [(c, e) for c, e in SHORTCODES.items() if c.startswith(q)]
    inside = [(c, e) for c, e in SHORTCODES.items() if q in c and not c.startswith(q)]
    starts.sort(key=lambda t: (len(t[0]), t[0]))
    inside.sort(key=lambda t: (len(t[0]), t[0]))
    return (starts + inside)[:limit]


def replace_shortcodes(text: str, *, emoticons: bool = True) -> str:
    """Replace every complete, known ``:code:`` (and, by default, standalone
    emoticons like ``:D``) in ``text``; unknown codes stay."""
    if ":" in text:
        def sub(m: re.Match) -> str:
            emoji = lookup(m.group(1))
            return emoji if emoji else m.group(0)
        text = _TOKEN.sub(sub, text)
    if emoticons and any(k[0] in text for k in EMOTICONS):
        text = _EMOTICON_TOKEN.sub(lambda m: EMOTICONS[m.group(1)], text)
    return text


def convert_before_cursor(text: str, cursor: int) -> tuple[str, int] | None:
    """After the user typed a space: if the word before it is a known
    ``:code:`` or emoticon, replace it. Returns (text, cursor) or None."""
    if cursor < 2 or cursor > len(text) or text[cursor - 1] != " ":
        return None
    head = text[:cursor - 1]
    start = max(head.rfind(" "), head.rfind("\n")) + 1
    token = head[start:]
    if not token:
        return None
    glyph = None
    m = re.fullmatch(r":([A-Za-z0-9_+\-]{1,40}):", token)
    if m:
        glyph = lookup(m.group(1))
    elif token in EMOTICONS:
        glyph = EMOTICONS[token]
    if not glyph:
        return None
    new = text[:start] + glyph + " " + text[cursor:]
    return new, start + len(glyph) + 1


_PARTIAL = re.compile(r"(?<![A-Za-z0-9_]):([A-Za-z0-9_+\-]{1,40})$")


def partial_at(text: str, cursor: int) -> tuple[int, str] | None:
    """If the text before ``cursor`` ends in ``:par``, return (start, "par")."""
    m = _PARTIAL.search(text[:cursor])
    if not m:
        return None
    return m.start(), m.group(1)
