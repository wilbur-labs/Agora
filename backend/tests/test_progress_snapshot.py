"""The RESUME progress snapshot the progress bar is driven by.

What these pin is the distinction the UI depends on: a snapshot that is absent, one
that could not be read, one whose checklist marks this reader does not count, one that
mixes both conventions, one that lists nothing, and one that was read and counted are
six different answers, and none of them may become 0.

Two of them are newer than the rest and carry the load: a checkbox file is *counted*
(with its in-progress split reported as unknown rather than zero), and a file that
carries both conventions is unknown rather than half-counted.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agora.projects.progress_snapshot import (
    CONVENTION_CHECKBOXES,
    CONVENTION_MARKERS,
    CONVENTION_NONE,
    MAX_SNAPSHOT_BYTES,
    MIXED_CONVENTIONS_REASON,
    SNAPSHOT_RELATIVE_PATH,
    UNCOUNTED_MARKERS_REASON,
    count_checkbox_items,
    count_progress_markers,
    count_progress_verdict,
    read_progress_snapshot,
)
from tests.acl_support import deny_self_traverse, restore_traverse


def _write(root: Path, text: str) -> Path:
    path = root / SNAPSHOT_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_counts_markers_as_the_first_token_of_a_line():
    counts = count_progress_markers(
        "# Progress\n"
        "- ✅ Shipped the reader\n"
        "  - ✅ nested item still counts\n"
        "* 🔵 Wiring the bar\n"
        "+ ⬜ Portfolio surface\n"
        "1. ⬜ ordered bullets too\n"
        "2) ⬜ and this form\n"
        "✅ no bullet at all\n"
    )
    assert counts == {"done": 3, "in_progress": 1, "todo": 3}


def test_prose_mentioning_a_marker_is_not_an_item():
    # The snapshot's own explanatory text says things like "mark it ✅ when done".
    # Counting that as an item would inflate the percentage from the file's prose.
    counts = count_progress_markers(
        "The convention is: mark an item ✅ when it is done, 🔵 while it is in\n"
        "progress, and leave it ⬜ until it starts.\n"
        "- ✅ the one real item\n"
    )
    assert counts == {"done": 1, "in_progress": 0, "todo": 0}


def test_one_item_per_line_even_when_a_line_carries_several_markers():
    assert count_progress_markers("- ✅ done ⬜ ⬜ ⬜\n") == {"done": 1, "in_progress": 0, "todo": 0}


def test_the_text_verdict_is_exactly_what_read_from_disk_reports(tmp_path):
    """`count_progress_verdict` is the decision `read_progress_snapshot` used to inline,
    lifted out so a second reader reaches the same answer. Pinned as parity: for each
    shape, counting the text directly and reading the same text off disk agree on
    counts, convention and reason. If they ever diverge, the extraction changed the
    file-level behavior the docstring promises it must not.
    """
    cases = {
        "markers": "- ✅ a\n- 🔵 b\n- ⬜ c\n",
        "checkboxes": "- [x] a\n- [ ] b\n",
        "none": "just prose, no items\n",
        MIXED_CONVENTIONS_REASON: "- ✅ a\n- [x] b\n",
        UNCOUNTED_MARKERS_REASON: "- [x] a\n- [/] b\n",
    }
    for label, text in cases.items():
        verdict = count_progress_verdict(text)
        snapshot = read_progress_snapshot_of(tmp_path, text)
        assert verdict.done == snapshot.done, label
        assert verdict.in_progress == snapshot.in_progress, label
        assert verdict.todo == snapshot.todo, label
        assert verdict.convention == snapshot.convention, label
        # A countable verdict is an available snapshot with no reason; an uncountable
        # verdict's reason is the snapshot's reason. Exactly one of the two per case.
        assert verdict.countable == snapshot.available, label
        assert verdict.reason == (None if snapshot.available else snapshot.reason), label


def read_progress_snapshot_of(root: Path, text: str):
    """Write `text` to the snapshot path and read it back — the file-level path, for the
    parity check above."""
    _write(root, text)
    return read_progress_snapshot(root)


def test_absent_snapshot_reports_absent_with_no_counts(tmp_path):
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is False
    assert snapshot.reason == "absent"
    # The counts are None, not 0: a project with no snapshot renders empty, and a
    # zero in this payload is a number the UI could render as 0%.
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (None, None, None)
    assert snapshot.source == SNAPSHOT_RELATIVE_PATH.as_posix()


def test_a_directory_at_the_snapshot_path_is_unreadable_not_absent(tmp_path):
    """A directory is present-but-unusable, which is not the same claim as absent.

    This test previously asserted `absent`, and that expectation was wrong: `absent`
    renders as "this project keeps no progress snapshot", a settled fact about the
    project, while a directory (or a device, or a broken symlink) at that path means the
    reader could not read what is there. Pinning the collapse made the defect look
    intended, so the assertion is inverted here rather than only in the reader.
    """
    (tmp_path / SNAPSHOT_RELATIVE_PATH).mkdir(parents=True)
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.reason == "unreadable"
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (None, None, None)


def test_a_checkbox_snapshot_is_counted_with_in_progress_unknown(tmp_path):
    """The shape the checkbox convention supports: an exact total, no in-flight split.

    This repository's own snapshot is written as `- [x]` task-list lines and holds over
    a thousand of them, and it used to be reported as uncountable. It is not: an item a
    human would call in progress is written as an unchecked box, so done + not-done
    still partitions the whole checklist and `done / total` is exact. What the file
    cannot support is the split of not-done, so `in_progress` is None — unknown, and
    emphatically not 0, which would assert that nothing is in flight.
    """
    _write(tmp_path, "# Progress\n\n- [x] Shipped the reader\n- [X] Counted it\n- [ ] Wire the bar\n")
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is True and snapshot.reason is None
    assert snapshot.convention == CONVENTION_CHECKBOXES
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (2, None, 1)


def test_a_box_holding_a_mark_this_reader_does_not_know_is_unknown(tmp_path):
    """`[/]` and `[-]` are real conventions elsewhere; neither has a meaning here.

    Counting only the boxes this reader understands would report a floor, which is the
    same wrong number `too_large` exists to prevent. So the file is unknown rather than
    partially counted — and the reason names the reader, not the project.
    """
    _write(tmp_path, "- [x] done\n- [ ] not yet\n- [/] whatever this means here\n")
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is False
    assert snapshot.reason == UNCOUNTED_MARKERS_REASON
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (None, None, None)


def test_a_file_carrying_both_conventions_is_unknown_rather_than_half_counted(tmp_path):
    """Reverses the earlier "markers win in a mixed file" rule, because its premise expired.

    That rule cost nothing while checkboxes were not counted at all. Now that they are
    countable data, preferring markers drops real items and reports a floor as a
    percentage. Merging the two is not available either: whether a block of boxes is
    another section of the same checklist or an unrelated list is not decidable from
    the text.
    """
    _write(tmp_path, "- ✅ counted\n- [x] also an item, in the other convention\n")
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is False
    assert snapshot.reason == MIXED_CONVENTIONS_REASON
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (None, None, None)


def test_counts_boxes_as_the_first_token_of_a_list_item():
    counts = count_checkbox_items(
        "# Progress\n"
        "- [x] shipped\n"
        "  - [X] nested and capitalised\n"
        "* [ ] pending\n"
        "1. [ ] ordered bullets too\n"
        "2) [/] a mark with no meaning here\n"
        "no bullet: [x] mid-sentence, not an item\n"
        "[x] no bullet at all — a paragraph, not a task list item\n"
    )
    assert counts == {"done": 2, "not_done": 2, "uncounted": 1}


def test_a_box_needs_a_list_bullet_because_a_task_list_is_a_list(tmp_path):
    """The rule that settles the shortcut-reference ambiguity instead of guessing.

    GFM's task-list marker is a marker *on a list item*. A bare ``[x] release`` line
    is a paragraph — and if a ``[x]: https://…`` definition appears further down it is
    a link, which used to be counted as one finished item and could be the only "item"
    in a file. Requiring the bullet removes every bare bracket line from the count, so
    a reference definition has nothing left to change.

    The inverse fix — resolving definitions and un-counting matching labels — was
    rejected: one stray ``[x]: url`` line would then silently un-count every real
    ``- [x]`` in the file, turning a rare over-count into a total one.
    """
    assert count_checkbox_items("[x] release\n")["done"] == 0
    assert count_checkbox_items("- [x] release\n")["done"] == 1

    _write(tmp_path, "[x] release\n\n[x]: https://example.com\n")
    snapshot = read_progress_snapshot(tmp_path)
    # Nothing here is an item of either convention — which is a very different claim
    # from "one item, and it is finished".
    assert snapshot.available is True
    assert snapshot.convention == CONVENTION_NONE
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (0, 0, 0)


def test_a_fence_that_is_a_list_item_s_own_content_is_still_a_fence(tmp_path):
    """Two errors pulling the same way, which is how a wrong 100% gets made.

    When the opening fence is the list item's own first line, looking for the fence
    before stripping the bullet misses it — and then the block's *indented closing*
    fence reads as a new opener, swallowing every real item after it. The example's
    box counted, the real items did not, and the file reported 100%.
    """
    _write(tmp_path, "- ```markdown\n  - [x] example\n  ```\n- [ ] real\n")
    snapshot = read_progress_snapshot(tmp_path)
    assert (snapshot.done, snapshot.todo) == (0, 1)


def test_indented_code_is_skipped_only_where_it_cannot_be_a_nested_item(tmp_path):
    """The narrow form of the rule this reader had rejected in general — and why.

    Rejected in general because "indented by four" eats legitimately nested checklist
    items, trading a rare over-count for a routine under-count. The narrow form does
    not: a four-space line with no list open above it cannot be a nested item,
    because there is nothing to nest in.
    """
    _write(tmp_path, "Some paragraph.\n\n    - [x] example in an indented code block\n\n- [ ] real\n")
    assert (read_progress_snapshot(tmp_path).done, read_progress_snapshot(tmp_path).todo) == (0, 1)

    # And the nesting that the general rule would have eaten is kept — including
    # across a blank line, where the item's content continues.
    _write(tmp_path, "- [x] real\n\n    - [ ] a nested item, not code\n")
    assert (read_progress_snapshot(tmp_path).done, read_progress_snapshot(tmp_path).todo) == (1, 1)
    _write(tmp_path, "- [x] a\n  - [x] b\n    - [ ] c\n      - [ ] d\n")
    assert (read_progress_snapshot(tmp_path).done, read_progress_snapshot(tmp_path).todo) == (2, 2)


def test_an_unindented_line_ends_a_list_only_across_a_blank_line(tmp_path):
    """Lazy continuation, and why getting it wrong drops a real item.

    An unindented paragraph line straight after a list item is a lazy continuation of
    that item — the list is still open, so a four-space line below it is a nested
    item and not code. Ending the list at the unindented line instead makes the
    reader skip that item, and an under-count is a floor. Across a blank line the
    same paragraph really does end the list, and then the indented line is code.
    """
    _write(tmp_path, "- [x] a\nLazy continuation of that item.\n    - [ ] b\n")
    snapshot = read_progress_snapshot(tmp_path)
    assert (snapshot.done, snapshot.todo) == (1, 1)

    _write(tmp_path, "- [x] a\n\nA new paragraph.\n\n    - [ ] b in an indented code block\n")
    snapshot = read_progress_snapshot(tmp_path)
    assert (snapshot.done, snapshot.todo) == (1, 0)


def test_a_one_character_link_is_not_mistaken_for_a_checkbox():
    assert count_checkbox_items("- [a](https://example.com) a link, not a box\n") == {
        "done": 0, "not_done": 0, "uncounted": 0,
    }
    assert count_checkbox_items("- [x] a box\n")["done"] == 1
    assert count_checkbox_items("1. [ ]\n")["not_done"] == 1


def test_a_shortcut_reference_link_is_not_a_checkbox(tmp_path):
    """The reported defect: a footnote costing a real snapshot its progress bar.

    ``[a]`` alone on a line, defined further down, is an ordinary Markdown shortcut
    reference link — the one link shape the "]" + trailing-space test cannot rule out.
    Counted as an unknown box it made this marker file read as carrying two
    conventions, so a file with four countable items reported no number at all.

    The rule is that an alphanumeric mark is not a checklist mark (``[a]``, ``[1]``),
    while a non-alphanumeric one is a checklist mark this reader does not count
    (``[/]``, ``[✓]``) and still forces the file to unknown.
    """
    # With a bullet, and undefined — the only shape that reaches the word-character
    # rule. The first version of this test used bullet-less lines, which are dropped
    # earlier for not being list items at all, so the rule under test never ran and
    # deleting it changed nothing. Without it, `- [a] details` counts as an uncounted
    # mark and takes the file's progress bar down with it.
    assert count_checkbox_items("- [a] details\n") == {"done": 0, "not_done": 0, "uncounted": 0}
    assert count_checkbox_items("- [1] details\n") == {"done": 0, "not_done": 0, "uncounted": 0}
    # A precomposed accented letter is one codepoint and still a label character.
    assert count_checkbox_items("- [é] details\n") == {"done": 0, "not_done": 0, "uncounted": 0}
    # Three regional indicators are not one character, so this is a label too.
    assert count_checkbox_items("- [\U0001f1ef\U0001f1f5\U0001f1fa] x\n") == {
        "done": 0, "not_done": 0, "uncounted": 0,
    }
    assert count_checkbox_items("[a]\n[1]\n") == {"done": 0, "not_done": 0, "uncounted": 0}
    assert count_checkbox_items("- [/] a mark, not a link\n")["uncounted"] == 1
    assert count_checkbox_items("- [✓] nor is this\n")["uncounted"] == 1
    # `str.isalnum()` is true for CJK, and that is the wanted answer rather than an
    # accident: a Chinese or Japanese snapshot's `[参]` is a reference label, and no
    # checklist convention uses a word character as its mark.
    assert count_checkbox_items("[参]\nSee [参] above.\n\n[参]: https://example.com\n") == {
        "done": 0, "not_done": 0, "uncounted": 0,
    }

    _write(tmp_path, "- ✅ done\n- ⬜ next\n\nSee [a] for context.\n\n[a]: https://example.com\n")
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is True
    assert snapshot.convention == CONVENTION_MARKERS
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (1, 0, 1)


def test_a_checklist_shown_inside_a_fenced_code_block_is_not_counted(tmp_path):
    """A snapshot documenting its own convention must not be counted as having done it.

    The file below tracks one real item and shows two more in a fence as an example.
    Counting the fenced ones reports items the project does not have — and on a file
    whose *only* boxes are the example, it manufactures a whole percentage out of
    documentation.
    """
    _write(
        tmp_path,
        "# Progress\n\n"
        "Write items like this:\n\n"
        "```markdown\n- [x] a finished item\n- [ ] an unfinished one\n```\n\n"
        "~~~\n- [x] and this fence style too\n~~~\n\n"
        "- ✅ the one real item\n",
    )
    snapshot = read_progress_snapshot(tmp_path)
    # Not `mixed_conventions` either: the fenced boxes are not items of a second
    # convention, they are a picture of one.
    assert snapshot.available is True
    assert snapshot.convention == CONVENTION_MARKERS
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (1, 0, 0)


def test_a_longer_fence_is_not_closed_by_a_shorter_one_inside_it(tmp_path):
    """The way a documentation snapshot actually shows a fenced example.

    Four backticks wrap a three-backtick block so the inner fence renders literally.
    Closing on any ``` run ends the outer block at the inner opener, and everything
    after it — the example's boxes — gets counted as real items.
    """
    _write(
        tmp_path,
        "````markdown\n"
        "```\n"
        "- [x] an example inside an example\n"
        "```\n"
        "````\n"
        "- [x] the one real item\n",
    )
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.convention == CONVENTION_CHECKBOXES
    assert (snapshot.done, snapshot.todo) == (1, 0)


def test_block_shapes_a_line_scan_kept_getting_wrong(tmp_path):
    """The cases that drove this reader onto a CommonMark parser, pinned as a set.

    Each of these was a real miscount in a hand-rolled line scanner, and each fix in
    that scanner uncovered the next. The first is the one that mattered most: an
    indented ``~~~`` is *indented code*, not a fence, and treating it as one opened a
    block that never closed — so a file with a real item reported that it tracked
    nothing at all, which is the exact failure this module exists to prevent.

    They are grouped in one test on purpose. Individually they look like trivia;
    together they are the argument for not hand-rolling block parsing, and a future
    reader tempted to drop the dependency has to make all of them pass again.
    """
    cases = [
        # (name, source, (done, todo))
        ("an indented ~~~ is code, and opens nothing",
         "text\n\n    ~~~\n\n- [x] real\n", (1, 0)),
        ("a fence that is a list item's content, with a nested item after it",
         "- ```text\n  ```\n    - [x] nested\n", (1, 0)),
        ("a paragraph indented too little to belong to the item ends the list",
         "- [x] real\n\n outside paragraph\n\n    - [ ] code example\n", (1, 0)),
        ("a bullet with no separating space is a paragraph, not a list item",
         "-[x] fake\n", (0, 0)),
        ("a tab indent is four columns, so this is code",
         "\t- [x] example\n", (0, 0)),
        ("a four-backtick fence is not closed by the ``` inside it",
         "````markdown\n```\n- [x] ex\n```\n````\n- [x] real\n", (1, 0)),
        ("a heading is a section title, not an item",
         "## ✅ Phase one\n\n- ✅ real\n", (1, 0)),
        ("a quoted checklist is somebody else's list",
         "> - [x] theirs\n\n- [ ] ours\n", (0, 1)),
    ]
    for name, source, expected in cases:
        _write(tmp_path, source)
        snapshot = read_progress_snapshot(tmp_path)
        assert (snapshot.done, snapshot.todo) == expected, name


def test_a_mark_is_a_visual_character_not_a_codepoint(tmp_path):
    """The worst shape a miscount can take: a partial count that looks complete.

    ``✔️`` is two codepoints, U+2714 and a variation selector. Reading the mark as
    ``head[2]`` meant the closing bracket was not where it was assumed to be, so the
    line did not even look like a box: it was skipped in silence, and the file's one
    countable item was then reported as the whole of it — an available 100% next to an
    item nobody counted. Unknown is the only honest answer.
    """
    _write(tmp_path, "- [x] real\n- [✔️] unknown\n")
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is False
    assert snapshot.reason == UNCOUNTED_MARKERS_REASON
    # The failure this replaces, stated so the test cannot be "fixed" back into it.
    assert (snapshot.done, snapshot.todo) != (1, 0)

    # Every multi-codepoint shape that is nonetheless *one character* has to reach the
    # same answer, because the danger is not the mark — it is being skipped without a
    # word. Each of these was silently dropped by a rule that recognised only some of
    # them, and each drop turns a partial count into an apparently whole one.
    for label, mark in [
        ("variation selector", "✔️"),
        ("skin-tone modifier", "\U0001f44d\U0001f3fb"),
        ("zero-width-joiner sequence", "\U0001f468‍\U0001f4bb"),
        ("regional-indicator pair (a flag)", "\U0001f1ef\U0001f1f5"),
        # The four above are all reached by explicit ranges or by the ZWJ rule, so
        # none of them exercised the Unicode-category test. These do, and each is a
        # continuation whose *combining class is zero* — which is why asking for the
        # combining class instead of the category let all of them through:
        ("canonical combining mark", "✓́"),
        ("enclosing mark (a keycap)", "#️⃣"),
        ("spacing combining mark", "☑ा"),
        ("supplementary variation selector", "✓\U000e0100"),
        ("tag sequence (a subdivision flag)",
         "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f"),
        # A digit keycap: the base character is a word character, so judging the mark
        # by that alone called it a link label and skipped it. A mark that carries
        # continuations is a symbol, whatever it starts with.
        ("digit keycap", "1️⃣"),
        # A flag with a variation selector: still one character, and a rule that
        # matched flags only at exactly two codepoints fell through and skipped it.
        ("regional-indicator pair plus a variation selector", "\U0001f1fa\U0001f1f8️"),
    ]:
        assert count_checkbox_items(f"- [{mark}] item\n") == {
            "done": 0, "not_done": 0, "uncounted": 1,
        }, label


def test_a_defined_reference_label_is_a_link_except_for_the_task_marks(tmp_path):
    """Where consulting the document's reference definitions is safe, and where it is not.

    Safe for symbols: ``- [~] details`` beside a ``[~]: https://…`` definition is a
    shortcut reference link, and counting it as an unknown box hid an otherwise
    countable marker file behind `mixed_conventions`.

    Not safe for ``x`` / ``X`` / space, and this is the asymmetry the whole rule turns
    on: **one** ``[x]: url`` definition anywhere in a document makes markdown-it
    render **every** ``- [x]`` in it as a link, so consulting the references for those
    would drop the entire checklist. GFM takes the task marker first, and so does
    this. Both halves are pinned here because the pair is the point.
    """
    _write(tmp_path, "- ✅ done\n- [~] details\n\n[~]: https://example.com\n")
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is True and snapshot.convention == CONVENTION_MARKERS
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (1, 0, 0)

    # The same shape with no definition really is an uncounted checklist mark.
    _write(tmp_path, "- ✅ done\n- [~] details\n")
    assert read_progress_snapshot(tmp_path).reason == MIXED_CONVENTIONS_REASON

    # And the task marks are counted whatever the references say.
    _write(tmp_path, "- [x] a\n- [ ] b\n\n[x]: https://example.com\n")
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.convention == CONVENTION_CHECKBOXES
    assert (snapshot.done, snapshot.todo) == (1, 1)


def test_a_multi_character_label_is_never_a_mark(tmp_path):
    """Finding the closing bracket rather than assuming its position means a long label
    now reaches the mark test, and no checklist convention has a multi-character mark.

    The load-bearing case is the second one, and the first version of this test did
    not contain it: ``[some label]`` is already excluded by the rule that a word
    character is a reference label, so with the single-character check removed the
    test still passed — it was pinning nothing. Deleting the check was caught only by
    a label that is multi-character **and** starts with a symbol, where nothing else
    rules it out; without the check it counts as an uncounted mark and takes the
    file's progress bar down with it.
    """
    assert count_checkbox_items("- [some label] text\n") == {
        "done": 0, "not_done": 0, "uncounted": 0,
    }
    assert count_checkbox_items("- [~sub] text\n") == {
        "done": 0, "not_done": 0, "uncounted": 0,
    }
    _write(tmp_path, "- ✅ real\n- [~sub] a label, not a mark\n")
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is True and snapshot.convention == CONVENTION_MARKERS
    assert snapshot.reason != MIXED_CONVENTIONS_REASON


def test_an_empty_block_quote_does_not_leak_the_list_item_it_opened(tmp_path):
    """A container that produces no inline token still ends "the item starts with text".

    ``- >`` is a list item whose first block is an empty block quote, so the item is
    not a task-list item. The quote emits an open and a close and nothing between, so
    the flag that says "the next text is this item's first" survived — and the next
    paragraph in the file inherited it and was counted as a finished item.
    """
    _write(tmp_path, "- >\n\n  [x] later\n")
    snapshot = read_progress_snapshot(tmp_path)
    assert (snapshot.done, snapshot.todo) == (0, 0)
    assert snapshot.convention == CONVENTION_NONE


def test_disabling_inline_parsing_is_a_speed_choice_and_changes_no_count():
    """The optimisation must be invisible to the result, or it is not an optimisation.

    Inline parsing is turned off because nothing here reads it — only the block
    parser's raw ``token.content``. That is worth a third of the parse on a real
    snapshot, and it is only safe while no counting rule depends on emphasis, links
    or code spans being resolved. This pins that: with the rule back on, every count
    is the same. If a future rule does need the inline tree, this test fails and says
    so, instead of the speed choice quietly changing a number.
    """
    from markdown_it import MarkdownIt

    import agora.projects.progress_snapshot as module

    source = (
        "- [x] **bold** item\n"
        "- [ ] an item with `code` and [a link](https://example.com)\n"
        "- [x] *emphasis* at the start\n"
        "\n"
        "Prose with [a reference][ref] in it.\n"
        "\n"
        "[ref]: https://example.com\n"
    )
    with_inline_off = count_checkbox_items(source), count_progress_markers(source)
    original = module._MARKDOWN
    try:
        module._MARKDOWN = MarkdownIt("commonmark")
        with_inline_on = count_checkbox_items(source), count_progress_markers(source)
    finally:
        module._MARKDOWN = original
    assert with_inline_off == with_inline_on
    assert with_inline_off[0] == {"done": 2, "not_done": 1, "uncounted": 0}


def test_a_fence_is_closed_only_by_its_own_character_on_a_bare_line(tmp_path):
    _write(
        tmp_path,
        "```\n"
        "~~~ this is content, not a close\n"
        "```javascript is an opener, not a close, and also content here\n"
        "- [x] still inside the fence\n"
        "```\n"
        "- [x] real\n"
        "- [ ] also real\n",
    )
    snapshot = read_progress_snapshot(tmp_path)
    assert (snapshot.done, snapshot.todo) == (1, 1)


def test_unreadable_snapshot_is_unknown_and_never_zero(tmp_path):
    path = _write(tmp_path, "- ✅ done\n")
    path.write_bytes(b"- \xff\xfe not utf-8 \xff")
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is False
    assert snapshot.reason == "unreadable"
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (None, None, None)


def test_a_snapshot_that_cannot_be_stat_ed_is_unreadable_not_absent(tmp_path, monkeypatch):
    """The reported defect: an inaccessible snapshot read as "this project keeps none".

    ``Path.exists()`` returns False both for nothing-is-there and for a stat this
    process may not complete, and the ``is_symlink()`` fallback that followed it failed
    the same way. A `PROGRESS.md` under a directory whose ACL denies traversal therefore
    rendered as a settled fact about the project instead of as unknown. Only a definite
    ``FileNotFoundError`` is absence.
    """
    _write(tmp_path, "- ✅ real content that cannot be reached\n")

    def refuse(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "lstat", refuse)
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is False
    assert snapshot.reason == "unreadable"
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (None, None, None)


@pytest.mark.parametrize(
    "error", [PermissionError(13, "denied"), OSError(5, "I/O error"), NotADirectoryError(20, "nope")]
)
def test_no_stat_failure_but_absence_may_report_absent(tmp_path, monkeypatch, error):
    # The distinction stated at the level every platform can test: whatever else the
    # OS raises, it is not "there is no snapshot".
    _write(tmp_path, "- ✅ done\n")

    def refuse(self, *args, **kwargs):
        raise error

    monkeypatch.setattr(Path, "lstat", refuse)
    assert read_progress_snapshot(tmp_path).reason == "unreadable"


def test_a_snapshot_under_a_directory_denying_traversal_is_unreadable(tmp_path):
    """The same case with the OS enforcing it, where the platform allows that.

    Skipped rather than faked when the account cannot deny itself traversal, so the
    record says whether the ACL path was actually exercised.
    """
    root = tmp_path / "project"
    _write(root, "- ✅ real content behind the deny\n")
    locked = root / ".agora"
    if not deny_self_traverse(locked):
        pytest.skip("this platform/account cannot deny itself traversal")
    try:
        snapshot = read_progress_snapshot(root)
        if snapshot.available:
            pytest.skip("the deny did not take effect for this token (elevated or bypassed)")
        assert snapshot.reason == "unreadable", "a denied read must not report 'no snapshot'"
        assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (None, None, None)
    finally:
        restore_traverse(locked)


def test_a_broken_symlink_at_the_snapshot_path_is_unreadable_not_absent(tmp_path):
    # Something is recorded at the path; "this project keeps no snapshot" would be false.
    path = tmp_path / SNAPSHOT_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.symlink_to(tmp_path / "nowhere.md")
    except (OSError, NotImplementedError):
        pytest.skip("this platform/account cannot create symlinks")
    assert read_progress_snapshot(tmp_path).reason == "unreadable"


def test_oversized_snapshot_is_unknown_rather_than_a_floor(tmp_path):
    _write(tmp_path, "- ✅ done\n" + "x" * (MAX_SNAPSHOT_BYTES + 1))
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is False
    # Counting a prefix would report a floor; a floor rendered as a percentage is a
    # wrong number, so the reason is its own and the counts stay absent.
    assert snapshot.reason == "too_large"
    assert snapshot.done is None


def test_read_snapshot_counts_are_exact(tmp_path):
    _write(tmp_path, "- ✅ a\n- ✅ b\n- 🔵 c\n- ⬜ d\n- ⬜ e\n")
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is True and snapshot.reason is None
    assert snapshot.convention == CONVENTION_MARKERS
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (2, 1, 2)


def test_a_snapshot_with_no_items_is_available_with_zero_items(tmp_path):
    # Present, readable, and genuinely tracking nothing — no items of either convention.
    # This is not "absent", not "unreadable", and not "another convention"; the display
    # layer turns 0 items into "no number", not into 0%. There is no convention to name
    # either: nothing was read in one.
    _write(tmp_path, "# Progress\n\nNothing tracked with markers yet.\n")
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is True
    # Named, not left empty. An empty `convention` on an available payload is
    # reserved for one meaning — a server older than the field — and a display has to
    # tell that apart from "this file lists nothing".
    assert snapshot.convention == CONVENTION_NONE
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (0, 0, 0)


def test_convention_is_never_empty_on_an_available_payload(tmp_path):
    """The invariant the display's version-skew rule is built on.

    If any available payload could arrive with no convention, "no convention" would
    stop proving "old server", and the display would be back to reading a missing
    field as a fact about the file.
    """
    for text in [
        "- ✅ a\n",
        "- [x] a\n",
        "# nothing tracked\n",
        "",
    ]:
        _write(tmp_path, text)
        snapshot = read_progress_snapshot(tmp_path)
        assert snapshot.available is True, text
        assert snapshot.convention is not None, text


def test_payload_shape_is_the_api_contract(tmp_path):
    _write(tmp_path, "- ✅ a\n")
    payload = read_progress_snapshot(tmp_path).payload()
    assert payload == {
        "available": True,
        "reason": None,
        "done": 1,
        "in_progress": 0,
        "todo": 0,
        "source": SNAPSHOT_RELATIVE_PATH.as_posix(),
        "convention": CONVENTION_MARKERS,
    }


def test_a_checkbox_payload_carries_a_null_in_progress_beside_available_true(tmp_path):
    """The one payload where `available` is true and a count is None.

    `convention` is what makes it readable rather than contradictory, and it is also
    what makes an older display fail closed: a UI written against "three integers
    whenever available" sees a count it cannot use and says so, instead of reading the
    null as 0 and drawing a percentage nobody's file supports.
    """
    _write(tmp_path, "- [x] a\n- [ ] b\n")
    assert read_progress_snapshot(tmp_path).payload() == {
        "available": True,
        "reason": None,
        "done": 1,
        "in_progress": None,
        "todo": 1,
        "source": SNAPSHOT_RELATIVE_PATH.as_posix(),
        "convention": CONVENTION_CHECKBOXES,
    }
