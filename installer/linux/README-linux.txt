Desktop Widgets for Linux
=========================

Three small desktop widgets: To-Do Widget, Usage Monitor and Desktop Monitor.

Install (per user, no sudo):
    ./install.sh            # asks which widgets you want
    ./install.sh --yes      # all three, no questions
    ./install.sh --help     # every option

Start them from your application menu ("Desktop Widgets ...") or run
desktopwidgets-todo, desktopwidgets-usage or desktopwidgets-desktop.

Uninstall:
    ~/.local/share/DesktopWidgets/uninstall.sh
It asks before it deletes your settings in ~/.config/DesktopWidgets.

Notes
- API keys are saved in your key store (Secret Service, for example gnome-keyring).
  Without one the apps say so and refuse to save a key.
- GNOME shows tray icons only with the AppIndicator extension. Without it the widgets
  still work; open Settings from the card (right-click the header) or start the
  app with --settings.
- Wayland does not let apps stay below other windows or snap to screen edges, so the
  widgets quietly skip that.
