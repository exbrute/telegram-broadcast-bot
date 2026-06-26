import csv
import io
import re

USERNAME_PATTERN = re.compile(r"@?([a-zA-Z][a-zA-Z0-9_]{4,31})")


def parse_usernames(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for match in USERNAME_PATTERN.finditer(text):
        username = match.group(1).lower()
        if username not in seen:
            seen.add(username)
            result.append(username)
    return result


def parse_usernames_from_csv(content: str) -> list[str]:
    usernames: list[str] = []
    reader = csv.reader(io.StringIO(content))
    for row in reader:
        if not row:
            continue
        for cell in row:
            usernames.extend(parse_usernames(cell))
    return _dedupe(usernames)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
