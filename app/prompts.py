"""Prompt construction. The manuscript text is only included when the caller passes `context`
(the redacted text the user approved)."""

REC = {"accept": "Accept", "minor": "Minor revision", "major": "Major revision", "reject": "Reject"}


def _project_header(p):
    lines = [f"Journal: {p.get('journal') or 'unknown'}"]
    if p.get("recommendation"):
        lines.append(f"Reviewer's intended recommendation: {REC.get(p['recommendation'], p['recommendation'])}")
    return "\n".join(lines)


def chat_messages(p, notes, history, message, context=None):
    sys = (
        "You are a thoughtful senior academic colleague helping a peer reviewer think through their review. "
        "The reviewer writes fragmentary notes, often mixing Chinese and English. Discuss their points critically "
        "and constructively: question weak arguments, point out what evidence would be needed, suggest what to ask "
        "the authors, and flag where a criticism might be unfair. Do not invent details about the manuscript "
        "that are not in the notes or the provided text. Answer in the language the reviewer uses.\n\n"
        + _project_header(p))
    if context:
        sys += ("\n\nManuscript text (redacted by the reviewer; confidential, use only for this discussion):\n"
                "<<<MANUSCRIPT\n" + context[:120000] + "\nMANUSCRIPT>>>")
    if notes.strip():
        sys += "\n\nReviewer's current notes:\n<<<NOTES\n" + notes[:30000] + "\nNOTES>>>"
    msgs = [{"role": "system", "content": sys}]
    for h in history[-30:]:
        msgs.append({"role": h["role"], "content": h["content"]})
    msgs.append({"role": "user", "content": message})
    return msgs


def review_messages(p, kind, notes, chat, anns, instructions, style, previous, context, remote):
    if kind == "editor":
        task = ("Write the CONFIDENTIAL COMMENTS TO THE EDITOR: a short, frank paragraph or two giving the "
                "recommendation and the main reasons, any concerns (e.g. ethics, novelty, scope fit, suspected "
                "problems) that should not go to the authors.")
    else:
        task = ("Write the COMMENTS TO THE AUTHORS: open with a brief summary of what the manuscript does and the "
                "overall assessment, then the major concerns and then the minor points, numbered. Be specific, "
                "constructive and courteous; every criticism should say what the authors should do.")
    sys = (
        "You turn a peer reviewer's own fragmentary notes and discussion into a finished review text. "
        "IMPORTANT RULES:\n"
        "- Use ONLY the reviewer's own judgements from the notes, annotations and discussion. Do not add new "
        "criticisms or praise the reviewer did not express. If the notes are thin on something, keep it thin.\n"
        "- Keep the polarity of every note: a positive remark (e.g. 'writing clear', '很新', 'good') is praise and "
        "must stay praise; never turn it into a criticism or a request.\n"
        "- Notes may be in Chinese or mixed language; understand them precisely before writing.\n"
        "- Write in the reviewer's own voice (first person), matching the style samples if given. Natural "
        "academic English unless the reviewer asks otherwise.\n"
        "- Output plain text only: no Markdown, no headings with # or **, no bullet symbols other than simple "
        "numbering like 1. 2. 3.\n"
        "- Do not mention AI or that the text was drafted with assistance.\n\n"
        + task + "\n\n" + _project_header(p))
    if style.strip():
        sys += "\n\nStyle samples of the reviewer's past reviews (imitate tone and phrasing, not content):\n<<<STYLE\n" \
               + style[:12000] + "\nSTYLE>>>"
    parts = []
    if notes.strip():
        parts.append("MY NOTES:\n" + notes[:40000])
    if anns:
        parts.append("MY ANNOTATIONS ON THE MANUSCRIPT:\n" + "\n".join(
            f"- p.{a['page']}: " + (f"\"{(a['quote'] or '')[:300]}\" — " if a["quote"] else "") + a["comment"]
            for a in anns[:300]))
    disc = [c for c in chat if c["content"].strip()]
    if disc:
        parts.append("OUR DISCUSSION (my points are what count; your earlier replies are only context):\n" +
                     "\n\n".join(("ME: " if c["role"] == "user" else "COLLEAGUE: ") + c["content"][:4000]
                                 for c in disc[-40:]))
    if previous.strip():
        parts.append("PREVIOUS DRAFT (improve on it, keep what I kept):\n" + previous[:20000])
    if context:
        parts.append("MANUSCRIPT TEXT (redacted, for checking facts and page references only):\n" + context[:100000])
    if instructions.strip():
        parts.append("EXTRA INSTRUCTIONS FROM ME:\n" + instructions)
    parts.append("Now write the final text.")
    return [{"role": "system", "content": sys}, {"role": "user", "content": "\n\n".join(parts)}]
