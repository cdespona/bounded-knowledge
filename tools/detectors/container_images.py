"""Conservative Dockerfile FROM literal inventory."""

import os
from pathlib import Path
import re

from landscape_core.observations import observation
from landscape_core.safety import (
    MAX_FILE_BYTES,
    directory_exclusion_reason,
    exclusion_reason,
)


NAME_PART = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
IMAGE_NAME = re.compile(
    r"^(?:" + NAME_PART + r"(?::[0-9]+)?/)?"
    + NAME_PART + r"(?:/" + NAME_PART + r")*"
    + r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?"
    + r"(?:@[A-Za-z][A-Za-z0-9_.+-]*:[A-Za-z0-9=_+.-]{32,})?$"
)
ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
CANDIDATE_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
FROM = re.compile(r"^FROM(?:[ \t]|$)", re.IGNORECASE)
ESCAPE_DIRECTIVE = re.compile(r"^[ \t]*#[ \t]*escape[ \t]*=", re.IGNORECASE)
HEREDOC = re.compile(
    r"(?:^|[ \t])<<-?[ \t]*(?:[A-Za-z0-9_]+|'[^'\r\n]+'|\"[^\"\r\n]+\")"
)
PARSER_DIRECTIVE = re.compile(
    r"^[ \t]*#[ \t]*[A-Za-z][A-Za-z0-9_-]*[ \t]*="
)

DETAILS = {
    "dynamic-image-reference":
        "The Dockerfile FROM reference uses dynamic variable syntax.",
    "templated-image-reference":
        "The Dockerfile FROM reference uses unsupported template syntax.",
    "unsupported-from-option":
        "Dockerfile FROM options are outside the version 1 literal contract.",
    "unsupported-from-continuation":
        "Continued Dockerfile instructions are outside the version 1 literal contract.",
    "unsupported-escape-directive":
        "Dockerfile escape directives are outside the version 1 parser contract.",
    "unsupported-heredoc":
        "Dockerfile heredoc syntax is outside the version 1 parser contract.",
    "malformed-from":
        "The Dockerfile FROM instruction does not match the supported physical-line shape.",
    "invalid-image-reference":
        "The Dockerfile FROM reference is outside the conservative literal image grammar.",
    "invalid-stage-alias":
        "The Dockerfile FROM stage alias is outside the supported literal alias grammar.",
    "duplicate-stage-alias":
        "The Dockerfile FROM stage alias was already declared earlier in the file.",
    "ambiguous-stage-reference":
        "The Dockerfile FROM reference is ambiguous against earlier stage aliases.",
    "unsupported-stage-source":
        "The Dockerfile FROM reference names a stage declared by an unsupported instruction.",
}


def _candidate(name):
    if name == "Dockerfile":
        return True
    if name.startswith("Dockerfile."):
        return bool(CANDIDATE_PART.fullmatch(name[len("Dockerfile."):]))
    if name.endswith(".Dockerfile"):
        return bool(CANDIDATE_PART.fullmatch(name[:-len(".Dockerfile")]))
    return False


def _candidate_files(root):
    found = []
    for current, directory_names, file_names in os.walk(root, topdown=True):
        current_path = Path(current)
        directory_names[:] = [
            name for name in sorted(directory_names)
            if not (current_path / name).is_symlink()
            and directory_exclusion_reason(
                (current_path / name).relative_to(root)
            ) is None
        ]
        for file_name in sorted(file_names):
            if not _candidate(file_name):
                continue
            path = current_path / file_name
            if path.is_symlink():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if exclusion_reason(path.relative_to(root), size) is None:
                found.append(path)
    return sorted(found, key=lambda path: path.relative_to(root).as_posix())


def _continued(line):
    stripped = line.rstrip(" \t")
    count = len(stripped) - len(stripped.rstrip("\\"))
    return count % 2 == 1


def _continuation_lines(lines):
    """Return FROM-looking line numbers participating in continuation chains."""
    unsupported = set()
    index = 0
    while index < len(lines):
        if lines[index].lstrip(" \t").startswith("#") or not _continued(lines[index]):
            index += 1
            continue
        chain = [index]
        index += 1
        while index < len(lines):
            chain.append(index)
            if lines[index].lstrip(" \t").startswith("#"):
                index += 1
                continue
            continued = _continued(lines[index])
            index += 1
            if not continued:
                break
        if any(FROM.match(lines[position].lstrip(" \t")) for position in chain):
            unsupported.update(position + 1 for position in chain)
    return unsupported


class ContainerImageDetector:
    name = "container-images"
    version = 1

    def _item(self, root, path, repository, commit, line, kind, value):
        return observation(
            kind=kind,
            repository=repository,
            commit=commit,
            detector=self.name,
            detector_version=self.version,
            source_path=path.relative_to(root).as_posix(),
            source_lines=str(line),
            value=value,
        )

    def _gap(self, root, path, repository, commit, line, code):
        return self._item(
            root, path, repository, commit, line, "container-gap",
            {
                "context": "dockerfile-from",
                "form": "dockerfile-from",
                "code": code,
                "detail": DETAILS[code],
            },
        )

    def _reference(
        self, root, path, repository, commit, line, kind, role, form, field,
        reference, alias=None,
    ):
        value = {"role": role, "form": form, field: reference}
        if alias is not None:
            value["stageAlias"] = alias
        return self._item(
            root, path, repository, commit, line, kind, value
        )

    @staticmethod
    def _unsupported_reference_code(reference):
        if "$" in reference or "$(" in reference:
            return "dynamic-image-reference"
        if any(marker in reference for marker in ("{{", "}}", "{%", "%}", "<%", "%>")):
            return "templated-image-reference"
        return "invalid-image-reference"

    def _extract(self, root, path, repository, commit, text):
        lines = text.split("\n")
        fatal = []
        parser_directive_region = True
        for number, line in enumerate(lines, 1):
            stripped = line.lstrip(" \t")
            if parser_directive_region:
                if ESCAPE_DIRECTIVE.match(line):
                    fatal.append((number, "unsupported-escape-directive"))
                elif PARSER_DIRECTIVE.match(line):
                    pass
                else:
                    parser_directive_region = False
            if stripped and not stripped.startswith("#") and HEREDOC.search(line):
                fatal.append((number, "unsupported-heredoc"))
        if fatal:
            line, code = sorted(fatal)[0]
            return [self._gap(root, path, repository, commit, line, code)]

        continuation_lines = _continuation_lines(lines)
        result = []
        aliases = {}
        aliases_by_casefold = {}
        continuation_reported = set()
        for number, line in enumerate(lines, 1):
            stripped = line.strip(" \t")
            if not stripped or stripped.startswith("#"):
                continue
            if number in continuation_lines:
                if FROM.match(line.lstrip(" \t")) and number not in continuation_reported:
                    result.append(self._gap(
                        root, path, repository, commit, number,
                        "unsupported-from-continuation",
                    ))
                    continuation_reported.add(number)
                continue
            if not FROM.match(line.lstrip(" \t")):
                continue

            tokens = re.split(r"[ \t]+", stripped)
            alias = None
            alias_valid = False
            if len(tokens) >= 2 and tokens[1].startswith("--"):
                result.append(self._gap(
                    root, path, repository, commit, number,
                    "unsupported-from-option",
                ))
                if (
                    len(tokens) >= 4 and tokens[-2].lower() == "as"
                    and ALIAS.fullmatch(tokens[-1])
                ):
                    alias = tokens[-1]
                    if alias in aliases:
                        aliases[alias] = "duplicate"
                        result.append(self._gap(
                            root, path, repository, commit, number,
                            "duplicate-stage-alias",
                        ))
                    else:
                        aliases[alias] = "unsupported"
                    aliases_by_casefold.setdefault(
                        alias.casefold(), set()
                    ).add(alias)
                continue
            shape_valid = len(tokens) in {2, 4}
            if len(tokens) == 4 and tokens[2].lower() == "as":
                alias = tokens[3]
                alias_valid = bool(ALIAS.fullmatch(alias))
            elif len(tokens) == 4:
                shape_valid = False
            if not shape_valid:
                result.append(self._gap(
                    root, path, repository, commit, number, "malformed-from"
                ))
                continue

            reference = tokens[1]
            if any(character in reference for character in ('"', "'", "#")):
                code = "malformed-from"
            elif alias is not None and not alias_valid:
                code = "invalid-stage-alias"
            else:
                code = None

            prior_state = aliases.get(reference)
            folded_states = aliases_by_casefold.get(reference.casefold(), set())
            kind = role = form = field = literal = None
            if code is None and prior_state == "supported":
                kind, role, form, field, literal = (
                    "container-stage-reference", "build-stage-base",
                    "dockerfile-from-stage", "stage", reference,
                )
            elif code is None and prior_state == "unsupported":
                code = "unsupported-stage-source"
            elif code is None and prior_state == "duplicate":
                code = "ambiguous-stage-reference"
            elif code is None and folded_states:
                code = "ambiguous-stage-reference"
            elif code is None and reference == "scratch":
                kind, role, form, field, literal = (
                    "container-image-reference", "scratch-base",
                    "dockerfile-from", "image", reference,
                )
            elif code is None and IMAGE_NAME.fullmatch(reference):
                kind, role, form, field, literal = (
                    "container-image-reference", "base-image",
                    "dockerfile-from", "image", reference,
                )
            elif code is None:
                code = self._unsupported_reference_code(reference)

            if code is not None:
                result.append(self._gap(
                    root, path, repository, commit, number, code
                ))
            else:
                result.append(self._reference(
                    root, path, repository, commit, number, kind, role, form,
                    field, literal, alias,
                ))

            if alias is not None and alias_valid:
                previous = aliases.get(alias)
                if previous is not None:
                    aliases[alias] = "duplicate"
                    result.append(self._gap(
                        root, path, repository, commit, number,
                        "duplicate-stage-alias",
                    ))
                else:
                    aliases[alias] = "supported" if code is None else "unsupported"
                aliases_by_casefold.setdefault(alias.casefold(), set()).add(alias)
        return result

    def detect(self, root, repository, commit):
        observations = []
        excluded = []
        for path in _candidate_files(root):
            relative = path.relative_to(root).as_posix()
            try:
                with path.open("rb") as source:
                    content = source.read(MAX_FILE_BYTES + 1)
                if len(content) > MAX_FILE_BYTES:
                    continue
                text = content.decode("utf-8")
            except (OSError, UnicodeError):
                excluded.append({
                    "path": relative,
                    "reason": "unsupported-or-unreadable-content",
                })
                continue
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            observations.extend(self._extract(
                root, path, repository, commit, text
            ))
        unique = {item["id"]: item for item in observations}
        return [unique[item_id] for item_id in sorted(unique)], excluded
