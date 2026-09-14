"""Private owner-scoped conversational analysis (ADR-0036).

A local model answers questions by calling the shared analysis tools in
``healthcurve.analysis``. It never receives a database connection, owner identifier,
or mutation operation; model-authored queries run only through view-only analyst roles.
"""
