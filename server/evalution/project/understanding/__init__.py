"""What evalution understood of a target (docs/architecture.md, "The understanding").

The target's ``understanding.json`` document is the source of truth; its profile is rendered
from it (:func:`render_profile`), for people and for the agent's prompt.

* **The agent writes** the one line, the diagram's nodes (each with the tools it stands for),
  the claims with their evidence, the priorities and starters, a ``tool -> step`` mapping and
  the paths it expects from the code (:func:`check` refuses claims or nodes without evidence,
  and tools neither offered in the traces nor in the code file a node cites).
* **The engine computes** from the trace digest at write time (:func:`build`): the tool and
  per-ask numbers, the diagram's edges (asks per node, through each node's tools), the nodes
  seen, and the common paths; and on every read (:func:`view`): the stack, the tests and
  library numbers, the state kinds, what is watched and the drift status.
* **The admin steers** (:func:`steer`): a claim or node marked right or wrong or fixed, a
  priority picked or added, a starter selected. Steers are kept in ``steers.json`` and laid
  over the understanding on read. A verdict or fix holds while the agent leaves that claim (or
  node title) as it was; once the agent rewrites it, the fix is taken as honoured. A fix or
  wrong shows in the rendered profile at once, as an ``[admin]`` line.

Every write is kept in ``understanding_versions.json``, the newest ``store.VERSIONS``.

The parts: :mod:`.document` what the agent writes and its checks, :mod:`.computed` what the engine
computes, :mod:`.steering` the admin's steers and the overlay, :mod:`.rendering` the profile and
the card as text, :mod:`.store` the writes (each one renders the profile again).
"""

from evalution.project.understanding.computed import NO_TOOLS, build, existing, paths, view
from evalution.project.understanding.document import (
    DETAILS,
    EVIDENCE_KINDS,
    NODE_KINDS,
    SECTION_IDS,
    check,
    clean_text,
    details_of,
    parts,
    patched,
    read_evidence,
    with_details,
)
from evalution.project.understanding.rendering import card_md, render_profile
from evalution.project.understanding.steering import (
    KNOWLEDGE_DOC,
    admin_priorities,
    marks,
    overlay,
    steers,
)
from evalution.project.understanding.store import (
    load,
    save,
    steer,
    versions,
    writer_profile,
    written,
)

__all__ = [
    "DETAILS",
    "EVIDENCE_KINDS",
    "KNOWLEDGE_DOC",
    "NODE_KINDS",
    "NO_TOOLS",
    "SECTION_IDS",
    "admin_priorities",
    "build",
    "card_md",
    "check",
    "clean_text",
    "details_of",
    "existing",
    "load",
    "marks",
    "overlay",
    "parts",
    "patched",
    "paths",
    "read_evidence",
    "render_profile",
    "save",
    "steer",
    "steers",
    "versions",
    "view",
    "with_details",
    "writer_profile",
    "written",
]
