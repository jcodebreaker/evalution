"""Who is asking, and in which workspace (docs/hosting.md).

Hosted, people sign in with OIDC and every request runs in one workspace they belong to; a CLI or
CI call carries a workspace API token instead. Local, there is no login: one workspace,
:data:`evalution.storage.database.LOCAL`, and no user.
"""
