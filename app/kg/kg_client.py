"""Knowledge-graph client: Oxigraph over HTTP, with an in-memory rdflib fallback.

Both backends expose the same interface so agents and tests never care which one runs:

    health() -> bool
    load_turtle(ttl_or_path, graph_uri=DATA_GRAPH) -> int      # triples added
    load_graph(rdflib_graph, graph_uri=DATA_GRAPH) -> int
    sparql_select(query) -> list[dict]                          # python values
    sparql_ask(query) -> bool
    construct(query) -> rdflib.Graph
    export_graph(graph_uri=None) -> rdflib.Graph                # None = everything visible
    sparql_update(update) -> None
    clear_graph(graph_uri=DATA_GRAPH) -> None
    triple_count(graph_uri=None) -> int

Environment:
    KG_URL            Oxigraph base URL (default http://kg:7878)
    KG_BACKEND        "auto" (default: HTTP if reachable, else memory), "http", "memory"
    KG_QUERY_GRAPHS   extra comma-separated graph IRIs added to the default dataset of HTTP queries
    KG_UNION_DEFAULT  "1" if the server runs with --union-default-graph (no dataset params are sent)

Graph layout: ontology in ONTOLOGY_GRAPH, instance data in DATA_GRAPH. Queries see both as one
default graph (rdflib Dataset with default_union; Oxigraph via the SPARQL `default-graph-uri`
protocol parameter, or --union-default-graph).
"""
from __future__ import annotations

import logging
import os
import threading
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from rdflib import Dataset, Graph, Literal, URIRef
from rdflib.query import Result

log = logging.getLogger("veridiplo.kg")

ONTOLOGY_GRAPH = "https://veridiplo.org/graph/ontology"
DATA_GRAPH = "https://veridiplo.org/graph/data"
DEFAULT_KG_URL = "http://kg:7878"

_HERE = Path(__file__).resolve().parent
ONTOLOGY_PATH = _HERE / "ontology" / "veridiplo.ttl"
SHAPES_PATH = _HERE / "shapes" / "veridiplo-shapes.ttl"


def _to_python(term: Any) -> Any:
    """Convert an rdflib term to a plain python value (IRIs become str)."""
    if term is None:
        return None
    if isinstance(term, Literal):
        value = term.toPython()
        if isinstance(value, Decimal):
            return float(value)
        return str(term) if isinstance(value, Literal) else value
    return str(term)


def _rows_from_result(res: Result) -> list[dict[str, Any]]:
    variables = [str(v) for v in res.vars]
    rows = []
    for row in res:
        rows.append({v: _to_python(row[i]) for i, v in enumerate(variables) if row[i] is not None})
    return rows


def _read_ttl(ttl_or_path: str | Path) -> str:
    if isinstance(ttl_or_path, Path):
        return ttl_or_path.read_text(encoding="utf-8")
    text = str(ttl_or_path)
    if "\n" not in text and len(text) < 1024 and Path(text).is_file():
        return Path(text).read_text(encoding="utf-8")
    return text


# --------------------------------------------------------------------------- local backend
class LocalKG:
    """In-memory rdflib backend with the same interface as OxigraphKG."""

    backend = "memory"

    def __init__(self) -> None:
        self.ds = Dataset(default_union=True)
        self._lock = threading.RLock()

    def health(self) -> bool:
        return True

    def _ctx(self, graph_uri: str | None) -> Graph:
        if graph_uri in (None, "default"):
            return getattr(self.ds, "default_graph", None) or self.ds.default_context
        return self.ds.graph(URIRef(graph_uri))

    def load_graph(self, graph: Graph, graph_uri: str | None = DATA_GRAPH) -> int:
        with self._lock:
            ctx = self._ctx(graph_uri)
            before = len(ctx)
            for t in graph:
                ctx.add(t)
            return len(ctx) - before

    def load_turtle(self, ttl_or_path: str | Path, graph_uri: str | None = DATA_GRAPH) -> int:
        g = Graph()
        g.parse(data=_read_ttl(ttl_or_path), format="turtle")
        return self.load_graph(g, graph_uri)

    def sparql_select(self, query: str) -> list[dict[str, Any]]:
        with self._lock:
            return _rows_from_result(self.ds.query(query))

    def sparql_ask(self, query: str) -> bool:
        with self._lock:
            return bool(self.ds.query(query).askAnswer)

    def construct(self, query: str) -> Graph:
        with self._lock:
            res = self.ds.query(query)
            g = Graph()
            for t in res.graph if res.graph is not None else res:
                g.add(t)
            return g

    def export_graph(self, graph_uri: str | None = None) -> Graph:
        with self._lock:
            g = Graph()
            src = self.ds if graph_uri is None else self._ctx(graph_uri)
            for t in src.triples((None, None, None)):
                g.add(t[:3])
            for prefix, ns in self.ds.namespaces():
                g.bind(prefix, ns, override=False)
            return g

    def sparql_update(self, update: str) -> None:
        with self._lock:
            self.ds.update(update)

    def clear_graph(self, graph_uri: str | None = DATA_GRAPH) -> None:
        with self._lock:
            self._ctx(graph_uri).remove((None, None, None))

    def triple_count(self, graph_uri: str | None = None) -> int:
        with self._lock:
            if graph_uri is None:
                return sum(1 for _ in self.ds.triples((None, None, None)))
            return len(self._ctx(graph_uri))


# --------------------------------------------------------------------------- HTTP backend
class OxigraphKG:
    """Oxigraph SPARQL/Graph-Store client (endpoints /store, /query, /update)."""

    backend = "http"

    def __init__(self, base_url: str | None = None, timeout: float = 30.0,
                 query_graphs: Iterable[str] | None = None, union_default: bool | None = None) -> None:
        import requests  # local import: keeps LocalKG usable without requests

        self._requests = requests
        self.base_url = (base_url or os.getenv("KG_URL") or DEFAULT_KG_URL).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        if union_default is None:
            union_default = os.getenv("KG_UNION_DEFAULT", "0") == "1"
        graphs = [ONTOLOGY_GRAPH, DATA_GRAPH]
        graphs += [g.strip() for g in os.getenv("KG_QUERY_GRAPHS", "").split(",") if g.strip()]
        graphs += list(query_graphs or [])
        self.query_graphs: list[str] = [] if union_default else list(dict.fromkeys(graphs))

    # -- helpers
    def _dataset_params(self) -> list[tuple[str, str]]:
        return [("default-graph-uri", g) for g in self.query_graphs]

    @staticmethod
    def _graph_param(graph_uri: str | None) -> dict[str, str]:
        if graph_uri in (None, "default"):
            return {"default": ""}
        return {"graph": graph_uri}

    def _query(self, query: str, accept: str) -> Any:
        r = self.session.post(
            f"{self.base_url}/query",
            params=self._dataset_params(),
            data=query.encode("utf-8"),
            headers={"Content-Type": "application/sparql-query", "Accept": accept},
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"SPARQL query failed ({r.status_code}): {r.text[:300]}")
        return r

    # -- interface
    def health(self) -> bool:
        try:
            return bool(self.sparql_ask("ASK {}"))
        except Exception:  # noqa: BLE001 - any failure means "not healthy"
            return False

    def load_graph(self, graph: Graph, graph_uri: str | None = DATA_GRAPH) -> int:
        return self._post_turtle(graph.serialize(format="turtle"), graph_uri, len(graph))

    def load_turtle(self, ttl_or_path: str | Path, graph_uri: str | None = DATA_GRAPH) -> int:
        text = _read_ttl(ttl_or_path)
        g = Graph()
        g.parse(data=text, format="turtle")  # validate syntax client-side, count triples
        return self._post_turtle(text, graph_uri, len(g))

    def _post_turtle(self, text: str, graph_uri: str | None, n: int) -> int:
        r = self.session.post(
            f"{self.base_url}/store",
            params=self._graph_param(graph_uri),
            data=text.encode("utf-8"),
            headers={"Content-Type": "text/turtle"},
            timeout=max(self.timeout, 120.0),
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Load failed ({r.status_code}): {r.text[:300]}")
        return n

    def sparql_select(self, query: str) -> list[dict[str, Any]]:
        data = self._query(query, "application/sparql-results+json").json()
        rows = []
        for b in data["results"]["bindings"]:
            row = {}
            for var, term in b.items():
                if term["type"] == "uri":
                    row[var] = term["value"]
                elif term["type"] == "bnode":
                    row[var] = "_:" + term["value"]
                else:
                    lit = Literal(term["value"], lang=term.get("xml:lang"),
                                  datatype=URIRef(term["datatype"]) if "datatype" in term else None)
                    row[var] = _to_python(lit)
            rows.append(row)
        return rows

    def sparql_ask(self, query: str) -> bool:
        return bool(self._query(query, "application/sparql-results+json").json()["boolean"])

    def construct(self, query: str) -> Graph:
        r = self._query(query, "text/turtle")
        g = Graph()
        g.parse(data=r.text, format="turtle")
        return g

    def export_graph(self, graph_uri: str | None = None) -> Graph:
        if graph_uri is None:
            return self.construct("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }")
        r = self.session.get(f"{self.base_url}/store", params=self._graph_param(graph_uri),
                             headers={"Accept": "text/turtle"}, timeout=self.timeout)
        if r.status_code == 404:
            return Graph()
        if r.status_code >= 400:
            raise RuntimeError(f"Export failed ({r.status_code}): {r.text[:300]}")
        g = Graph()
        g.parse(data=r.text, format="turtle")
        return g

    def sparql_update(self, update: str) -> None:
        r = self.session.post(f"{self.base_url}/update", data=update.encode("utf-8"),
                              headers={"Content-Type": "application/sparql-update"}, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"SPARQL update failed ({r.status_code}): {r.text[:300]}")

    def clear_graph(self, graph_uri: str | None = DATA_GRAPH) -> None:
        if graph_uri in (None, "default"):
            self.sparql_update("CLEAR DEFAULT")
        else:
            self.sparql_update(f"CLEAR SILENT GRAPH <{graph_uri}>")

    def triple_count(self, graph_uri: str | None = None) -> int:
        if graph_uri is None:
            q = "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }"
        else:
            q = f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH <{graph_uri}> {{ ?s ?p ?o }} }}"
        rows = self.sparql_select(q)
        return int(rows[0]["n"]) if rows else 0


# --------------------------------------------------------------------------- factory
_kg_singleton: LocalKG | OxigraphKG | None = None
_kg_lock = threading.Lock()


def make_kg(backend: str | None = None, url: str | None = None) -> LocalKG | OxigraphKG:
    """Build a new client. backend: auto | http | memory (default from KG_BACKEND)."""
    backend = (backend or os.getenv("KG_BACKEND") or "auto").lower()
    if backend == "memory":
        return LocalKG()
    client = OxigraphKG(url)
    if backend == "http":
        return client
    if client.health():
        return client
    log.warning("KG at %s unreachable - falling back to in-memory rdflib store", client.base_url)
    return LocalKG()


def get_kg() -> LocalKG | OxigraphKG:
    """Process-wide KG client (created on first use)."""
    global _kg_singleton
    with _kg_lock:
        if _kg_singleton is None:
            _kg_singleton = make_kg()
        return _kg_singleton


def set_kg(kg: LocalKG | OxigraphKG | None) -> None:
    """Override (or reset with None) the process-wide client; used by tests."""
    global _kg_singleton
    with _kg_lock:
        _kg_singleton = kg


def load_ontology(kg: LocalKG | OxigraphKG | None = None, replace: bool = True) -> int:
    """Load veridiplo.ttl into ONTOLOGY_GRAPH (idempotent)."""
    kg = kg or get_kg()
    if replace:
        kg.clear_graph(ONTOLOGY_GRAPH)
    return kg.load_turtle(ONTOLOGY_PATH, ONTOLOGY_GRAPH)


if __name__ == "__main__":  # python -m app.kg.kg_client [health|load-ontology|count]
    import sys

    logging.basicConfig(level=logging.INFO)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "health"
    _kg = get_kg()
    if cmd == "load-ontology":
        print(f"{_kg.backend}: loaded {load_ontology(_kg)} ontology triples")
    elif cmd == "count":
        print(f"{_kg.backend}: {_kg.triple_count()} triples")
    else:
        print(f"{_kg.backend}: health={_kg.health()}")
