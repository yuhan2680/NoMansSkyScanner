"""Session-fixed filters with pure spectrum calculation and existing map data."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from nms_scanner.single_run import NativePreconditionError

COLORS = {"yellow": "黄色", "red": "红色", "green": "绿色", "blue": "蓝色", "purple": "紫色"}
LETTER_COLORS = dict(
    F="yellow", G="yellow", M="red", K="red", E="green", O="blue", B="blue", X="purple", Y="purple"
)
LETTERS = {key: key for key in LETTER_COLORS}
DIGITS = {str(key): str(key) for key in range(10)}
SUFFIXES = {"none": "无", "p": "p", "f": "f", "pf": "pf"}
CATEGORIES = {"normal": "普通", "pirate": "海盗", "abandoned": "废弃", "empty": "无人"}
RACES = {"gek": "吉克", "vykeen": "维吉恩", "korvax": "科尔瓦克", "none": "无主导种族"}
RACE_IDS = {0: "gek", 1: "vykeen", 2: "korvax", 7: "none"}
TAGS = {
    "none": "无附加标签",
    "water": "水",
    "dissonant": "不协",
    "gas_giant": "气态巨行星",
    "worm": "蠕虫",
}
GROUPS = {
    "letters": (LETTERS, "光谱字母"),
    "digits": (DIGITS, "光谱数字"),
    "suffixes": (SUFFIXES, "光谱后缀"),
    "categories": (CATEGORIES, "星系类别"),
    "tags": (TAGS, "地图标签"),
    "races": (RACES, "主导种族"),
}
CLI_FIELDS = {
    "letters": "star-letters",
    "digits": "star-digits",
    "suffixes": "star-suffixes",
    "categories": "system-types",
    "tags": "system-tags",
    "races": "system-races",
}


@dataclass(frozen=True)
class StarFilter:
    enabled: bool = False
    letters: tuple[str, ...] = tuple(LETTERS)
    categories: tuple[str, ...] = tuple(CATEGORIES)
    digits: tuple[str, ...] = tuple(DIGITS)
    suffixes: tuple[str, ...] = tuple(SUFFIXES)
    tags: tuple[str, ...] = tuple(TAGS)
    races: tuple[str, ...] = tuple(RACES)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {"enabled", *GROUPS}:
            raise ValueError(
                "星系筛选配置需要 enabled、letters、digits、suffixes、categories、tags、races。"
            )
        if type(value["enabled"]) is not bool:
            raise ValueError("星系筛选开关必须为布尔值。")
        for key, (labels, title) in GROUPS.items():
            selected = value[key]
            if not isinstance(selected, list) or any(type(item) is not str for item in selected):
                raise ValueError(f"{title}必须是选项列表。")
            if len(selected) != len(set(selected)) or set(selected) - set(labels):
                raise ValueError(f"{title}含未知或重复选项。")
            if value["enabled"] and not selected:
                raise ValueError(f"启用筛选时，{title}至少勾选一项。")
        return cls(enabled=value["enabled"], **{key: tuple(value[key]) for key in GROUPS})

    def as_dict(self):
        return {"enabled": self.enabled, **{key: list(getattr(self, key)) for key in GROUPS}}

    def matches(self, details, *, include_tag=True):
        if not self.enabled:
            return True
        for group, field in (
            ("letters", "letter"),
            ("digits", "digit"),
            ("suffixes", "suffix"),
            ("categories", "category"),
            ("races", "race"),
        ):
            if details.get(field) not in getattr(self, group):
                return False
        return not include_tag or details.get("tag") in self.tags

    def describe(self):
        if not self.enabled:
            return "星系筛选：关闭，按原有规则选择可达目标。"
        return (
            "星系筛选："
            + "；".join(
                title + " " + "、".join(labels[item] for item in getattr(self, key))
                for key, (labels, title) in GROUPS.items()
            )
            + "。各组同时匹配。"
        )

    def arguments(self):
        if not self.enabled:
            return []
        args = ["--star-filter-enabled"]
        for key, flag in CLI_FIELDS.items():
            args.extend(["--" + flag, *getattr(self, key)])
        return args


def add_filter_arguments(parser):
    parser.add_argument("--star-filter-enabled", action="store_true", help="启用星系筛选")
    for key, (labels, title) in GROUPS.items():
        parser.add_argument(
            "--" + CLI_FIELDS[key], nargs="+", choices=labels, help="允许的" + title
        )


def selection_from_args(args):
    enabled = getattr(args, "star_filter_enabled", False)
    values = {key: getattr(args, flag.replace("-", "_"), None) for key, flag in CLI_FIELDS.items()}
    if not enabled and any(value is not None for value in values.values()):
        raise ValueError("指定星系类型时，需要同时启用 --star-filter-enabled。")
    return StarFilter.from_dict(
        {
            "enabled": enabled,
            **{
                key: list(GROUPS[key][0]) if value is None else value
                for key, value in values.items()
            },
        }
    )


def spectrum(seed, color):
    """170671 RVA 0x151AA40, reproduced without executing native code.

    The map passes its selected universal address as the seed. Green consumes
    no letter-choice draw. Keep unsigned wraparound at the same instruction widths.
    """
    if type(seed) is not int or not 0 <= seed < 1 << 64 or color not in COLORS:
        raise NativePreconditionError("star_filter_invalid_spectrum")
    mask32, mask64 = (1 << 32) - 1, (1 << 64) - 1
    hashed = ((seed ^ (seed >> 33)) * 0x64DD81482CBD31D7) & mask64
    hashed = ((hashed ^ (hashed >> 33)) * 0xE36AA5C613612997) & mask64
    hashed ^= hashed >> 33
    low = hashed & mask32
    carry = (((low >> 16) | (low << 16)) & mask32) ^ (hashed >> 32) ^ low
    state = (low + (low == 0)) & mask32

    def draw():
        nonlocal state, carry
        value = state * 0x5A76F899 + carry
        state, carry = value & mask32, value >> 32
        return state

    if color == "green":
        letter = "E"
    else:
        odd, even = {
            "yellow": ("F", "G"),
            "red": ("M", "K"),
            "blue": ("O", "B"),
            "purple": ("X", "Y"),
        }[color]
        letter = odd if draw() & 1 else even
    digit = str((draw() * 10) >> 32)
    suffix_draw = draw()
    suffix = ("p" if suffix_draw & 1 else "") + ("f" if (carry - suffix_draw) & 1 else "")
    return {"letter": letter, "digit": digit, "suffix": suffix or "none"}


def map_tag(flags):
    """The displayed label uses priority gas giant > worm > dissonance > water."""
    if len(flags) != 4 or any(flag not in (0, 1) for flag in flags):
        raise NativePreconditionError("star_filter_invalid_panel")
    for index, tag in ((3, "gas_giant"), (1, "worm"), (2, "dissonant"), (0, "water")):
        if flags[index]:
            return tag
    return "none"


def validate_definition(config):
    if config.get("schema_version") != 2 or config.get("default_enabled") is not False:
        raise ValueError("星系筛选必须默认关闭。")
    attempts = config.get("max_candidate_attempts")
    if type(attempts) is not int or not 1 <= attempts <= 256:
        raise ValueError("筛选候选上限必须为 1～256。")
    layout = config.get("layout", {})
    size = layout.get("attributes_size")
    if type(size) is not int or size != 48:
        raise ValueError("星系分类缓冲区大小与当前版本不符。")
    for key, width in (
        ("star_type_offset", 4),
        ("race_offset", 4),
        ("abandoned_offset", 1),
        ("pirate_offset", 1),
    ):
        offset = layout.get(key)
        if type(offset) is not int or not 0 <= offset <= size - width:
            raise ValueError("星系分类字段偏移无效。")
    fields = [
        set(range(layout[key], layout[key] + width))
        for key, width in (
            ("star_type_offset", 4),
            ("race_offset", 4),
            ("abandoned_offset", 1),
            ("pirate_offset", 1),
        )
    ]
    if len(set.union(*fields)) != sum(map(len, fields)):
        raise ValueError("星系分类字段不得重叠。")
    if layout.get("star_types") != {
        "0": "yellow",
        "1": "green",
        "2": "blue",
        "3": "red",
        "4": "purple",
    }:
        raise ValueError("星系颜色枚举与当前版本不符。")
    if layout.get("empty_race") != 7 or layout.get("populated_races") != [0, 1, 2]:
        raise ValueError("星系居民类型枚举与当前版本不符。")
    expected_panel = {
        "map_offset": 0x21C0,
        "query_offset": 0x5D0,
        "rendered_query_offset": 0x5C0,
        "star_type_offset": 0xA4C,
        "flags_offset": 0xA80,
    }
    if config.get("panel") != expected_panel:
        raise ValueError("地图标签读取布局与已核查版本不符。")
    return config


def apply_star_filter(profile, selection):
    selection = StarFilter.from_dict(selection)
    profile["star_filter_selection"] = selection.as_dict()
    if selection.enabled:
        profile["single_trial"]["max_candidate_attempts"] = profile["star_filter"][
            "max_candidate_attempts"
        ]
    return selection


def classify_attributes(raw: bytes, layout: dict):
    if len(raw) != layout["attributes_size"]:
        raise NativePreconditionError("star_filter_invalid_attributes")
    star_type = struct.unpack_from("<I", raw, layout["star_type_offset"])[0]
    race = struct.unpack_from("<I", raw, layout["race_offset"])[0]
    abandoned, pirate = raw[layout["abandoned_offset"]], raw[layout["pirate_offset"]]
    color = layout["star_types"].get(str(star_type))
    if color is None or abandoned not in (0, 1) or pirate not in (0, 1):
        raise NativePreconditionError("star_filter_invalid_attributes")
    if race not in (*layout["populated_races"], layout["empty_race"]):
        raise NativePreconditionError("star_filter_unknown_category")
    if pirate and (abandoned or race == layout["empty_race"]):
        raise NativePreconditionError("star_filter_conflicting_attributes")
    # Empty population takes precedence: generation can retain the abandoned flag.
    if race == layout["empty_race"]:
        category = "empty"
    elif abandoned:
        category = "abandoned"
    elif pirate:
        category = "pirate"
    else:
        category = "normal"
    return color, category, RACE_IDS[race]
