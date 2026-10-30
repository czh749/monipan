"""Parse only the small CHECK grammar used by the frozen baseline.

Compare expression trees rather than stripping parentheses, which could change
AND/OR precedence. Unknown syntax blocks adoption instead of being guessed.
"""

from decimal import Decimal
import re


TOKEN = re.compile(r"\s*(?:(_[A-Za-z0-9]+)?('(?:''|[^'])*')|`([A-Za-z_][A-Za-z0-9_]*)`|"
                   r"([A-Za-z_][A-Za-z0-9_]*)|([0-9]+(?:\.[0-9]+)?)|(>=|<=|<>|!=|[(),=<>]))")


def expression_tree(source):
    tokens = []
    position = 0
    while source[position:].strip():
        match = TOKEN.match(source, position)
        if not match:
            raise ValueError("CHECK expression syntax requires manual review")
        _charset, string, quoted, word, number, punctuation = match.groups()
        if string is not None:
            tokens.append(("string", string[1:-1].replace("''", "'")))
        elif number is not None:
            tokens.append(("number", Decimal(number)))
        elif quoted is not None:
            tokens.append(("identifier", quoted.lower()))
        elif word is not None and word.upper() not in {"AND", "OR", "IN", "IS", "NOT", "NULL"}:
            tokens.append(("identifier", word.lower()))
        else:
            tokens.append((word or punctuation).upper())
        position = match.end()
    cursor = 0

    def take(value=None):
        nonlocal cursor
        if cursor >= len(tokens) or (value is not None and tokens[cursor] != value):
            raise ValueError("CHECK expression requires manual review")
        result = tokens[cursor]
        cursor += 1
        return result

    def next_is(value):
        return cursor < len(tokens) and tokens[cursor] == value

    def atom():
        if next_is("("):
            take("(")
            value = disjunction()
            take(")")
            return value
        value = take()
        if not isinstance(value, tuple):
            raise ValueError("Unsupported CHECK operand")
        return value

    def comparison():
        left = atom()
        if next_is("IS"):
            take("IS")
            negative = next_is("NOT")
            if negative:
                take("NOT")
            take("NULL")
            return ("is_not_null" if negative else "is_null", left)
        if next_is("IN"):
            take("IN")
            take("(")
            values = [atom()]
            while next_is(","):
                take(",")
                values.append(atom())
            take(")")
            return ("in", left, tuple(values))
        if cursor < len(tokens) and tokens[cursor] in ("=", "!=", "<>", ">", "<", ">=", "<="):
            operator = take()
            return ("!=" if operator == "<>" else operator, left, atom())
        return left

    def conjunction():
        value = comparison()
        while next_is("AND"):
            take("AND")
            value = ("and", value, comparison())
        return value

    def disjunction():
        value = conjunction()
        while next_is("OR"):
            take("OR")
            value = ("or", value, conjunction())
        return value

    result = disjunction()
    if cursor != len(tokens):
        raise ValueError("Unconsumed CHECK expression tokens")
    return result
