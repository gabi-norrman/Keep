"""Builds the Keep Atlas: docs/atlas/out/index.html, a zoomable map of the source tree with one sentence per package,
module and class and the rest of the docstring behind a "More info" link. Standard library only, so CI runs it without the venv.

    python3 docs/atlas/build.py [--out PATH]

A description is the first sentence of the module's docstring (its main class's, when it has one), unless
descriptions.py overrides it. Exits 1 when descriptions.py names something that no longer exists or a module has no
description at all; `problems()` is the same check for the unit test."""
import argparse
import ast
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

from descriptions import CLASSES, GROUPS, MODULES, PROMPT_DESCRIPTION, SCRIPTS, TOUR  # noqa: E402

# The top-level groups in drawing order, each with the colour token of template.html its subtree is drawn in.
TOP_HUES = {"launcher": "slate", "src/interfaces": "teal", "src/assistant": "amber", "src/integrations": "lime",
            "src/common": "violet", "res": "rose"}
MAX_SENTENCE = 230


SENTENCE_END = re.compile(r"(?<=[^\s.])\.(\s+(?=[A-Z`(])|$)")


def _without_plan_references(text: str) -> str:
    text = re.sub(r"\s*\((?:see )?plans/[^)]*\)", "", text)
    return re.sub(r"\s*\([A-Z_]+_PLAN[^)]*\)", "", text)


def _sentence_end(text: str) -> re.Match | None:
    m = SENTENCE_END.search(text)
    return m if m and not text[:m.start()].endswith(("e.g", "i.e", "etc")) else None


def first_sentence(text: str) -> str:
    text = " ".join(_without_plan_references(text).split())
    m = _sentence_end(text)
    if m:
        text = text[:m.start() + 1]
    if len(text) > MAX_SENTENCE:
        cut = text[:MAX_SENTENCE]
        for sep in (" — ", "; ", ", "):
            i = cut.rfind(sep)
            if i > 80:
                text = cut[:i] + "."
                break
        else:
            text = cut.rstrip() + "…"
    if text and not text.endswith((".", "…", "?")):
        text += "."
    return text


def more_info(doc: str, desc: str) -> str:
    """What the docstring says beyond the description: the rest of it when the description is its first sentence,
    all of it when the description was shortened or comes from descriptions.py."""
    doc = _without_plan_references(doc or "").strip()
    m = _sentence_end(doc)
    head = doc[:m.start() + 1] if m else doc
    if " ".join(head.split()) == desc.rstrip("."):
        head = head + "."
    if " ".join(head.split()) == desc:
        return doc[m.end():].strip("\n") if m else ""
    return doc


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _is_exception(cls: ast.ClassDef) -> bool:
    bases = [ast.unparse(b) for b in cls.bases]
    return cls.name.endswith(("Error", "Exception")) or any("Error" in b or "Exception" in b for b in bases)


def _module(path: Path) -> dict:
    rel = path.relative_to(REPO).as_posix()
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    classes = []
    for n in tree.body:
        doc = isinstance(n, ast.ClassDef) and ast.get_docstring(n)
        if doc and not n.name.startswith("_") and not _is_exception(n):
            cid = f"{rel}::{n.name}"
            classes.append({"id": cid, "name": n.name, "kind": "class", "path": rel,
                            "lines": max(n.end_lineno - n.lineno + 1, 5),
                            "desc": CLASSES.get(cid) or first_sentence(doc), "doc": doc,
                            "bases": [ast.unparse(b) for b in n.bases]})
    main = next((c for c in classes if c["name"] == path.stem), None)
    desc = (MODULES.get(rel) or (main and main["desc"]) or first_sentence(ast.get_docstring(tree) or "")
            or (classes[0]["desc"] if classes else ""))
    name = path.name if path.name != "__init__.py" else f"{path.parent.name}/__init__.py"
    doc = (main and main["doc"]) or ast.get_docstring(tree) or (classes[0]["doc"] if classes else "")
    node = {"id": rel, "name": name, "kind": "module", "path": rel, "lines": len(source.splitlines()), "desc": desc,
            "more": more_info(doc, desc)}
    for c in classes:
        c["more"] = more_info(c.pop("doc"), c["desc"])
    if main:
        node["bases"] = main["bases"]
    if len(classes) >= 2:
        node["children"] = classes
    return node


def _group(key: str) -> dict:
    name, desc = GROUPS.get(key, (Path(key).name, ""))
    return {"id": key, "name": name, "kind": "package", "path": key + "/", "desc": desc, "children": []}


def _front_matter(path: Path) -> dict:
    """The `key: value` lines of a folder toolkit's front matter (strings and flat lists — all it
    holds), read without a YAML library."""
    meta = {}
    lines = path.read_text(encoding="utf-8").split("\n")
    for line in lines[1:lines.index("---", 1)] if lines[:1] == ["---"] and "---" in lines[1:] else []:
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith("["):
            meta[key.strip()] = [v.strip().strip("\"'") for v in value.strip("[]").split(",") if v.strip()]
        else:
            meta[key.strip()] = json.loads(value) if value.startswith('"') else value
    return meta


def _toolkits() -> list[tuple[str, str, list[str], str]]:
    """(name, summary, commands, file) per toolkit, read without importing the assistant: the
    Toolkit(...) calls in Toolkits.py, whose files are in res/toolkits, and the toolkit.md an
    integration folder owns."""
    tree = ast.parse((REPO / "src/assistant/commands/Toolkits.py").read_text(encoding="utf-8"))
    out = []
    for call in ast.walk(tree):
        if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "Toolkit" and len(call.args) >= 3 \
                and all(isinstance(a, (ast.Constant, ast.Tuple)) for a in call.args[:3]):
            name, summary, commands = (ast.literal_eval(a) for a in call.args[:3])
            out.append((name, summary, list(commands), f"res/toolkits/{name}.md"))
    for path in sorted((REPO / "src/integrations").glob("*/toolkit.md")):
        meta = _front_matter(path)
        out.append((meta["name"], meta["summary"], meta["commands"], path.relative_to(REPO).as_posix()))
    return out


def build_tree() -> dict:
    root = _group("root")
    root.update(kind="system", path="")
    groups: dict[str, dict] = {}

    def group_for(directory: str) -> dict:
        if directory not in groups:
            groups[directory] = _group(directory)
            parent = str(Path(directory).parent)
            (root if parent == "src" else group_for(parent))["children"].append(groups[directory])
        return groups[directory]

    for path in sorted((REPO / "src").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if "__pycache__" in rel or (path.name == "__init__.py" and rel not in MODULES):
            continue
        group_for(Path(rel).parent.as_posix())["children"].append(_module(path))

    launcher = _group("launcher")
    launcher["path"] = "./"
    for name, desc in SCRIPTS.items():
        launcher["children"].append({"id": name, "name": Path(name).name, "kind": "script", "path": name,
                                     "lines": _line_count(REPO / name), "desc": desc})
    res = _group("res")
    res["children"].append({"id": "res/prompt.md", "name": "prompt.md", "kind": "resource", "path": "res/prompt.md",
                            "lines": _line_count(REPO / "res/prompt.md"), "desc": PROMPT_DESCRIPTION})
    toolkits = _group("res/toolkits")
    res["children"].append(toolkits)
    for name, summary, commands, rel in _toolkits():
        toolkits["children"].append({"id": rel, "name": f"{name}.md", "kind": "resource", "path": rel,
                                     "lines": _line_count(REPO / rel), "desc": first_sentence(summary[0].upper() + summary[1:]),
                                     "commands": commands})
    root["children"] += [launcher, res]
    for group in root["children"]:
        group["hue"] = TOP_HUES[group["id"]]
    root["children"].sort(key=lambda g: list(TOP_HUES).index(g["id"]))

    def sort(node):
        if node["kind"] in ("package", "system"):
            node["children"].sort(key=lambda c: (c["kind"] != "package", c["name"].lower()))
            for c in node["children"]:
                sort(c)
    sort(root)
    return root


def _nodes(node: dict):
    yield node
    for c in node.get("children", []):
        yield from _nodes(c)


def problems(tree: dict) -> list[str]:
    """What makes the atlas wrong: descriptions.py naming what no longer exists, a module with nothing to say,
    a top-level group drawn in a colour the template does not define (the browser paints it black)."""
    nodes = {n["id"]: n for n in _nodes(tree)}
    dirs = {Path(n["id"]).parent.as_posix() for n in nodes.values() if n["kind"] == "module"}
    found = []
    for table, keys in (("MODULES", MODULES), ("CLASSES", CLASSES), ("SCRIPTS", SCRIPTS)):
        found += [f"descriptions.{table} names {k}, which no longer exists" for k in keys if k not in nodes]
    found += [f"descriptions.GROUPS names {k}, which holds no modules" for k in GROUPS
              if k.startswith("src/") and not any(d == k or d.startswith(k + "/") for d in dirs)]
    found += [f"descriptions.TOUR step {i + 1} names {k}, which no longer exists" for i, (k, _) in enumerate(TOUR)
              if k not in nodes]
    found += [f"{n['id']} has no description: add a descriptions.GROUPS entry" if n["kind"] == "package" else
              f"{n['id']} has no description: give it a docstring or a descriptions.MODULES entry"
              for n in nodes.values() if not n["desc"]]
    template = (HERE / "template.html").read_text(encoding="utf-8")
    found += [f"{g['id']} is drawn in --{g.get('hue')}, which template.html does not define"
              for g in tree.get("children", []) if f"--{g.get('hue')}:" not in template]
    return found


def render(tree: dict) -> str:
    def embed(value) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    template = (HERE / "template.html").read_text(encoding="utf-8")
    return template.replace("__DATA__", embed(tree)).replace("__TOUR__", embed(TOUR))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=HERE / "out" / "index.html")
    args = parser.parse_args()
    tree = build_tree()
    found = problems(tree)
    for p in found:
        print(f"atlas: {p}", file=sys.stderr)
    if found:
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(tree), encoding="utf-8")
    count = sum(1 for n in _nodes(tree) if n["kind"] == "module")
    print(f"atlas: {count} modules → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
