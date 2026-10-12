from flowpilot.core.flow_layout import TBlock, TStep, build_tree, layout_flow
from flowpilot.core.steps import new_step


def S(*types):  # noqa: N802
    return [new_step(t) for t in types]


def test_tree_nesting():
    steps = S("open_url", "if_element", "click", "else", "repeat", "wait", "end_loop", "end_if", "close_browser")
    tree = build_tree(steps)
    assert isinstance(tree[0], TStep) and isinstance(tree[2], TStep) and tree[2].index == 8
    block = tree[1]
    assert isinstance(block, TBlock) and block.kind == "if" and block.end_index == 7 and block.second_index == 3
    assert [t.index for t in block.first] == [2]
    inner = block.second[0]
    assert inner.kind == "loop" and inner.end_index == 6 and [t.index for t in inner.first] == [5]


def test_broken_structure_never_fails():
    cases = [  # (steps, indexes drawn as nodes); a matched Else / On error becomes the branch split
        (S("else", "end_loop", "click"), [0, 1, 2]),
        (S("if_element", "click"), [0, 1]),
        (S("repeat", "end_if", "wait"), [0, 1, 2]),
        (S("try", "catch"), [0]),
        ([], []),
    ]
    for steps, expected in cases:
        box = layout_flow(steps)
        assert sorted(n.index for n in box.nodes if n.index >= 0) == expected


def test_layout_geometry():
    steps = S("open_url", "if_element", "click", "else", "wait", "end_if", "repeat", "scroll", "end_loop")
    box = layout_flow(steps)
    nodes = {n.index: n for n in box.nodes}
    assert nodes[-1].kind == "start" and nodes[-2].kind == "finish"
    assert nodes[1].kind == "decision" and nodes[6].kind == "loop" and nodes[5].kind == "end"
    # branches side by side, below the decision
    assert nodes[2].y == nodes[4].y > nodes[1].y and nodes[2].x < nodes[4].x
    # order top to bottom
    assert nodes[0].y < nodes[1].y < nodes[5].y < nodes[6].y < nodes[7].y < nodes[8].y < nodes[-2].y
    labels = {e.label for e in box.edges}
    assert {"Yes", "No", "repeat"} <= labels
    assert all(0 <= n.x and n.x + n.w <= box.w + 0.01 for n in box.nodes)
