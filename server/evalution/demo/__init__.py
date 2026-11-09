"""Demos that show a part of evalution working in a minute, on made-up things, spending nothing.

* :mod:`evalution.demo.app`: a made-up assistant and model gateway, with a chat page, run as a
  process (``python -m evalution.demo.app PORT``). The acceptance tests play it too.
* :mod:`evalution.demo.ui`: ``evalution demo ui``, two tests played through that page.
* :mod:`evalution.demo.versions`: ``evalution demo versions``, a branch of a small repository
  tested against ``main``, each version checked out and run by evalution.

Nothing here is imported by the rest of evalution but the CLI (``pyproject.toml``'s layers).
"""
