"""The parameter space: a sum of products.

Two concrete node kinds. A Leaf is a parameter and its values. An Internal is an
ordered list of children plus one combiner strategy, `SUM` or `PRODUCT`, to
which it delegates everything.

Sum children are independent axes. Product children interact.
"""

from abc import ABC, abstractmethod


class Node(ABC):
    """A subtree of the parameter space."""

    name: str

    @abstractmethod
    def count(self) -> int:
        """How many variants this subtree contributes. No enumeration."""

    @abstractmethod
    def generate(self) -> list[dict]:
        """The partial configs this subtree produces.

        Each partial names only the parameters this subtree touches.
        """


class Leaf(Node):
    """A parameter name and the set of values to test for it.

    `domain`, when the tree declares one, is the parameter's legal set: booleans
    and mode identifiers, where the strategy fixes what exists. The user may
    then only sweep values drawn from it. Left absent the parameter is open and
    the user supplies whatever values they want to test.
    """

    def __init__(self, name: str, values, domain=None):
        self.name = name
        self.domain = None if domain is None else list(domain)
        values = list(values)
        if self.domain is not None:
            outside = [value for value in values if value not in self.domain]
            if outside:
                raise ValueError(
                    f"{name}: {outside} not in declared domain {self.domain}"
                )
        self.values = values

    def count(self) -> int:
        return len(self.values)

    def generate(self) -> list[dict]:
        return [{self.name: value} for value in self.values]

    def __repr__(self) -> str:
        domain = "" if self.domain is None else f", domain={self.domain!r}"
        return f"Leaf({self.name!r}, {self.values!r}{domain})"


class Combiner(ABC):
    """How an Internal node folds its children."""

    @abstractmethod
    def count(self, children: list[Node]) -> int: ...

    @abstractmethod
    def generate(self, children: list[Node]) -> list[dict]: ...


class Sum(Combiner):
    """Independent axes: counts add, partial-lists concatenate."""

    def count(self, children):
        return sum(child.count() for child in children)

    def generate(self, children):
        partials = []
        for child in children:
            partials.extend(child.generate())
        return partials


class Product(Combiner):
    """Interacting parameters: counts multiply, partials cross-multiply.

    Keys are disjoint across product-siblings, so the merges never collide.
    """

    def count(self, children):
        total = 1
        for child in children:
            total *= child.count()
        return total

    def generate(self, children):
        combos = [{}]
        for child in children:
            combos = [{**combo, **partial} for combo in combos for partial in child.generate()]
        return combos


SUM = Sum()
PRODUCT = Product()


class Internal(Node):
    """An ordered list of children folded by one combiner strategy."""

    def __init__(self, name: str, combiner: Combiner, children):
        self.name = name
        self.combiner = combiner
        self.children = list(children)

    def count(self) -> int:
        return self.combiner.count(self.children)

    def generate(self) -> list[dict]:
        return self.combiner.generate(self.children)

    def __repr__(self) -> str:
        kind = type(self.combiner).__name__.lower()
        return f"Internal({self.name!r}, {kind}, {self.children!r})"


def paths(node: Node) -> dict[str, Leaf]:
    """Every leaf under this node, keyed by its path relative to it.

    Once a tree has alternative branches the same parameter appears in many
    places - fifteen leaves may all be called
    `underlying_threshold_hedge_constant` - so a leaf is addressed by where it
    sits, not by what it is named.
    """
    if isinstance(node, Leaf):
        return {node.name: node}
    found: dict[str, Leaf] = {}
    for child in node.children:
        below = paths(child)
        if isinstance(child, Leaf):
            found.update(below)
        else:
            found.update({f"{child.name}/{path}": leaf for path, leaf in below.items()})
    return found


def branches(node: Node) -> list[tuple[str, Node]]:
    """(path, group) for each terminal group of runs under this node.

    A sum is walked through, because its children are separate groups of runs
    that are stored and tagged apart. A product or a leaf is terminal: one grid,
    generated whole.
    """
    if isinstance(node, Internal) and isinstance(node.combiner, Sum):
        found = []
        for child in node.children:
            for sub, terminal in branches(child):
                found.append((f"{child.name}/{sub}" if sub else child.name, terminal))
        return found
    return [("", node)]


def sweep(node: Node, chosen: dict[str, list]) -> Node:
    """A copy of the tree with the user's chosen values substituted into leaves.

    `chosen` is keyed by leaf path, as `paths` reports it. The tree is fixed per
    strategy and defines the universe; this is the user's only say over values,
    and it cannot introduce a parameter the tree does not declare, nor a value
    outside a declared domain.
    """
    unknown = set(chosen) - set(paths(node))
    if unknown:
        raise KeyError(f"not a leaf of this tree: {sorted(unknown)}")
    return _sweep(node, chosen, "")


def _sweep(node: Node, chosen: dict[str, list], prefix: str) -> Node:
    if isinstance(node, Leaf):
        path = f"{prefix}{node.name}"
        return Leaf(node.name, chosen[path], node.domain) if path in chosen else node
    children = []
    for child in node.children:
        below = prefix if isinstance(child, Leaf) else f"{prefix}{child.name}/"
        children.append(_sweep(child, chosen, below))
    return Internal(node.name, node.combiner, children)


def alternatives(node: Node) -> list[Node] | None:
    """The branches of an axis that is a choice between structures, else None.

    An axis built as a sum holds separate groups of runs, stored and tagged
    apart. An axis built as a product is a single interacting grid. Callers that
    display a tree need to tell the two apart; `count` and `generate` do not.
    """
    if isinstance(node, Internal) and isinstance(node.combiner, Sum):
        return list(node.children)
    return None


def leaves(node: Node) -> list[Leaf]:
    """Every Leaf under this node, in order."""
    if isinstance(node, Leaf):
        return [node]
    return [leaf for child in node.children for leaf in leaves(child)]
