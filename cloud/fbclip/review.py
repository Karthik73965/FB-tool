"""Ending + caption review for the website: the same logic as the desktop app (app.py Api._lines / fix_caption),
working on a job's transcript and edits instead of local files."""
import difflib, re
import reframe


def norm(w):
    return re.sub(r"[^\w']", "", w.lower())


def lines(sn, edits):
    allw = [(si, wi, w["s"]) for si, s in enumerate(sn) for wi, w in enumerate(s[3])]
    fm = {}
    for f in edits.get("words", []):
        if not allw:
            break
        si, wi, t = min(allw, key=lambda x: abs(x[2] - f["at"]))
        if abs(t - f["at"]) < 0.6:
            fm[(si, wi)] = f["to"]
    out = []
    for si, s in enumerate(sn):
        ws = [fm.get((si, wi), w["w"].strip()) for wi, w in enumerate(s[3])]
        out.append({"s": s[0], "e": s[1], "text": " ".join(x for x in ws if x), "fixed": any(k[0] == si for k in fm)})
    return out, fm


def review(tr, edits):
    return lines(reframe.sentences(tr), edits)[0]


def fix_caption(tr, edits, si, text):
    """turn an edited sentence into per-word fixes ({"at": word start, "to": new text}, "" deletes a word)"""
    sn = reframe.sentences(tr)
    _, fm = lines(sn, edits)
    words = sn[si][3]
    cur = [fm.get((si, wi), w["w"].strip()) for wi, w in enumerate(words)]
    toks, target = text.split(), list(cur)
    sm = difflib.SequenceMatcher(a=[norm(x) for x in cur], b=[norm(x) for x in toks], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        rep = toks[j1:j2]
        if tag == "equal":
            target[i1:i2] = rep
        elif tag in ("replace", "delete"):
            for k in range(i2 - i1):
                target[i1 + k] = rep[k] if k < len(rep) else ""
            if len(rep) > i2 - i1:
                target[i2 - 1] = " ".join([target[i2 - 1]] + rep[i2 - i1:])
        elif tag == "insert" and words:
            k = max(0, i1 - 1)
            target[k] = (target[k] + " " + " ".join(rep)) if i1 else (" ".join(rep) + " " + target[0])
    mine = {round(w["s"], 2) for w in words}
    fixes = [f for f in edits.get("words", []) if round(f["at"], 2) not in mine]
    fixes += [{"at": round(w["s"], 2), "to": target[wi]} for wi, w in enumerate(words) if target[wi] != w["w"].strip()]
    edits = dict(edits, words=fixes)
    return edits, lines(sn, edits)[0]


def keep_broll(clean, sent, old):
    """B-roll file paths only ever come from the worker. The browser can drop entries (sends the "at" of the ones
    to keep) or omit "broll" to keep them all; it never sends paths."""
    if "broll" not in old:
        return clean
    keep = sent.get("broll")
    if keep is None:
        clean["broll"] = old["broll"]
    else:
        ats = [float(k["at"]) for k in keep if isinstance(k, dict) and isinstance(k.get("at"), (int, float))]
        clean["broll"] = [b for b in old["broll"] if any(abs(b["at"] - a) < 0.01 for a in ats)]
    return clean


def clean_edits(e):
    """only the fields reframe reads, with sane types"""
    end = e.get("end")
    return {"end": float(end) if end not in (None, "") else None, "keep_outro": bool(e.get("keep_outro")),
            "words": [{"at": float(f["at"]), "to": str(f["to"])[:80]} for f in e.get("words", [])][:2000],
            "wave": [[float(a), float(b)] for a, b in e.get("wave", [])][:200],
            "face": [[float(a), float(b)] for a, b in e.get("face", [])][:200]}
