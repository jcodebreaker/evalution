"""Where an app keeps what it remembers, found before any test runs (prototype).

:mod:`.discover` reads the project folder with no model and no app running: the packages, env
names, compose services and literals in code that say "a store lives here". :mod:`.observe`
watches the running app while one probe message carrying a canary goes through it: which files
under the folders it uses changed, which ones now hold the canary, and which database sockets it
holds open. :func:`.observe.reconcile` lines the two up, so a store the code never named but the
app wrote to shows as undeclared, and one named but never touched as unconfirmed.
"""
