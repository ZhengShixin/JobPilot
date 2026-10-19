"""Offline emulation of how the LinkedIn jobs actor (fantastic-jobs/advanced-linkedin-job-search-api) matches its
text filters, so a search plan can be checked before it costs anything.

    group_matches(filters, title, company, industry, description) -> bool
    plan_groups(groups, title, ...) -> [names of the groups that would return the posting]

Inferred from more than 50,000 rows the actor returned, with no counterexample: PostgreSQL "simple" full-text search.
Lowercase, no stemming and no stop words ("Research" does not match "Researcher"); "x:*" is a prefix match; a
multi-word term is a phrase (adjacent words); "a/b" is ONE token, so "Trader/Researcher" needs "Researcher:*"; a
hyphenated word yields the compound and its parts. Terms of one list are OR'ed, different fields AND'ed.
industryFilter is an exact match on the company's LinkedIn industry; organizationSearch is a phrase match on the
company name; descriptionSearch searches the title and the description. descriptionExclusionSearch showed no effect
upstream, so it is ignored here too.
"""
import html
import re

_FILE = r"[A-Za-z0-9_.]*[A-Za-z0-9]/[A-Za-z0-9][A-Za-z0-9_./]*"
_HYPH = r"[A-Za-z0-9À-ÖØ-öø-ÿ]+(?:-[A-Za-z0-9À-ÖØ-öø-ÿ]+)+"
_WORD = r"[A-Za-z0-9À-ÖØ-öø-ÿ]+"
_RX = re.compile("(" + _FILE + ")|(" + _HYPH + ")|(" + _WORD + ")")


def tokens(text):
    """[(position, lexeme)] in the parser's order."""
    out, pos = [], 0
    for m in _RX.finditer(html.unescape(text or "")):
        slash, hyphen, word = m.groups()
        if slash:
            pos += 1
            out.append((pos, slash.lower().rstrip(".-_/")))
        elif hyphen:
            out.append((pos + 1, hyphen.lower()))      # the compound shares the first part's position
            for part in hyphen.split("-"):
                pos += 1
                out.append((pos, part.lower()))
        else:
            pos += 1
            out.append((pos, word.lower()))
    return out


class Doc:
    __slots__ = ("bypos",)

    def __init__(self, text):
        self.bypos = {}
        for p, lexeme in tokens(text):
            self.bypos.setdefault(lexeme, []).append(p)

    def positions(self, lexeme, prefix):
        if not prefix:
            return self.bypos.get(lexeme, ())
        return [p for word, ps in self.bypos.items() if word.startswith(lexeme) for p in ps]


def parse(term):
    """"Quant:*" -> [("quant", True)]; "Data Scientist" -> [("data", False), ("scientist", False)]."""
    return [(m.group(1).lower(), bool(m.group(2))) for m in re.finditer(r"([0-9A-Za-zÀ-ÖØ-öø-ÿ]+)(:\*)?", term)]


def term_matches(parts, doc):
    if not parts:
        return False
    starts = doc.positions(*parts[0])
    if len(parts) == 1 or not starts:
        return bool(starts)
    rest = []
    for k, (lexeme, prefix) in enumerate(parts[1:], 1):
        found = set(doc.positions(lexeme, prefix))
        if not found:
            return False
        rest.append((k, found))
    return any(all(s + k in found for k, found in rest) for s in starts)


def any_term(terms, doc):
    return any(term_matches(parse(t), doc) for t in terms or [])


def matching_terms(terms, text):
    doc = Doc(text)
    return [t for t in terms or [] if term_matches(parse(t), doc)]


def group_matches(filters, title, company="", industry="", description=""):
    """Whether one search group (actor input filters) would return a posting with these fields."""
    if filters.get("industryFilter") and industry not in set(filters["industryFilter"]):
        return False
    if filters.get("industryExclusionFilter") and industry in set(filters["industryExclusionFilter"]):
        return False
    t = Doc(title)
    if filters.get("titleSearch") and not any_term(filters["titleSearch"], t):
        return False
    if filters.get("titleExclusionSearch") and any_term(filters["titleExclusionSearch"], t):
        return False
    if filters.get("organizationSearch") or filters.get("organizationExclusionSearch"):
        o = Doc(company)
        if filters.get("organizationSearch") and not any_term(filters["organizationSearch"], o):
            return False
        if filters.get("organizationExclusionSearch") and any_term(filters["organizationExclusionSearch"], o):
            return False
    if filters.get("descriptionSearch") and not (any_term(filters["descriptionSearch"], Doc(description))
                                                 or any_term(filters["descriptionSearch"], t)):
        return False
    return True


def plan_groups(groups, title, company="", industry="", description=""):
    return [name for name, filters in groups if group_matches(filters, title, company, industry, description)]
