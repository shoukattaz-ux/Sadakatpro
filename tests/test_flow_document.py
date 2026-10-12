from flowpilot.core.flow_document import FlowDocument
from flowpilot.core.steps import new_step


def doc_with(*types):
    return FlowDocument([new_step(t) for t in types])


def test_insert_block_adds_companions_and_undo_redo():
    d = FlowDocument()
    d.insert(0, "open_url", url="https://a")
    d.insert(1, "if_element", selector="#x")
    assert [s.type for s in d.steps] == ["open_url", "if_element", "else", "end_if"]
    d.insert(2, "repeat")
    assert [s.type for s in d.steps][2:4] == ["repeat", "end_loop"]
    assert d.depths() == [0, 0, 1, 1, 0, 0]
    assert d.undo() and len(d.steps) == 4
    assert d.redo() and len(d.steps) == 6
    assert d.undo() and d.undo() and d.undo()
    assert d.steps == [] and not d.undo()


def test_move_delete_duplicate_enable():
    d = doc_with("wait", "click", "reload", "go_back")
    ids = [s.id for s in d.steps]
    assert d.move([3], 0) == [0]
    assert [s.type for s in d.steps] == ["go_back", "wait", "click", "reload"]
    d.move([0, 1], 4)
    assert [s.type for s in d.steps] == ["click", "reload", "go_back", "wait"]
    new = d.duplicate([0])
    assert new == [1] and d.steps[1].type == "click" and d.steps[1].id != d.steps[0].id
    d.delete([0, 1])
    assert [s.type for s in d.steps] == ["reload", "go_back", "wait"]
    d.set_enabled([0], False)
    assert not d.steps[0].enabled
    d.reorder(list(reversed([s.id for s in d.steps])))
    assert [s.type for s in d.steps] == ["wait", "go_back", "reload"]
    assert ids  # unchanged list object used above


def test_duplicate_whole_block_and_collapse():
    d = FlowDocument()
    d.insert(0, "repeat")
    d.insert(1, "wait")
    assert [s.type for s in d.steps] == ["repeat", "wait", "end_loop"]
    d.duplicate([0])
    assert [s.type for s in d.steps] == ["repeat", "wait", "end_loop", "repeat", "wait", "end_loop"]
    d.toggle_collapsed(0)
    assert d.hidden_rows() == {1}


def test_labels_problems_and_json_roundtrip():
    d = FlowDocument()
    d.insert(0, "label_marker", name="start")
    d.insert(1, "click", label="btn")
    assert d.labels() == ["start", "btn"]
    assert 1 in d.problems()
    again = FlowDocument.from_json(d.to_json())
    assert again.steps == d.steps


def test_recorder_append_replace():
    d = FlowDocument()
    d.append_step(new_step("type_text", selector="#a", text="x"))
    d.append_step(new_step("type_text", selector="#a", text="xy"), replace_last=True)
    assert len(d.steps) == 1 and d.steps[0].text == "xy"


def test_flow_io_roundtrip_and_devtools():
    import json

    import pytest

    from flowpilot.core.flow_io import export_flow, import_flow

    steps = [new_step("open_url", url="https://x"), new_step("click", selector="#a")]
    name, desc, again = import_flow(export_flow("Mine", "d", steps))
    assert (name, desc, again) == ("Mine", "d", steps)
    dev = {"title": "Rec", "steps": [{"type": "navigate", "url": "https://y"},
                                     {"type": "click", "selectors": [["#b"]]}]}
    name, _, imported = import_flow(json.dumps(dev))
    assert name == "Rec" and [s.type for s in imported] == ["open_url", "click"]
    with pytest.raises(ValueError):
        import_flow("{not json")
    with pytest.raises(ValueError):
        import_flow('{"hello": 1}')
