"""Lexical helpers for Java and Kotlin sources: comments, string literals, balanced groups, annotations."""

import re


class StringLiteral:
    """A string literal found outside comments; ``value`` is the raw text between the quotes."""

    def __init__(self, start, end, value):
        self.start = start
        self.end = end
        self.value = value


def scan(text):
    """Return (clean, code, strings).

    ``clean`` blanks comments; ``code`` additionally blanks string contents. Both keep offsets and newlines
    identical to ``text`` so that offsets found in one are valid in the others.
    """
    clean, code, strings = list(text), list(text), []
    length, index = len(text), 0

    def blank(target, start, end):
        for position in range(start, end):
            if target[position] != "\n":
                target[position] = " "

    while index < length:
        pair = text[index:index + 2]
        if pair == "//":
            end = text.find("\n", index)
            end = length if end < 0 else end
            blank(clean, index, end)
            blank(code, index, end)
            index = end
        elif pair == "/*":
            end = text.find("*/", index + 2)
            end = length if end < 0 else end + 2
            blank(clean, index, end)
            blank(code, index, end)
            index = end
        elif text.startswith('"""', index):
            end = text.find('"""', index + 3)
            if end < 0:
                break
            strings.append(StringLiteral(index, end + 3, text[index + 3:end]))
            blank(code, index + 3, end)
            index = end + 3
        elif text[index] == '"':
            position = index + 1
            while position < length and text[position] not in '"\n':
                position += 2 if text[position] == "\\" else 1
            if position >= length or text[position] != '"':
                index += 1
                continue
            strings.append(StringLiteral(index, position + 1, text[index + 1:position]))
            blank(code, index + 1, position)
            index = position + 1
        elif text[index] == "'":
            position = index + 1
            while position < length and text[position] not in "'\n":
                position += 2 if text[position] == "\\" else 1
            if position < length and text[position] == "'":
                blank(code, index + 1, position)
                index = position + 1
            else:
                index += 1
        else:
            index += 1
    return "".join(clean), "".join(code), strings


def line_starts(text):
    starts, position = [0], 0
    while True:
        position = text.find("\n", position)
        if position < 0:
            return starts
        position += 1
        starts.append(position)


def line_of(starts, offset):
    low, high = 0, len(starts) - 1
    while low < high:
        middle = (low + high + 1) // 2
        if starts[middle] <= offset:
            low = middle
        else:
            high = middle - 1
    return low + 1


_OPEN, _CLOSE = "([{", ")]}"


def group_end(code, open_index):
    """Return the offset of the bracket closing the one at ``open_index``, or -1."""
    depth = 0
    for position in range(open_index, len(code)):
        character = code[position]
        if character in _OPEN:
            depth += 1
        elif character in _CLOSE:
            depth -= 1
            if depth == 0:
                return position
    return -1


def split_arguments(code, open_index):
    """Split the arguments of the group opened at ``open_index``; return (spans, close_index) or (None, -1)."""
    close = group_end(code, open_index)
    if close < 0:
        return None, -1
    spans, depth, begin = [], 0, open_index + 1
    for position in range(open_index + 1, close):
        character = code[position]
        if character in _OPEN:
            depth += 1
        elif character in _CLOSE:
            depth -= 1
        elif character == "," and depth == 0:
            spans.append((begin, position))
            begin = position + 1
    spans.append((begin, close))
    return [span for span in spans if code[span[0]:span[1]].strip()], close


def statement_end(code, start):
    """Return the offset of the ';' ending the statement that contains ``start`` (bracket aware)."""
    depth = 0
    for position in range(start, len(code)):
        character = code[position]
        if character in _OPEN:
            depth += 1
        elif character in _CLOSE:
            depth -= 1
            if depth < 0:
                return position
        elif character == ";" and depth == 0:
            return position
    return len(code)


class Annotation:
    def __init__(self, name, start, end, open_index):
        self.name = name
        self.start = start
        self.end = end
        self.open_index = open_index


_ANNOTATION = re.compile(r"@([A-Za-z_][\w.]*)")
_MODIFIERS = re.compile(r"^\s*(?:(?:public|protected|private|abstract|final|static|open|data|sealed|internal)\s+)*$")
_DECLARATION = re.compile(r"\b(class|interface|record|object)\s+([A-Za-z_]\w*)")


def annotations(code):
    found, position = [], 0
    while True:
        match = _ANNOTATION.search(code, position)
        if match is None:
            return found
        end, open_index = match.end(), None
        probe = end
        while probe < len(code) and code[probe] in " \t\r\n":
            probe += 1
        if probe < len(code) and code[probe] == "(":
            close = group_end(code, probe)
            if close > 0:
                end, open_index = close + 1, probe
        found.append(Annotation(match.group(1).split(".")[-1], match.start(), end, open_index))
        position = max(match.end(), end if open_index is None else probe + 1)


def declarations(code, found):
    """Yield (kind, name, offset, attached annotations) for each class-like declaration."""
    for match in _DECLARATION.finditer(code):
        attached, cursor = [], match.start()
        for annotation in reversed([item for item in found if item.end <= cursor]):
            if _MODIFIERS.match(code[annotation.end:cursor]):
                attached.append(annotation)
                cursor = annotation.start
            else:
                break
        yield match.group(1), match.group(2), match.start(), list(reversed(attached))
