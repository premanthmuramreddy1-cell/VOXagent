"""Turn spoken code terms into code text: "open bracket" -> "(", "line forty two" -> "line 42"."""

import re

SYMBOLS = {
    "open square bracket": "[", "close square bracket": "]",
    "open curly brace": "{", "close curly brace": "}",
    "open brace": "{", "close brace": "}",
    "open bracket": "(", "close bracket": ")",
    "open paren": "(", "close paren": ")",
    "underscore": "_", "semicolon": ";", "double equals": "==",
}
UNITS = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen " \
        "sixteen seventeen eighteen nineteen".split()
TENS = dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10), strict=True))
NUMBER_WORD = "|".join([*UNITS, *TENS, "hundred", "and"])
STOP = {"in", "to", "on", "at", "and", "then", "for", "with", "from", "everywhere", "into", "by"}
CASES = {
    "camel case": lambda w: w[0] + "".join(x.capitalize() for x in w[1:]),
    "snake case": "_".join,
    "pascal case": lambda w: "".join(x.capitalize() for x in w),
    "kebab case": "-".join,
}


def _number(words: list[str]) -> int:
    total = 0
    for word in words:
        if word in UNITS:
            total += UNITS.index(word)
        elif word in TENS:
            total += TENS[word]
        elif word == "hundred":
            total = max(total, 1) * 100
    return total


def _line_numbers(match: re.Match) -> str:
    return f"{match.group(1)} {_number(match.group(2).lower().split())}"


def _case(match: re.Match) -> str:
    words, rest = [], match.group(2).split()
    while rest and rest[0].lower() not in STOP and rest[0].isalnum():
        words.append(rest.pop(0).lower())
    if not words:
        return match.group(0)
    return " ".join([CASES[match.group(1).lower()](words), *rest])


def normalize(text: str) -> str:
    for phrase, symbol in SYMBOLS.items():
        text = re.sub(rf"\b{phrase}\b", symbol, text, flags=re.I)
    text = re.sub(rf"\b(line|lines)\s+((?:(?:{NUMBER_WORD})\b[\s-]*)+)", _line_numbers, text, flags=re.I)
    text = re.sub(r"\b(camel case|snake case|pascal case|kebab case)\s+([^,.;:!?]+)", _case, text, flags=re.I)
    return re.sub(r"\s{2,}", " ", text).strip()
