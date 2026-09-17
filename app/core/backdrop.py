"""Whether the window is drawn on Windows 11's Mica material or on a solid grey.

The decision is its own module because it is the one piece of the 0.26 window
that can be wrong on a machine nobody here can see, and because every input to
it is a fact that has to be read on Windows and then judged the same way
everywhere. The reading happens in `app/ui/winframe.py`; the judging happens
here, where a test can hand it any machine it likes.

Glass is not free, and there are three machines on which it is the wrong
answer rather than a matter of taste:

- **A remote session.** Hyper-V's enhanced session is Remote Desktop, and a
  Remote Desktop session turns transparency off. Mica then paints as a flat
  grey that is *not* the theme's grey, which is worse than asking for the
  theme's grey in the first place. The user runs this application in exactly
  that session, so this is the case the rule exists for.
- **Transparency switched off** in Windows' personalisation settings, for the
  same reason.
- **A build older than 22621**, where `DWMWA_SYSTEMBACKDROP_TYPE` does not
  exist and the call does nothing at all.

"auto" is the default and means "glass where it will actually look like glass".
"glass" and "solid" are the answers somebody gives when auto guessed wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The first Windows build that honours `DWMWA_SYSTEMBACKDROP_TYPE`.
MICA_BUILD = 22621

PREFERENCES = ("auto", "glass", "solid")


@dataclass(frozen=True)
class Machine:
    """What the decision is made from. Every field is a local read."""

    windows: bool = False
    build: int = 0
    remote: bool = False
    transparency: bool = True


def choose(preference: str | None, machine: Machine) -> tuple[str, str]:
    """Return `("glass" | "solid", why)`.

    `why` is a sentence for the View menu, because a setting that silently
    answers something other than what was picked is a setting that looks
    broken. An unknown preference is treated as "auto".
    """
    wanted = preference if preference in PREFERENCES else "auto"
    if not machine.windows:
        return "solid", "the glass backdrop is a Windows 11 feature"
    if machine.build < MICA_BUILD:
        return "solid", "this version of Windows has no glass backdrop"
    if wanted == "solid":
        return "solid", "solid, as chosen"
    if wanted == "glass":
        return "glass", "glass, as chosen"
    if machine.remote:
        return "solid", "solid, because this is a remote session"
    if not machine.transparency:
        return "solid", "solid, because transparency is off in Windows"
    return "glass", "glass"
