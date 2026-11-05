from velox_ui.services.think_split import Answer, Retract, ThinkSplitter, Thought


def _run(chunks):
    splitter = ThinkSplitter()
    out = []
    for chunk in chunks:
        out += splitter.feed(chunk)
    return out + splitter.finish()


def test_both_tags_split_across_chunks():
    assert _run(["<thi", "nk>abc</th", "ink>hello"]) == [Thought("abc"), Answer("hello")]


def test_lone_closing_tag_retracts_prior_text():
    events = _run(["abc ", "def</thi", "nk>\n\nhello"])
    assert Retract("abc def") in events
    assert events[-1] == Answer("\n\nhello")


def test_plain_text_untouched():
    assert _run(["a < b"]) == [Answer("a < b")]
