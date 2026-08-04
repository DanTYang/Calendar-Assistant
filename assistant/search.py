"""Retrieval over meeting notes: find the relevant paragraph, then answer.

Retrieval-Augmented Generation is a heavy name for a simple idea - before
asking the model, go find the relevant text and put it in the prompt. The model
has never seen these notes, so "what did we decide about the Northwind
renewal?" is unanswerable without retrieval and trivial with it.

The difficulty is finding the right paragraph. Keyword matching is not enough:
someone asks about "the vendor contract" and the note says "moving to the
annual commit tier" - not one shared word. So text becomes numbers:

    "budget review"  ->  {"budget": 1, "review": 1}
    "the budget"     ->  {"budget": 1}

and cosine similarity measures the angle between them. Text about the same
subject reuses the same words, so the vectors point the same way.

Production systems produce those numbers with a neural network, which captures
that "vendor" and "supplier" are related. Plain word counts are the same maths
with no API dependency, and they have one real advantage while building: you
can read a vector and see exactly why a result came back.

Note where this is *not* used. Calendar questions are filters and arithmetic
over structured data and are answered in `queries`. Ranking events by how much
they resemble the word "birthday" would be strictly worse than filtering them.
Same program, two retrieval strategies, each where it belongs.
"""

import math
import re
from pathlib import Path

# Words common enough to appear in every document tell you nothing about which
# one matches; leaving them in makes everything look similar to everything.
STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "if", "of", "to", "in", "on", "at", "for",
    "with", "is", "are", "was", "were", "be", "been", "it", "this", "that", "we",
    "i", "you", "he", "she", "they", "as", "by", "from", "our", "us", "what", "did",
}

WORD = re.compile(r"[a-z0-9']+")
PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def load_notes(folder):
    """Read every .md file in a folder into {"file": name, "text": contents}.

    The filename is kept so answers can cite their source. An assistant that
    cites is one you can check; one that does not is one you have to trust.
    """
    return [{"file": path.name, "text": path.read_text(encoding="utf-8")}
            for path in sorted(Path(folder).glob("*.md"))]


def split_into_chunks(text, max_words=120):
    """Cut one note into pieces of roughly `max_words`, on paragraph boundaries.

    Whole files make poor units of retrieval: most of a note is irrelevant to
    any one question, so its score gets diluted by the parts that do not match,
    and pasting the whole thing wastes prompt space.

    Splitting on paragraphs rather than every N words matters - cut mid-sentence
    and you get a chunk beginning "...therefore we rejected it", which is
    useless alone because the thing rejected is in the previous chunk.
    """
    chunks, current, words = [], [], 0

    for paragraph in PARAGRAPH_BREAK.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        length = len(paragraph.split())
        if current and words + length > max_words:
            chunks.append("\n\n".join(current))
            current, words = [], 0
        current.append(paragraph)
        words += length

    if current:
        chunks.append("\n\n".join(current))
    return chunks


def build_chunks(notes, max_words=120):
    """Chunk every note, tagging each chunk with the file it came from."""
    return [{"file": note["file"], "text": chunk}
            for note in notes
            for chunk in split_into_chunks(note["text"], max_words)]


def tokenize(text):
    """Lowercase, strip punctuation, and drop filler and single characters."""
    return [word for word in WORD.findall(text.lower())
            if word not in STOP_WORDS and len(word) > 1]


def text_to_vector(text):
    """Count how often each word appears. That dictionary is the vector.

    Words that do not appear are simply absent rather than stored as zeros,
    which is what keeps this small despite a vocabulary of any size.
    """
    vector = {}
    for word in tokenize(text):
        vector[word] = vector.get(word, 0) + 1
    return vector


def cosine_similarity(a, b):
    """Angle between two vectors: 1.0 identical, 0.0 nothing in common.

    Dividing by both magnitudes is what lets a three-word question score highly
    against a three-hundred-word paragraph on the same subject. Without it,
    long documents win everything by being long.
    """
    shared = set(a) & set(b)
    if not shared:
        return 0.0

    size_a = math.sqrt(sum(count * count for count in a.values()))
    size_b = math.sqrt(sum(count * count for count in b.values()))
    if size_a == 0 or size_b == 0:
        return 0.0

    return sum(a[word] * b[word] for word in shared) / (size_a * size_b)


def search_notes(chunks, query, top_k=3):
    """Return the best-matching chunks, formatted with source and score.

        [from 2026-07-30-vendor-contract-review.md, score 0.22]
        We are moving to the annual commit tier...

    Chunks scoring zero are dropped rather than padded out to `top_k`. No
    shared words means no match, and handing the model an unrelated paragraph
    invites it to summarise something irrelevant with full confidence.
    """
    query_vector = text_to_vector(query)

    scored = []
    for chunk in chunks:
        # Cached on first use; otherwise every question re-tokenises every note.
        if "vector" not in chunk:
            chunk["vector"] = text_to_vector(chunk["text"])
        score = cosine_similarity(query_vector, chunk["vector"])
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    if not scored:
        return f"No notes matched {query!r}."

    return "\n\n".join(f"[from {chunk['file']}, score {score:.2f}]\n{chunk['text']}"
                       for score, chunk in scored[:top_k])