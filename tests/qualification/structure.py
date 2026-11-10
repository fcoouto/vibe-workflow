"""Static checks over the harness sources, run by tests/test_qualification_harness.py (StructureTests).

Pilot attempt 1 aborted on an instance attribute that shadowed a method (`Run.account`), which no behavioural test
reached. These checks look for that class of defect without running anything:

- `signature_problems`: calls through `self.`, `env.`/`self.env.`, `run.`/`self.run.`, `self.server.`,
  `self.routing.`/`run.routing.` and `self.ledger.` must name a method of the target harness class with a compatible
  signature (or one of its instance attributes);
- `undefined_names`: names loaded but bound nowhere in the module (a coarse pass for obvious typos; pyflakes is not
  installed here).
"""
import ast
import builtins
from pathlib import Path

# Receiver spelling -> the harness class it refers to. Bare names resolve only for `env.` and `run.` (locals named
# `server` or `ledger` are often something else); attributes resolve through `self.<name>.` and `run.<name>.`.
RECEIVERS = {"env": "CanaryEnv", "run": "Run", "server": "Server", "routing": "Routing", "ledger": "CallLedger"}
BARE = ("env", "run")
# The live-session modes get their environment from live_modes.py: their `env.` calls are checked against it.
MODULE_RECEIVERS = {"launcher_probes.py": dict(env="LiveEnv"), "drill.py": dict(env="LiveEnv"),
                    "gateway.py": dict(env="GatewayEnv")}


def _signature(function):
    decorators = {getattr(d, "id", getattr(d, "attr", None)) for d in function.decorator_list}
    if "property" in decorators:
        return None
    args = function.args
    positional = [a.arg for a in args.posonlyargs + args.args]
    if "staticmethod" not in decorators and positional:
        positional = positional[1:]  # self or cls
    return dict(positional=positional, defaults=len(args.defaults), vararg=args.vararg is not None,
                kwarg=args.kwarg is not None,
                kwonly=[(a.arg, default is not None) for a, default in zip(args.kwonlyargs, args.kw_defaults)])


def _classes(trees):
    found = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = {item.name: _signature(item) for item in node.body
                           if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}
                attributes = set()
                for item in ast.walk(node):
                    targets = []
                    if isinstance(item, ast.Assign):
                        targets = item.targets
                    elif isinstance(item, (ast.AnnAssign, ast.AugAssign)):
                        targets = [item.target]
                    for target in targets:
                        for leaf in ast.walk(target):
                            if isinstance(leaf, ast.Attribute) and isinstance(leaf.value, ast.Name) and leaf.value.id == "self":
                                attributes.add(leaf.attr)
                attributes |= {item.targets[0].id for item in node.body
                               if isinstance(item, ast.Assign) and isinstance(item.targets[0], ast.Name)}
                found[node.name] = dict(methods=methods, attributes=attributes, bases=bool(node.bases),
                                        base_names=[b.id for b in node.bases if isinstance(b, ast.Name)])
    for info in found.values():  # inherited methods and attributes from bases parsed here (one level is enough)
        for base in info["base_names"]:
            if base in found:
                info["methods"] = dict(found[base]["methods"], **info["methods"])
                info["attributes"] |= found[base]["attributes"]
    return found


def _own_nodes(node):
    """The nodes of a class without those of classes nested in it (they are checked as their own class)."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        item = stack.pop()
        yield item
        if not isinstance(item, ast.ClassDef):
            stack.extend(ast.iter_child_nodes(item))


def _check(signature, call):
    if any(isinstance(arg, ast.Starred) for arg in call.args) or any(kw.arg is None for kw in call.keywords):
        return None  # *args / **kwargs: not checkable statically
    positional, names = len(call.args), [kw.arg for kw in call.keywords]
    params, kwonly = signature["positional"], dict(signature["kwonly"])
    if positional > len(params) and not signature["vararg"]:
        return "too many positional arguments"
    unknown = [name for name in names if name not in params and name not in kwonly]
    if unknown and not signature["kwarg"]:
        return "unknown keyword %s" % unknown[0]
    required = params[:len(params) - signature["defaults"]]
    missing = [name for name in required[positional:] if name not in names]
    missing += [name for name, has_default in kwonly.items() if not has_default and name not in names]
    return "missing argument %s" % missing[0] if missing else None


def _target(call_func, enclosing, receivers=RECEIVERS):
    """(class name, method) a call refers to, or None when the receiver is not one we resolve."""
    receiver = call_func.value
    if isinstance(receiver, ast.Name):
        if receiver.id == "self":
            return enclosing, call_func.attr, True
        if receiver.id in BARE:
            return receivers[receiver.id], call_func.attr, False
    if isinstance(receiver, ast.Attribute) and receiver.attr in receivers:
        base = receiver.value
        if isinstance(base, ast.Name) and base.id in ("self", "run"):
            return receivers[receiver.attr], call_func.attr, False
    return None


def signature_problems(files):
    trees = {Path(path).name: ast.parse(Path(path).read_text(), str(path)) for path in files}
    classes = _classes(trees)
    problems = []
    for name, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            receivers = dict(RECEIVERS, **MODULE_RECEIVERS.get(name, {}))
            for call in _own_nodes(node):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                    continue
                resolved = _target(call.func, node.name, receivers)
                if resolved is None or resolved[0] not in classes:
                    continue
                target, method, on_self = resolved
                info = classes[target]
                if method in info["methods"]:
                    signature = info["methods"][method]
                    problem = None if signature is None else _check(signature, call)
                elif method in info["attributes"] or (on_self and info["bases"]):
                    problem = None  # a callable attribute, or something an unparsed base class may provide
                else:
                    problem = "no such method"
                if problem:
                    problems.append("%s:%d %s.%s: %s" % (name, call.lineno, target, method, problem))
    return problems


def undefined_names(files):
    known = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
    found = []
    for path in files:
        tree = ast.parse(Path(path).read_text(), str(path))
        bound = set(known)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                bound |= {(alias.asname or alias.name).split(".")[0] for alias in node.names}
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, ast.arg):
                bound.add(node.arg)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                bound |= set(node.names)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in bound:
                found.append("%s:%d %s" % (Path(path).name, node.lineno, node.id))
    return found
