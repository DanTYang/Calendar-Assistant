"""Part 6 — searching your notes."""


def test_load_notes_reads_every_markdown_file(chunks):
    from assistant.search import load_notes
    import config

    notes = load_notes(config.NOTES_FOLDER)
    assert len(notes) == 5
    assert all(note["text"].strip() for note in notes)


def test_split_into_chunks_breaks_up_long_text():
    from assistant.search import split_into_chunks

    text = "\n\n".join(["word " * 60] * 6)
    pieces = split_into_chunks(text, max_words=100)
    assert len(pieces) > 1
    assert all(len(piece.split()) <= 200 for piece in pieces)


def test_split_into_chunks_leaves_short_text_alone():
    from assistant.search import split_into_chunks

    assert split_into_chunks("just one short paragraph", max_words=100) == \
        ["just one short paragraph"]


def test_text_to_vector_counts_words():
    from assistant.search import text_to_vector

    vector = text_to_vector("budget budget review")
    assert vector["budget"] == 2
    assert vector["review"] == 1


def test_text_to_vector_ignores_case_and_punctuation():
    from assistant.search import text_to_vector

    assert text_to_vector("Budget, budget!") == text_to_vector("budget budget")


def test_text_to_vector_drops_filler_words():
    from assistant.search import text_to_vector

    # "the" appears in every document, so it tells you nothing about which one
    # matches. Leaving it in makes everything look similar to everything.
    assert "the" not in text_to_vector("the budget and the review")


def test_cosine_similarity_of_identical_text_is_one():
    from assistant.search import cosine_similarity, text_to_vector

    vector = text_to_vector("platform migration cutover")
    assert abs(cosine_similarity(vector, vector) - 1.0) < 1e-9


def test_cosine_similarity_of_unrelated_text_is_zero():
    from assistant.search import cosine_similarity, text_to_vector

    a = text_to_vector("platform migration cutover")
    b = text_to_vector("dentist appointment tomorrow")
    assert cosine_similarity(a, b) == 0.0


def test_cosine_similarity_handles_empty_vectors():
    from assistant.search import cosine_similarity

    assert cosine_similarity({}, {"budget": 1}) == 0.0


def test_cosine_similarity_ignores_length():
    from assistant.search import cosine_similarity, text_to_vector

    short = text_to_vector("budget review")
    long = text_to_vector("budget review " * 20)
    assert abs(cosine_similarity(short, long) - 1.0) < 1e-9


def test_search_finds_the_right_note(chunks):
    from assistant.search import search_notes

    out = search_notes(chunks, "what did we decide about the Northwind renewal?")
    assert "annual commit tier" in out
    assert "vendor-contract" in out, "the answer should say which file it came from"


def test_search_finds_a_different_note_for_a_different_question(chunks):
    from assistant.search import search_notes

    out = search_notes(chunks, "who owns the cutover runbook for the migration?")
    assert "migration" in out.lower()


def test_search_says_so_when_nothing_matches(chunks):
    from assistant.search import search_notes

    out = search_notes(chunks, "zzzzz qqqqq xyzzy")
    # Says nothing matched, names the query, and returns no note content -
    # handing the model an unrelated paragraph is worse than admitting a miss.
    assert "no notes matched" in out.lower()
    assert "zzzzz qqqqq xyzzy" in out
    assert "[from " not in out
