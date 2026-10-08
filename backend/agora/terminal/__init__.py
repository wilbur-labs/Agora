"""The embedded terminal (CR-30): an interactive pseudo-terminal per window, local only.

Agora does not re-implement claude-workspace's quality control. It opens a terminal in
the right place — a project's repository, or a claude-workspace task the way ``cw open``
does — and the agent CLI running there brings its own hooks, RESUME, CR log and review
gate with it.
"""
