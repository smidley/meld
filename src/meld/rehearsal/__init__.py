"""The dress rehearsal (plan 48): the whole of meld end to end on one machine, with no network and no money.

A stand-in GitHub served over loopback (`github.FakeGitHub`), a `gh` on PATH that talks to it (`ghshim`), stand-in AI
CLIs that play the roles and workers by policy from `meld status` (`cli`, `actor`). The real wizard, viewer,
supervisors, adapters and every `meld` command run unchanged; the suite drives it in `tests/test_rehearsal.py`.
"""
